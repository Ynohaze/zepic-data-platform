# Zepic Data Platform

Zepic is a local, end-to-end data engineering portfolio project built around a
synthetic manufacturing and order-fulfilment business. It generates operational
data, extracts incremental changes through an API, preserves immutable Raw
versions, enforces data-quality gates, and publishes tested Silver and Gold
models. Apache Airflow makes the complete Raw-to-Gold path observable and
recoverable.

The project is intentionally laptop-sized: PostgreSQL 16, Python, FastAPI, dbt
Core and Airflow 3 run in one Docker Compose project. The design focuses on
transaction boundaries, restart safety, explicit quality rules and honest data
semantics rather than distributed-system scale.

## Architecture

```text
Operational simulator
        |
        v
PostgreSQL operational tables (9)
        |
        | keyset API: (updated_at, primary key)
        v
FastAPI source service
        |
        | per-table incremental extraction + transactional watermarks
        v
zepic_raw: append-only versions, batches and quality results
        |
        | row validation -> order-total reconciliation
        v
dbt Silver: zepic_staging (10 physical tables)
        |
        v
dbt Gold: zepic_analytics (8 analytical tables)

Airflow: 9 parallel extracts -> validation -> reconciliation -> dbt build
```

The source and all analytical layers share one local PostgreSQL instance, while
Airflow metadata uses a separate database. Airflow orchestrates existing CLI
entry points; business and quality logic remains testable outside the DAG.

## What the project demonstrates

- A deterministic operational simulation with atomic daily order, production,
  inventory and fulfilment cycles.
- Cursor-based incremental extraction for all nine source tables, with a fixed
  upper bound and independent per-table watermarks.
- Append-only Raw storage where records, batch metadata and watermark movement
  commit in one transaction and are safe to retry.
- Versioned row-quality rules and a cross-table order-total reconciliation gate;
  invalid data is quarantined rather than deleted.
- Physical Silver tables and analytics-ready Gold dimensions/facts built and
  tested with dbt.
- A manual Airflow DAG with parallel extraction, bounded concurrency, retry
  policy, visible downstream blocking and task-level recovery.
- A governed Level 1 analytics dashboard with deterministic Gold metrics,
  single-turn natural-language questions, visible SQL and evidence metadata.

## Current accepted checkpoint

The final ETL closeout checkpoint covers simulated operations through February
3, 2026. The next simulation date is February 4, 2026.

| Evidence | Accepted result |
| --- | ---: |
| Operational orders / order items | 3,468 / 10,334 |
| Completed production orders / runs | 610 / 610 |
| Shipped / pending orders | 3,342 / 126 |
| Raw rows / extraction batches | 15,551 / 72 |
| Latest orders reconciled | 3,468 matched, 0 issues |
| Silver / Gold tables | 10 / 8 |
| Python tests with PostgreSQL integration | 61 passed |
| dbt models and data tests | 18 models, 93 tests, 111/111 passed |
| Airflow closeout run | 1 DAG, 14 tasks, 14/14 successful |

The database checkpoint is local runtime state, not a fixture required by the
code. Fresh environments can build their own deterministic simulation from the
committed configuration.

## Quick start: run the accepted pipeline

Requirements: Docker Desktop or Docker Engine with Compose, plus Python 3.10 or
newer for host-side commands.

Create local configuration and install the Python dependencies:

```sh
cp .env.example .env
# Replace POSTGRES_PASSWORD and any shared-environment Airflow credentials.
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

Build the two application images, initialize PostgreSQL and the separate Airflow
metadata database, then start the runtime:

```sh
docker compose build source-api airflow-scheduler
docker compose up --no-build --abort-on-container-exit \
  --exit-code-from airflow-init airflow-init
docker compose up -d --no-build \
  source-api airflow-apiserver airflow-scheduler airflow-dag-processor
```

Open <http://127.0.0.1:8080>, sign in with `AIRFLOW_ADMIN_USER` and
`AIRFLOW_ADMIN_PASSWORD` (local defaults: `airflow` / `airflow`), enable
`zepic_raw_to_gold`, and trigger it manually. The DAG has no schedule or catchup
and allows only one active run.

Stop the application services without deleting database state:

```sh
docker compose stop \
  source-api airflow-apiserver airflow-scheduler airflow-dag-processor
