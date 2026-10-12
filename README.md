# Zepic Data Platform

DE flagship — local PostgreSQL with order, production and fulfillment simulation.

## Phase 4 warehouse and dbt

The dbt project in `warehouse/` reads only Phase 3's validated raw view and builds
typed current-state models in `zepic_staging` and analytics-ready tables in
`zepic_analytics`. It contains all nine source staging models, customer/product/
machine dimensions, order-line/production/maintenance facts, current inventory,
and daily order metrics.

```sh
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python scripts/validate_raw.py
.venv/bin/python scripts/reconcile_order_totals.py
.venv/bin/python scripts/run_warehouse.py build
```

The wrapper loads the ignored root `.env`; `warehouse/profiles.yml` contains only
environment-variable references. dbt tests enforce keys, relationships, domains,
inventory nonnegativity, production terminal fields, and order total agreement.
See `warehouse/README.md` for model grains and current-state limitations.

## Phase 1.6 multi-day simulation

Preview a date range, then apply it (30 days includes January 2 through January 31):

```sh
.venv/bin/python scripts/run_simulation.py --start-date 2026-01-02 --days 30 --resume
.venv/bin/python scripts/run_simulation.py --start-date 2026-01-02 --days 30 --resume --apply
```

That original Phase 1.6 checkpoint had 3,137 orders, 9,306 items, 550 production
orders/runs, 3,018 shipped orders and 119 pending orders. Phase 2 later advanced
the local simulation by one day; its current counts are recorded below.

- `--days` accepts 1–366, default 30. Config overrides: `--daily-config` and
  `--production-config`. They default to the existing Phase 1.4/1.5 JSON files.
- Preview reads a consistent database snapshot and shows dates/actions only;
  it does not forecast future generated quantities or consume identity values.
- Each new day has one outer transaction: confirmation/order generation, then
  planning/production/shipping. Nested helpers use savepoints. Commit only when
  the whole day succeeds. Failure rolls back that day, preserving prior commits.
  Identity sequence gaps after a failed write remain normal PostgreSQL behavior.
- Each committed day prints a JSON summary. After resolving a failure, repeat
  the same range with `--resume`; completed dates are skipped without duplicates.
  Without `--resume`, overlapping existing dates are rejected.
- Resume can finish the latest standalone order day if its confirmed orders
  still await production. Include that date in the requested range. It does not
  replay old order generation or overwrite the original production schedule.
- Resume infers progress from contiguous retained orders, no older pending
  orders, no unfinished production and the all-success shipping rule. It is not
  a general job ledger. Manually edited/deleted history, mixed configs or future
  failure/backlog models need a stronger checkpoint design. Keep configs fixed
  while continuing the same simulation. A no-demand first day is a valid no-op.
- All operational tables are locked in a consistent order for each apply day;
  locks are released after each day's commit. Competing runners recheck progress
  under the locks. This is deliberately a small local simulator, not a live ERP.
- Business times remain 08:00 confirmation, 09:00 planning, 16:00 shipping;
  new orders between 09:00–17:00 remain pending until the next day. Future pending
  timestamps are excluded from production inputs. January 3 retains its earlier
  validated 18:00 schedule; January 4 onward uses the revised working-day schedule.

Tests include a complete 30-day run, stock conservation, repeat resume, recovery
after a late production/shipping failure, and simultaneous runners. Run them
with the existing `ZEPIC_DB_TESTS=1` command below. This completes the successful
operational simulation path; maintenance/failure generation remains deferred.
The database stores current row states, not every transition. Phase 2 demonstrates
incremental pulls between simulated days; it does not retain intermediate events.

## Phase 2 source API and raw ingestion

The API exposes all nine operational tables through an explicit table allowlist.
It reads only from the operational schema. The API uses a
maximum page size of 1,000 and keyset ordering by `(updated_at, primary key)`.
Money is serialized as decimal strings and timestamps as UTC ISO-8601 values.

For each table, the extractor first reads a fixed upper-bound tuple, then walks
pages strictly after its stored lower cursor and through that upper bound. Pause
the simulator while extracting: the tuple stabilizes pagination, while pausing
writes gives all HTTP pages one consistent source state. This is current-state
extraction, not change-data capture; transitions committed between pulls cannot
be reconstructed. Raw rows retain the complete source payload and are append-only
per extraction batch. The client commits each table's batch rows, batch metadata
and watermark in one transaction. Tables advance independently; failed table
batches roll back and are safe to retry. Empty tables are marked initialized
without inventing a timestamp watermark.

Run the API and extractor in separate terminals:

```sh
.venv/bin/uvicorn src.source_api:app --host 127.0.0.1 --port 8000
.venv/bin/python scripts/extract_to_raw.py --table all
```

The extractor creates `zepic_raw` objects from `database/raw_schema.sql` on
first use. Repeat the extraction after advancing one simulation day to see
inserts and updates. Keep the API bound to localhost for this local MVP; network
authentication and concurrent source writes are outside this slice.

