# Zepic — Phase 1.2 handoff

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

Phase 1.2 supplies repository structure, Compose and initialization SQL.
Runtime acceptance requires a healthy PostgreSQL 16 container plus successful
`database/verify.sql`; see README for commands. Phase 1.3 will generate initial
customers, products, machines and inventory. Daily activity and production
planning remain subsequent phases.

Collaboration: show changed files and git diff after each group; do not commit
unless explicitly requested.

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