```

Do not use `docker compose down --volumes` unless the entire local operational,
Raw and warehouse checkpoint is intentionally disposable.

Detailed Airflow startup, parsing and recovery commands are in
[orchestration/README.md](orchestration/README.md).

## Run the Level 1 analytics dashboard

After building the warehouse, configure `ANALYTICS_DB_USER`,
`ANALYTICS_DB_PASSWORD` and `GROQ_API_KEY` in `.env`, then provision the
read-only analytics role and start the local application:

```sh
docker compose up -d --wait postgres
.venv/bin/python scripts/ai/setup_analytics_role.py
.venv/bin/uvicorn src.analytics_api:app --host 127.0.0.1 --port 8001
```

Open <http://127.0.0.1:8001/dashboard/>. The headline metrics use fixed,
guarded SQL and do not depend on Groq. The question workspace performs one
model proposal, one validated read-only query and one evidence-grounded answer.
The disabled `Analysis task` mode is a design seam for a future bounded agent;
Level 1 does not plan or run autonomous task loops.

Detailed provider, safety and recovery notes are in
[analytics/README.md](analytics/README.md).

## Run each layer directly

The pipeline can be exercised without Airflow. Start PostgreSQL and the source
API, then run the same commands used by the DAG:

```sh
docker compose up -d --wait postgres source-api
.venv/bin/python scripts/etl/extract_to_raw.py --table all
.venv/bin/python scripts/etl/validate_raw.py --fail-on-findings
.venv/bin/python scripts/etl/reconcile_order_totals.py --fail-on-findings
.venv/bin/python scripts/etl/run_warehouse.py build
```

Each source table is extracted independently. A failed table rolls back its Raw
rows, batch and watermark together; successfully completed tables do not need to
be repeated. The validation and reconciliation commands are strict gates in the
orchestrated path.

To inspect dbt lineage and model documentation:

```sh
.venv/bin/python scripts/etl/run_warehouse.py docs generate
.venv/bin/python scripts/etl/run_warehouse.py docs serve
```

## Build a fresh simulation

PostgreSQL initialization creates the nine-table operational schema only when
its volume is new. Preview and then load the deterministic master data:

```sh
docker compose up -d --wait postgres
.venv/bin/python scripts/etl/generate_initial_data.py
.venv/bin/python scripts/etl/generate_initial_data.py --apply
```

Preview a date range before applying complete daily cycles:

```sh
.venv/bin/python scripts/etl/run_simulation.py \
  --start-date 2026-01-02 --days 30 --resume
.venv/bin/python scripts/etl/run_simulation.py \
  --start-date 2026-01-02 --days 30 --resume --apply
```

Every applied day wraps order confirmation and generation, production, inventory
movement and whole-order shipping in one outer transaction. A failed day rolls
back as a unit; committed earlier days remain intact. `--resume` skips inferred
complete dates and can finish the latest order-only date after a resolved failure.
It is deliberately not a general job ledger: keep configuration and retained
history unchanged while extending one simulation.

## Data contracts and important semantics

### Operational model

The source contains customers, products, machines, inventory, orders, order
items, production orders, production runs and maintenance records. Orders move
through `pending -> confirmed -> shipped`; fulfilment is whole-order only.
Production closes shortages with a configurable buffer and batch rounding. The
simulator commits final daily states, not a complete business-event history.

### Incremental extraction

The API pages by `(updated_at, primary key)` and serializes money as decimal
strings and timestamps as UTC ISO-8601 values. For each table, extraction fixes
an upper cursor before paging from the saved lower cursor. This prevents page
drift but does not make multiple HTTP requests one source snapshot, so simulator
writes should be paused during extraction.

This is current-state incremental extraction, not CDC. A transition that starts
and finishes between two pulls cannot be reconstructed.

### Quality and quarantine

Raw rows are immutable. Versioned rules check metadata consistency, required
fields, domains, numeric and lifecycle constraints, and landed relationships.
Any finding quarantines that record version without changing the extraction
watermark. A strict run continues to fail while a source key's latest validated
version remains quarantined, so an empty retry cannot bypass an old issue.

Order reconciliation compares the latest eligible header with its current line
items using exact decimal arithmetic. Mismatches, missing items and quarantine-
related skips block the warehouse build.

### Silver and Gold

dbt first ranks the latest quality-checked Raw version for each key and then
applies validity. If that exact latest version is quarantined, the key is omitted;
the model never silently falls back to an older valid value. Silver is rebuilt as
ten physical tables, while Raw remains the replayable version history.

Gold contains three current dimensions, four facts and current inventory:

| Model | Grain |
| --- | --- |
| `dim_customers`, `dim_products`, `dim_machines` | one row per current entity |
| `fct_orders` | one row per order |
| `fct_order_lines` | one row per order item |
| `fct_production_runs` | one row per production run |
| `fct_maintenance` | one row per maintenance record |
| `current_inventory` | one row per product |

`stg_order_versions` preserves observed valid order versions for lifecycle
milestones. Because extraction is not CDC, a null `confirmed_at` means “not
observed”, not “never confirmed”. The current checkpoint captured pending and
shipped versions but no confirmed versions, so it must not be presented as a
complete order funnel.

## Airflow execution and recovery

`zepic_raw_to_gold` runs nine independent extraction tasks before its quality
gates and warehouse build:

```text
start -> extract.* -> validate_raw -> reconcile_order_totals
      -> build_warehouse -> complete
