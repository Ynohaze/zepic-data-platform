# Zepic Data Platform

[English](README.md) | [简体中文](README.zh-CN.md)

Zepic is a laptop-sized, end-to-end data and AI engineering portfolio project
for a synthetic manufacturing business. It turns operational PostgreSQL data
into governed analytical products through restart-safe incremental extraction,
versioned quality gates, dbt Silver/Gold models, Airflow orchestration, and a
bounded LLM-assisted analytics application.

The project is intentionally focused on trustworthy data flow rather than
distributed-system scale. PostgreSQL 16, Python, FastAPI, dbt Core and Airflow 3
run locally in one Docker Compose project.

## Architecture

```text
Operational simulator
        |
        v
PostgreSQL operational tables (9)
        |
        | allowlisted keyset API: (updated_at, primary key)
        v
FastAPI source service
        |
        | independent extraction + transactional watermarks
        v
Append-only Raw versions, batches and quality results
        |
        | row validation + order-total reconciliation
        v
dbt Silver (10 tables) -> dbt Gold (8 tables)
        |                         |
        |                         +-> deterministic dashboard metrics
        |                         +-> guarded Level 1 question answering
        |                         +-> bounded analytical agent beta
        v
Airflow: 9 parallel extracts -> quality gates -> warehouse build
```

The LLM never receives database credentials or direct database access. Every
model-proposed query is parsed, restricted to approved Gold models, executed by
a read-only PostgreSQL role with timeout and row limits, and returned with its
evidence and visible execution events.

## What this project demonstrates

- Deterministic, atomic daily order, production, inventory and fulfilment cycles.
- Cursor-based incremental extraction for nine source tables with fixed upper
  bounds and per-table watermarks.
- Append-only Raw storage where rows, batch metadata and watermark movement
  commit together and remain safe to retry.
- Versioned quarantine rules and exact order-header/line reconciliation gates.
- Ten physical Silver tables and eight analytics-ready Gold tables, built and
  tested with dbt.
- A manual Airflow DAG with parallel extraction, bounded concurrency, visible
  downstream blocking and task-level recovery.
- A lightweight dashboard, single-query Level 1 analytics, and a bounded agent
  that may plan up to three guarded queries while exposing its real event trail.

## Accepted checkpoint

| Evidence | Accepted result |
| --- | ---: |
| Operational orders / order items | 3,468 / 10,334 |
| Completed production orders / runs | 610 / 610 |
| Shipped / pending orders | 3,342 / 126 |
| Raw rows / extraction batches | 15,551 / 72 |
| Latest order reconciliation | 3,468 matched, 0 issues |
| Silver / Gold tables | 10 / 8 |
| Full Python suite with PostgreSQL | 110/110 passed |
| dbt models and data tests | 18 models, 93 tests, 111/111 passed |
| Airflow closeout run | 1 DAG, 14 tasks, 14/14 successful |

The retained local database is an accepted portfolio checkpoint, not a fixture
required by the code. A fresh environment can build its own deterministic data.

## Quick start

Requirements: Docker with Compose and Python 3.10 or newer.

```sh
cp .env.example .env
# Set local database credentials and, for AI features, GROQ_API_KEY.
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt

docker compose build source-api airflow-scheduler
docker compose up --no-build --abort-on-container-exit \
  --exit-code-from airflow-init airflow-init
docker compose up -d --no-build \
  source-api airflow-apiserver airflow-scheduler airflow-dag-processor
```

Airflow is available at <http://127.0.0.1:8080>. Enable and manually trigger
`zepic_raw_to_gold`; it has no schedule or catchup and allows one active run.
Local default credentials are `airflow` / `airflow` and should be changed in a
shared environment.

To run the data path directly without Airflow:

```sh
docker compose up -d --wait postgres source-api
.venv/bin/python scripts/etl/extract_to_raw.py --table all
.venv/bin/python scripts/etl/validate_raw.py --fail-on-findings
.venv/bin/python scripts/etl/reconcile_order_totals.py --fail-on-findings
.venv/bin/python scripts/etl/run_warehouse.py build
```

To start the governed analytics application after the Gold build:

```sh
.venv/bin/python scripts/ai/setup_analytics_role.py
.venv/bin/uvicorn src.analytics_api:app --host 127.0.0.1 --port 8001
```

Open <http://127.0.0.1:8001/dashboard/>. Dashboard metrics do not require the
LLM. `Ask data` is the single-query Level 1 flow; `Analysis task` is the bounded
agent beta.

## Data semantics that matter

- Incremental extraction captures current-state changes; it is not CDC. A state
  transition that starts and finishes between pulls cannot be reconstructed.
- Each extraction fixes an upper cursor before paging, but concurrent simulator
  writes are outside the MVP contract. Pause simulation during extraction.
- Silver chooses the latest quality-checked Raw version first and includes it
  only if that exact version is valid. It never falls back to stale valid data.
- `confirmed_at` and `shipped_at` describe observed versions. A null value means
  “not observed,” not “never happened.”
- `current_inventory` is a current balance, not an inventory history.
- Agent output is analytical assistance, not an operational action. The agent
  cannot write data, trigger Airflow, retain conversational memory or run in the
  background.

## Verification

```sh
# Fast suite
.venv/bin/python -m unittest discover -s tests -v

# Full suite with isolated temporary PostgreSQL databases
ZEPIC_DB_TESTS=1 .venv/bin/python -m unittest discover -s tests -v

# Warehouse contract
.venv/bin/python scripts/etl/run_warehouse.py parse
.venv/bin/python scripts/etl/run_warehouse.py build

# Optional live Agent acceptance; consumes provider quota
.venv/bin/python scripts/ai/run_agent_acceptance.py
```

Database tests create uniquely named temporary databases and remove them after
use. `database/verify.sql` is a destructive-edge smoke test and must be used
only against a disposable schema.

## Repository map

| Path | Responsibility |
| --- | --- |
| `config/` | deterministic simulation settings |
| `database/` | operational, Raw and quality PostgreSQL DDL |
| `src/source_api.py` | allowlisted operational source API |
| `src/analytics_api.py` | dashboard and governed analytics API |
| `scripts/etl/` | simulation, extraction, quality and warehouse CLIs |
| `scripts/ai/` | analytics-role setup and live Agent acceptance |
| `warehouse/` | dbt Silver/Gold models, macros and tests |
| `orchestration/` | Airflow DAG, task contract and container images |
| `analytics/` | semantic contract, provider boundary and query guard |
| `dashboard/` | lightweight analytics UI |
| `tests/` | unit and isolated PostgreSQL integration tests |

Detailed phase references:

- [ETL phase: design, operation and evidence](ETL_README.md)
- [AI phase: boundary, setup and evaluation](AI_README.md)

## Scope

This is a local portfolio runtime, not a production deployment. It intentionally
omits TLS, authentication, secret management, scheduler HA, remote logging,
alerting, CDC, distributed compute and autonomous background agents. Those are
explicit boundaries, not implied features.
