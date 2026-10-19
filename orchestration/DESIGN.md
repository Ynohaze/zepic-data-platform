# Phase 5 orchestration design

Phase 5 adds a thin local Airflow layer over the existing command-line entry
points. Airflow owns scheduling, dependencies, retries, logs, and run state; it
does not duplicate extraction, validation, reconciliation, or dbt logic.

## DAG contract

The first checkpoint established the testable DAG contract below.

```text
start (initialize/upgrade Raw schema once)
  |
  +-- extract.customers -----------+
  +-- extract.products ------------+
  +-- extract.machines ------------+
  +-- extract.inventory -----------+
  +-- extract.orders --------------+
  +-- extract.order_items ---------+--> validate_raw
  +-- extract.production_orders ---+          |
  +-- extract.production_runs -----+          v
  +-- extract.maintenance_records -+  reconcile_order_totals
                                               |
                                               v
                                        build_warehouse
                                               |
                                               v
                                            complete
```

The DAG is manual (`schedule=None`), has no catchup, and permits only one active
run. Extraction retries twice because each table batch advances its watermark in
the same transaction as its raw records. Downstream steps are idempotent and do
not retry automatically in this first version, keeping failures visible for the
recovery demonstration.

## Quality gates

`validate_raw.py --fail-on-findings` fails while the latest quality-checked
version of any source key remains quarantined. It intentionally checks current
latest versions rather than only newly validated batches, so retrying a failed
Airflow task cannot bypass an existing quarantine.

`reconcile_order_totals.py --fail-on-findings` fails when the current snapshot
contains an order-total mismatch, an order without valid details, or an order
skipped because its header or details are quarantined. The dbt build therefore
runs only after both gates pass.

## Local runtime

One Compose project will place the application containers on an explicit bridge
network. The intended application path is:

```text
Airflow task -> http://source-api:8000 -> postgres:5432
Airflow dbt task ----------------------> postgres:5432
```

The Airflow image extends the official versioned Airflow 3.3.2 Python 3.13 image
and bakes in this project's Python/dbt dependencies. Runtime package installation
is intentionally avoided.

The existing PostgreSQL container is reused. Operational, Raw, Silver, and Gold
data remain in `POSTGRES_DB`; Airflow creates and uses a separate `airflow`
database in that same PostgreSQL instance. The one-shot `airflow-db-init` service
creates the metadata database idempotently before Airflow runs its own migrations.

The local Airflow deployment uses `LocalExecutor`, so this project needs no Redis
or Celery worker. Airflow 3 is split into three long-running services:

- `airflow-apiserver` provides the UI and task execution API;
- `airflow-scheduler` decides when runnable task instances start;
- `airflow-dag-processor` parses and serializes DAG files.

`airflow-init` is a one-shot migration and admin-user initialization service.
The source API is its own application container. All services join the explicit
`zepic-network` bridge network.

Global Airflow parallelism is capped at four for a laptop-sized local runtime.
The nine extraction tasks remain independent parallel branches in the DAG, but
Airflow executes at most four task instances at once.

The `start` task applies the idempotent Raw schema before those branches are
released. Per-table Airflow commands then skip schema initialization. This keeps
parallel extraction transactions from deadlocking against repeated concurrent
`ALTER TABLE` statements while preserving direct CLI behavior: a host-side
`extract_to_raw.py --table all` still initializes its own schema by default.

The project checkout is mounted read-only in Airflow containers. dbt therefore
writes its runtime logs and compiled artifacts to `/tmp/zepic-dbt`; warehouse
tables and views remain persisted in PostgreSQL.

The second checkpoint built both custom images, initialized the separate Airflow
database, and ran one Airflow DAG processor pass. Airflow discovered one paused
`zepic_raw_to_gold` DAG with 14 tasks and zero import errors. It did not trigger a
DAG run.

## Failure and recovery acceptance scenario

The Phase 5 acceptance run can make reconciliation fail without mutating the
production checkpoint by creating
`/tmp/zepic-simulate-reconciliation-failure` in the scheduler container. The
real reconciliation still runs first; the sentinel then makes the task exit
nonzero with an explicit simulated order-total mismatch message. Airflow must
show the gate in red and all downstream tasks as blocked. After removing the
sentinel and clearing that task, only the failed/downstream path should resume
and the DAG run should finish successfully.

## Acceptance result

On 2026-10-17, manual run
`manual__2026-10-17T10:36:01.774340+00:00` exercised this scenario. Extraction
and validation succeeded, reconciliation failed with the explicit controlled
mismatch log message, and `build_warehouse` plus `complete` were marked
`upstream_failed`. Removing the sentinel and clearing the reconciliation task
selected exactly those three task instances. The same run then completed with
all 14 tasks successful. The DAG was paused again after acceptance.