The Jan 31 baseline held 3,137 orders, 9,306 items and 20 inventory rows.
The Feb 1 pull captured 97 new orders, 119 updated orders, 309 new items and
20 updated inventory rows. Full baselines for the remaining tables followed;
maintenance was empty. The Feb 2 pull captured 108 new orders, 97 order updates,
335 new items, 20 new production orders, 20 new runs and 20 inventory updates.
The operational database now has 3,342 orders, 9,950 items, 590 completed
production orders/runs, 3,234 shipped orders, 108 pending orders and 700
inventory units. Raw contains 14,873 rows across nine source tables. Next
simulation date: February 3, 2026.

## Phase 3 raw quality checks

`scripts/validate_raw.py` applies versioned row-level rules to landed batches:
source key/timestamp consistency, required fields, enum domains, numeric bounds,
status/time combinations and references to landed parent rows. Invalid rows stay
in immutable raw storage; their findings appear through
`zepic_raw.quarantined_records`, while `zepic_raw.validated_records` excludes
those rows for the latest completed ruleset. Validation never changes extraction
watermarks. A row with any finding is quarantined, while the remaining rows in
its batch stay eligible.

Validate every batch not yet processed under rule version `v1`, or target one
batch explicitly:

```sh
.venv/bin/python scripts/validate_raw.py
.venv/bin/python scripts/validate_raw.py --batch-id 1
```

Quality run keys are `(batch_id, rule_version)`. Re-running an existing batch
returns its saved result without adding duplicate findings. Each batch's quality
run and findings commit atomically. The current raw checkpoint validated all 27
batches and 14,873 rows: all passed, with zero quarantine findings. A temporary
database test injected an invalid customer tier and verified that the raw row
and watermark remained unchanged while one finding was quarantined.

The row-level ruleset mirrors local row constraints and checks raw metadata and
landed foreign keys. After all raw batches have been validated, reconcile current
order headers against the latest current-state item rows:

```sh
.venv/bin/python scripts/validate_raw.py
.venv/bin/python scripts/reconcile_order_totals.py
```

The reconciliation uses exact decimal arithmetic for `quantity * unit_price`,
reports orders with missing details separately, and skips an order if its header
or any current detail row was quarantined by row-level validation. Runs and
findings are stored separately in `zepic_raw.order_reconciliation_runs` and
`zepic_raw.order_reconciliation_findings`; the latest result is available through
`zepic_raw.latest_order_reconciliation` and
`zepic_raw.order_reconciliation_issues`. It does not modify raw rows or
watermarks. The same order/item snapshot is idempotent; changed source rows
produce a new reconciliation run.

The current raw checkpoint reconciled all 3,342 latest orders: 3,342 matched,
zero mismatched, zero without details, and zero skipped. The combined unit and
temporary-PostgreSQL suite has 53 passing tests.

Run all 53 tests, including temporary-PostgreSQL integration tests, with:

```sh
ZEPIC_DB_TESTS=1 .venv/bin/python -m unittest discover -s tests -v
```

## Phase 1.5 production and fulfillment

After generating a day's orders, preview then apply its production cycle:

```sh
.venv/bin/python scripts/run_production_planning.py --date 2026-01-03
.venv/bin/python scripts/run_production_planning.py --date 2026-01-03 --apply
```

The example is for a fresh simulation at that date. The Phase 1.5 checkpoint
completed January 3 with 14 production orders/runs, 3,200 units produced,
148 orders shipped and 82 pending. The database has since advanced through
January 31 (see Phase 1.6 above). Do not replay the January 3 apply command.
The January 3 records retain the earlier 18:00/18:32 schedule; new cycles use
09:00 planning and 16:00 shipping. Historical rows were not rewritten.

Rules (`config/production.json`, overridable with `--config`):

- At 09:00 UTC, aggregate only confirmed demand by product, subtract current
  inventory and create one production order per positive shortage. Planned
  quantity is `ceil(shortage * (1 + buffer_rate) / batch_multiple) * batch_multiple`.
  Default buffer is 5%; snapshot it and the product batch size on the production order.
- Available machines with no active maintenance are deterministically shuffled
  using seed + date, then assigned in round-robin waves. Each run lasts 10 simulated
  minutes by default, independent of quantity: this is an MVP timing assumption,
  not a throughput/capacity model. First runs start at 09:01. A machine may execute
  several successive runs, but its recorded intervals never overlap.
- One run per production order; all runs succeed with actual quantity equal to
  planned quantity. Orders go planned → released → completed, runs go running →
  completed, and output enters inventory. Machine availability is not changed.
- At 16:00 UTC (also when no production is needed), try confirmed sales orders oldest
  first, breaking ties by order_id. Ship only if every item is available. Otherwise
  consume nothing for that order and continue trying the others. Deduction and
  the shipped header update share the same transaction; items/prices stay unchanged.
- Reject missing inventory, inactive products requiring production, shortages
  without machines, unfinished prior production, dates other than the latest
  generated order date, future participating-source timestamps or runs ending at/after 16:00.
  Pending orders and their items are excluded from the source-time guard as well
  as demand/fulfillment; afternoon pending rows cannot block morning planning.
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

These are fresh-database examples. The Phase 1.4 checkpoint applied January 2
and 3; the local database has since advanced through January 31. Existing dates
are rejected rather than appended again. Run the production cycle after each
day's orders, or use the Phase 1.6 runner above.

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
