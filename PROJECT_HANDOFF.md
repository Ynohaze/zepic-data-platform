# Zepic — Phase 1.6 handoff

**Current project checkpoint: [PHASE4_HANDOFF.md](PHASE4_HANDOFF.md).** It covers
the validated raw-to-warehouse dbt implementation and current analytics counts.
The Phase 1 history below remains useful for operational business semantics.

**Starting Phase 2 in a new session? Read [PHASE2_HANDOFF.md](PHASE2_HANDOFF.md)
first.** It consolidates the current checkpoint, collaboration rules, source
timestamp semantics and proposed next steps. Sections below retain phase history.

## Phase 1.6 implementation and validation — 2026-10-01

`scripts/run_simulation.py` connects the existing daily and production helpers
with one outer transaction per new date. Source/business locks use the bootstrap
order, nested helpers use savepoints, and daily JSON progress prints after commit.
Preview lists dates/actions in a read-only snapshot; it is not a quantity forecast.

`--start-date`, `--days` (1–366; default 30), `--resume`, `--apply`,
`--daily-config` and `--production-config` control the run. Resume infers completion
from contiguous retained order history and the all-success fulfillment rule;
it skips completed days or finishes the latest order-only day. It refuses gaps,
unfinished production and recorded production with remaining confirmed backlog.
No schema/job-ledger change. Keep configs fixed and retain history; manual edits
and future partial-failure models need stronger checkpoint semantics.

All 32 tests passed, including a full 30-day database test, preview without
writes/sequences, rollback of the whole failed day, recovery, price/stock/timeline
invariants, and concurrent resume. Temporary databases were automatically removed.

Applied range January 2–31 (30 days) in `zepic_operational`: skipped January 2–3,
committed January 4–31 (28 new days). Retained January 3's historical evening
schedule; all new cycles use 09:00 planning and 16:00 shipping.

- 3,137 orders; 9,306 items; 550 completed production orders and 550 runs.
- 3,018 shipped orders; 119 pending, all created January 31; no confirmed backlog.
- Incremental inventory check: 1,672 opening + 88,180 produced − 89,193 shipped
  = 659 units, verified separately per product. No negative stock or overlapping runs.
- Order totals and new shipment times verified; master data and maintenance unchanged.

Next simulation date is February 1. Phase 2 can now build source API and watermark
incremental ingestion. Extract between future simulation days to observe row
updates; this database is current-state storage, not a transition/event history.
Maintenance/failure generation remains deferred. Earlier checkpoints below are
historical and do not describe the current database counts.

## Production schedule correction — 2026-10-01

New cycles plan at 09:00 UTC, run from 09:01 and ship at 16:00. All runs must
finish strictly before 16:00. Pending orders and their items are excluded from
the time guard as well as demand/fulfillment, so pre-generated afternoon pending
rows do not block morning production. Future timestamps on participating
confirmed demand and operational state still prevent historical rewrites.
Existing January 3 database rows retain the prior schedule; no business data
was modified by this correction. Earlier notes below describe that prior run.

## Phase 1.5 implementation and validation — 2026-10-01

Production and fulfillment are implemented in `scripts/run_production_planning.py`
with `config/production.json`. No schema changes. At 18:00 UTC, confirmed demand
minus inventory determines positive shortages; apply 5% buffer and round up to
the snapshotted batch multiple. One production order and one successful run per
short product; actual output equals planned output. No random failure/yield loss.

Seed + date shuffles available machines without active maintenance. Assign runs
in waves, each lasting 10 simulated minutes regardless of output quantity.
Machines can perform successive runs, never overlapping; reject a cycle that
cannot finish before midnight. All output is added before oldest-first whole-order
shipping one minute after the last run. Unfulfillable orders consume no stock.

Preview changes no rows/sequences. Apply locks sources and business tables and
commits planning, execution, inventory and shipping atomically. Intermediate
states are not separately committed/event-logged. Refuse unfinished prior work,
future source timestamps, wrong dates, missing inventory and no-machine shortages.
Recorded production/shipping protects against repeats; a no-demand no-op leaves
no persistent cycle marker and can be repeated safely.

Validation: 23 tests passed (12 pure/unit, 11 PostgreSQL integration), including
inventory balance, nonoverlapping runs, shortage rounding, insufficient stock,
all-or-nothing shipment selection, no-machine refusal, rollback at shipping,
concurrent duplicate protection, price/item preservation and next-day continuation.
All database tests used automatically removed isolated temporary databases.

Applied January 3 to local `zepic_operational`:

- 14 completed production orders, 14 completed runs; 3,200 units produced.
- 148 orders shipped at 18:32 UTC; 82 remain pending, none confirmed.
- Inventory: 3,100 opening + 3,200 produced − 4,628 shipped = 1,672 units.
- No negative stock, no running production; order items and master data unchanged.

Next is Phase 1.6: compose daily orders and production into consecutive complete
cycles, starting January 4 for this database. Earlier notes below describe their
historical validation states. Zepic permits local commits/pulls; pushes still
require explicit authorization. This permission does not apply to project 4403.

## Phase 1.4 implementation and validation — 2026-10-01

`scripts/generate_daily_operations.py` with `config/daily_operations.json` now
generates 50–150 orders/day, 1–5 distinct active products/order and 1–20 units
per item. Customer and product choices are uniform; prices are snapshots and
totals use Decimal. The default first date is January 2, 2026.

New orders start pending at 09:00–17:00 UTC (end exclusive). On the next day,
older pending orders become confirmed at 08:00 UTC, updating only the header.
There is no cancellation, shipping, inventory mutation or production here.

