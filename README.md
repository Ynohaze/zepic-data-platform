# Zepic Data Platform

DE flagship — local PostgreSQL with order, production and fulfillment simulation.

## Phase 1.5 production and fulfillment

After generating a day's orders, preview then apply its production cycle:

```sh
.venv/bin/python scripts/run_production_planning.py --date 2026-01-03
.venv/bin/python scripts/run_production_planning.py --date 2026-01-03 --apply
```

The example is for a fresh simulation at that date. This checkout has already
completed January 3: 14 production orders/runs, 3,200 units produced, 148 orders
shipped and 82 still pending. Next run daily operations for January 4, then
production for January 4. Do not replay the January 3 apply command.

Rules (`config/production.json`, overridable with `--config`):

- At 18:00 UTC, aggregate only confirmed demand by product, subtract current
  inventory and create one production order per positive shortage. Planned
  quantity is `ceil(shortage * (1 + buffer_rate) / batch_multiple) * batch_multiple`.
  Default buffer is 5%; snapshot it and the product batch size on the production order.
- Available machines with no active maintenance are deterministically shuffled
  using seed + date, then assigned in round-robin waves. Each run lasts 10 simulated
  minutes by default, independent of quantity: this is an MVP timing assumption,
  not a throughput/capacity model. First runs start at 18:01. A machine may execute
  several successive runs, but its recorded intervals never overlap.
- One run per production order; all runs succeed with actual quantity equal to
  planned quantity. Orders go planned → released → completed, runs go running →
  completed, and output enters inventory. Machine availability is not changed.
- One minute after the last run (18:01 if none), try confirmed sales orders oldest
  first, breaking ties by order_id. Ship only if every item is available. Otherwise
  consume nothing for that order and continue trying the others. Deduction and
  the shipped header update share the same transaction; items/prices stay unchanged.
- Reject missing inventory, inactive products requiring production, shortages
  without machines, unfinished prior production, dates other than the latest
  generated order date, future source timestamps or schedules crossing midnight.
  Existing stock can be shipped without any available machines.
- Preview is read-only; apply locks the relevant tables and commits the whole
  cycle atomically. Intermediate planned/released/running states are executed
  inside the transaction, so outside readers see the committed final states only.
  These are state tables, not an event history or CDC stream.
- Existing production orders or shipment timestamps reject repeat execution.
  An empty-demand no-op writes no marker and may be safely repeated. Guards
  assume retained simulation records, not manually deleted or edited history.
- Random failures, yield loss, maintenance generation, cancellation and partial
  shipments remain out of scope. Tests exercise insufficient stock and forced
  database failures; they do not add random failed-run generation.

The existing test commands below include production coverage. Database tests
also verify rollback, no double production under concurrency, preview without
sequence changes, nonoverlapping machine runs and per-product inventory balance.

## Phase 1.4 daily orders

After Phase 1.3 initialization, preview then apply each UTC day in order:

```sh
.venv/bin/python scripts/generate_daily_operations.py --date 2026-01-02
.venv/bin/python scripts/generate_daily_operations.py --date 2026-01-02 --apply
.venv/bin/python scripts/generate_daily_operations.py --date 2026-01-03 --apply
```

These are fresh-database examples. This local checkout has already applied
January 2 and 3; its next order-generation date is January 4. Existing dates
are rejected rather than appended again. Run the production cycle after each
day's orders; the multi-day orchestrator belongs to Phase 1.6.

Configuration: `config/daily_operations.json` (`--config` accepts another path).

- 50–150 orders/day; each uniformly selects a customer and 1–5 distinct active
  products, with 1–20 units per item. Sales quantities need not be production
  batch multiples. The catalog price becomes the order-item price snapshot;
  `total_amount` is the Decimal sum of its item amounts.
- Older pending orders are confirmed at 08:00 UTC on the next simulation day.
  New pending orders are timestamped between 09:00 inclusive and 17:00 exclusive.
  Confirmation changes only the order header's status and updated_at, not items.
- First date defaults to January 2, 2026, after the master-data origin. Change
  `first_date` before the first run if using another origin. Subsequent runs
  must be the calendar day immediately after the most recent order date.
- Seed + date determines each day's random stream. Reproduction also requires
  identical config, source rows, code and Python version. Preview reads current
  database data in a read-only snapshot; it changes neither rows nor sequences.
  A later apply can differ if source data changes after preview.
- One transaction confirms previous orders and inserts the new day. Source
  table locks freeze prices/customer IDs; order table locks serialize the date
  check with inserts. Failure rolls back all row changes, including confirmations.
  Failed inserts can still consume identity sequence values.