```

Extraction retries twice at 30-second intervals; downstream quality and build
tasks do not retry automatically so failures stay visible for operator review.
Global parallelism is four and `max_active_runs=1` prevents concurrent pipeline
runs from advancing the same watermarks.

Acceptance covered both source-service failure and a controlled reconciliation
failure. After the cause was removed, clearing only the affected tasks completed
the same DAG run with all 14 tasks successful and without duplicating the data
checkpoint. See [PHASE5_HANDOFF.md](PHASE5_HANDOFF.md) for the recorded run and
recovery evidence.

Closeout screenshots and the final evidence narrative are collected in
[ETL_PHASE_SUMMARY.md](ETL_PHASE_SUMMARY.md).

## Tests and verification

Run the fast host-side suite:

```sh
.venv/bin/python -m unittest discover -s tests -v
```

Run the complete suite, including temporary PostgreSQL databases:

```sh
ZEPIC_DB_TESTS=1 .venv/bin/python -m unittest discover -s tests -v
```

Database tests create isolated, uniquely named databases and remove them after
use; the configured PostgreSQL user must be able to create databases. The
final full run contains 61 tests.

Run the warehouse contract directly with:

```sh
.venv/bin/python scripts/etl/run_warehouse.py parse
.venv/bin/python scripts/etl/run_warehouse.py build
```

The build covers 18 table models and 93 dbt data tests. Tests enforce keys,
relationships, domains, latest-invalid suppression, nonnegative inventory,
production terminal fields, lifecycle chronology and exact order totals.

`database/verify.sql` is a destructive-edge smoke test for a separate disposable
schema checkpoint. Do not run it against populated project data.

## Repository map

| Path | Responsibility |
| --- | --- |
| `config/` | deterministic master-data, daily-order and production settings |
| `database/` | operational, Raw and quality PostgreSQL DDL plus disposable verification |
| `src/source_api.py` | allowlisted, paginated operational source API |
| `scripts/etl/` | simulation, extraction, validation, reconciliation and dbt CLIs |
| `scripts/ai/` | governed AI analytics setup and operational CLIs |
| `warehouse/` | dbt project, Silver/Gold models, macros and singular tests |
| `orchestration/` | Airflow DAG, task contract, container images and runbook |
| `tests/` | unit and isolated PostgreSQL integration tests |
| `PHASE*_HANDOFF.md` | phase acceptance evidence and historical checkpoints |

The most durable design references are
[warehouse/DESIGN.md](warehouse/DESIGN.md) and
[orchestration/DESIGN.md](orchestration/DESIGN.md). Earlier handoffs preserve why
the project evolved; this README describes the final integrated path.

## Scope boundaries

This is a portfolio runtime, not a production deployment. It intentionally has
no TLS, secret manager, source authentication, CDC, scheduler HA, remote logs,
alerting integration or Celery/Kubernetes executor. Maintenance/failure data
generation, partial fulfilment and random production yield loss are also outside
scope. These boundaries keep the project focused on a coherent, testable ETL
contract rather than unimplemented platform breadth.