Seed + date gives independent daily random streams. Preview uses a read-only
database snapshot. Apply locks customer/product sources and order tables,
checks the next date, confirms older orders and inserts orders/items in one
transaction. Duplicate, skipped and backward dates are refused. The date guard
uses retained order rows and positive daily counts; deleting a day's orders
can invalidate that guard. No schema changes or run ledger were introduced.

Validation: all 13 tests passed, including five PostgreSQL 16 integration tests
in an automatically removed temporary database. Covered preview without writes
or sequence changes, exact totals, historical price snapshots, active products,
confirmation timestamps, duplicate/date guards, rollback including previous
confirmations, retry after failure and two concurrent attempts at one date.

Local `zepic_operational` was advanced through two simulation days:

- January 2: 148 orders / 441 items, now confirmed on January 3 at 08:00 UTC.
- January 3: 82 orders / 239 items, currently pending.
- Totals: 230 orders / 680 items. Master data and inventory unchanged;
  production and maintenance tables remain empty.

Next: Phase 1.5 planning/production and all-or-nothing fulfillment of confirmed
demand. Next ungenerated order date is January 4. Do not advance a 30-day run
until the production cycle is connected. Earlier validation notes below record
historical states before this phase. Phase 1.4 was committed as `53bb9a0` locally.

## Phase 1.3 implementation

Initial data is configured in `config/initial_data.json` and generated by
`scripts/generate_initial_data.py`. Defaults: 100 customers with exact tier
counts 70/20/10, 20 explicitly defined products, 5 available machines and
20 inventory rows. Product details are synthetic examples.

Seed 42 controls customer-tier shuffling; a separate seed + 1 stream controls
inventory batch choices (0/1/3/5). The default simulation origin is January 1,
2026 UTC and can be changed before initialization. Money uses Decimal.

The CLI previews by default and writes with --apply. All nine operational
tables must be empty; table locks protect the check/write transaction against
concurrent writers. Existing data is never silently overwritten or appended.
Inventory links use returned product IDs, not assumed identity values.

Later phases target 30 simulation days for development, then 365 days with
50–150 orders/day and about 3 items/order. The estimated full dataset is
roughly 100,000–200,000 rows; these are future simulation targets, not Phase 1.3
rows or a guaranteed output size.

### Validation — 2026-09-25

- Four generator unit tests passed on Python 3.14.6.
- PostgreSQL 16 integration checks passed in an isolated temporary database:
  late-failure rollback, non-1 generated IDs, inventory relationships, timestamps
  and refusal of repeated initialization. The test database was removed.
- Applied to local `zepic_operational`: customers 100, products 20, machines 5,
  inventory 20; all other operational tables remain empty. Seven products have
  zero stock. All machines are available. No schema changes were needed.
- Installed locally: psycopg/psycopg-binary 3.3.6, python-dotenv 1.2.3.
- The Phase 1.2 validation below describes the historical pre-seeding state.

Design source: conversation `6aace92d-c368-83ec-86bc-39d1d50c595f`,
final Phase 1.1 decisions. This note summarizes the operational phase.

## Locked schema and business rules

- Nine tables: customers, products, machines, inventory, orders, order_items,
  production_orders, production_runs and maintenance_records.
- BIGINT identity keys; inventory uses product_id as its PK/FK. Money uses
  NUMERIC(12,2), quantities INTEGER, timestamps TIMESTAMPTZ.
- Customer tiers: standard / silver / gold. Orders retain total_amount.
- Order lifecycle: pending → confirmed → shipped. Shipping is all-or-nothing
  per order and deducts inventory in the same application transaction.
  There is no fulfilled_quantity, reservation or allocation model.
- Planning: confirmed demand minus current available inventory. Only positive
  shortages create production orders. Apply buffer and round to batch multiple.
  Planning fields are historical snapshots; do not subtract open production.
- Production orders: planned → released → completed. Runs start as running,
  then completed / failed; final actual_quantity may be zero.
- All runs finish before the next planning cycle. Cross-cycle WIP is out of scope.
- All available machines can produce all products. Each run links to one product
  through its production order. A machine can have only one running run.
- Machines: available / maintenance / retired. Running is derived from runs.
  The trigger validates availability when inserting/updating a running run.
- Maintenance: scheduled → in_progress → completed; completed requires start,
  end and a non-null cost (zero is valid). One active maintenance per machine.
- Application transactions own inventory changes, order totals and machine /
  maintenance synchronization; there are no automatic business-state triggers.
  Future maintenance logic must avoid starting maintenance during an active run.
- All tables have updated_at indexes and triggers. Ordinary updates use current
  transaction time; an explicitly different simulation timestamp is preserved.
  Explicitly supplying the previous timestamp still triggers an automatic update.
- SQL CHECKs validate row states, not the full sequence of lifecycle transitions.

## Scope and next step

Phases 1.2–1.6 supply the schema, initial master data and multi-day operational
simulation. Next is Phase 2 source API and incremental ingestion.
`database/verify.sql` remains a Phase 1.2 check for a disposable database only.

Collaboration: show changed files and git diff after each group. The user permits
commits and pulls in Zepic; do not push without explicit authorization.

## Phase 1.2 validation — 2026-09-23

- Docker Desktop bundled Compose configuration validation passed.
- `postgres:16` started successfully as `zepic-postgres-1` and became healthy.
- First startup initialized `zepic_operational`: 9 tables, 32 total indexes
  (20 explicit plus 12 PK/UNIQUE) and 10 triggers.
- `database/verify.sql` passed on PostgreSQL 16 in the container; it also passed
  on a temporary local PostgreSQL 17 instance, which was then stopped.
- Test rows were rolled back; identity sequences have advanced. The operational
  database remains empty and the container remains running on localhost:5432.
- Local `.env` contains a generated password and is ignored. No files were
  staged and no commit was created.