- Duplicate, skipped and backward dates are rejected. This is a bootstrap
  simulator guard, not pipeline UPSERT/idempotency. It relies on keeping generated
  orders and having at least one order per day; manual deletion of a whole day's
  orders removes that evidence. No new run-history table was added.
- No cancellation, shipment, inventory deduction, production or maintenance
  is performed in this phase. Confirmed demand waits for Phase 1.5.

Tests (database suite creates and removes a uniquely named temporary database;
the configured PostgreSQL user needs permission to create databases):

```sh
.venv/bin/python -m unittest discover -s tests -v
ZEPIC_DB_TESTS=1 .venv/bin/python -m unittest discover -s tests -v
```

## Phase 1.3 initial data

Use Python 3.10+ and run from the repository root:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python scripts/generate_initial_data.py
docker compose up -d --wait
.venv/bin/python scripts/generate_initial_data.py --apply
```

The first invocation previews data without connecting to PostgreSQL. `--apply`
loads `.env` (existing environment variables take precedence), checks all nine
tables are empty and inserts all four initial datasets in one transaction.
Repeated initialization is rejected without changing existing data. There is
no automatic reset or overwrite. Database-assigned IDs need not start at 1.

Edit `config/initial_data.json`, or pass `--config /path/to/config.json`:

- Default: 100 synthetic customers (70 standard, 20 silver, 10 gold), 20 active
  products, 5 available machines and one inventory row per product.
- Product names, prices and batch sizes are illustrative industrial-part data,
  not market prices. Prices are decimal strings, never floating-point values.
- Seed 42 shuffles customer tiers. Seed + 1 independently chooses inventory
  from 0, 1, 3 or 5 product batches, with equal probability per choice.
- Start time defaults to `2026-01-01T00:00:00+00:00`; all initial created_at and
  updated_at values use this instant. The date is a configurable simulation origin.
- Reproduction assumes unchanged configuration, product order, generator code
  and Python version. Business values are repeatable; database IDs are not fixed.
- Orders, production and maintenance records remain empty for later phases.

Run generator tests with `.venv/bin/python -m unittest discover -s tests -v`.
The Phase 1.2 `database/verify.sql` is for a separate disposable empty database;
do not run it on this initialized dataset.

## Start

Requires Docker Desktop (or Docker Engine) running with Compose support.
Run commands from this repository root. A local `.env` has been created for
this checkout; for a fresh clone, copy `.env.example` to `.env` and replace
the example password first. `.env` is ignored by Git.

```sh
docker compose config --quiet
docker compose up -d --wait
docker compose ps
docker compose exec postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "\dt"'
```

PostgreSQL 16 listens on localhost only. `POSTGRES_PORT` changes the host port;
the container port stays 5432. `POSTGRES_HOST` is for future host-side Python
clients; another Compose service would use hostname `postgres`.

On a Mac where Docker Desktop is installed but the Compose plugin is not on
the CLI search path, replace `docker compose` with:

```sh
/Applications/Docker.app/Contents/Resources/cli-plugins/docker-compose
```

## Files

- `docker-compose.yml`: PostgreSQL service, healthcheck and persistent volume.
- `database/schema.sql`: agreed 9-table schema, indexes and triggers.
- `database/verify.sql`: SQL smoke checks for a disposable initialized database.
- `config/`: initial master-data and daily-order parameters.
- `scripts/`: initial-data and daily-operation generators.
- `tests/`: generation tests and optional isolated database tests.
- `src/`: reserved for shared utilities when needed.
- `PROJECT_HANDOFF.md`: locked business decisions and phase boundary.

## Verify

Run on a disposable initialized database. Test rows roll back, but PostgreSQL
identity sequence increments do not roll back. Do not use on populated data:
some negative test statements deliberately target whole tables.

```sh
docker compose exec -T postgres sh -c 'psql -X -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < database/verify.sql
```

Expected: 9 tables, 32 indexes (including PK/UNIQUE indexes), 10 triggers,
and a final `PASS` message. A healthcheck alone does not verify the schema.

## Persistence and schema changes

`docker compose down` removes the container but retains database data.
The initialization SQL runs only when the data volume is empty. Editing
`schema.sql` or changing initialization credentials in `.env` does not modify
an existing database. SQL initialization is transactional and fails on errors.

Only when intentionally discarding **all local database data**, reset with
`docker compose down -v`, followed by `docker compose up -d --wait`.
An initialization failure may also require this reset for a disposable volume.
Use migrations once data must be preserved.
