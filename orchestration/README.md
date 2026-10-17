# Local Airflow operations

Phase 5 uses the existing PostgreSQL container for both the Zepic platform and
Airflow, but keeps them in separate databases. Never remove the Compose volumes
unless you deliberately intend to delete the local Zepic checkpoint.

Build the versioned local images:

```sh
docker compose build source-api airflow-scheduler
```

Initialize or migrate the separate Airflow metadata database:

```sh
docker compose up --no-build --abort-on-container-exit \
  --exit-code-from airflow-init airflow-init
```

Verify DAG parsing without triggering a run:

```sh
docker compose run --rm --no-deps airflow-dag-processor \
  dag-processor --num-runs 1
docker compose run --rm --no-deps airflow-scheduler \
  dags list-import-errors --output=json
docker compose run --rm --no-deps airflow-scheduler \
  dags list --output=json
```

The expected result is zero import errors and one paused DAG named
`zepic_raw_to_gold`.

Start the local source API and Airflow UI services:

```sh
docker compose up -d --no-build \
  source-api airflow-apiserver airflow-scheduler airflow-dag-processor
```

Open <http://127.0.0.1:8080> and sign in with `AIRFLOW_ADMIN_USER` and
`AIRFLOW_ADMIN_PASSWORD`. Their local defaults are `airflow` / `airflow`; change
them in `.env` if this environment is shared. The DAG remains paused until it is
explicitly enabled, so opening its Graph view does not run the pipeline.

Stop the UI runtime without deleting PostgreSQL data:

```sh
docker compose stop \
  source-api airflow-apiserver airflow-scheduler airflow-dag-processor
```

To repeat the non-destructive reconciliation failure exercise, create the
one-shot sentinel before triggering a manual run:

```sh
docker compose exec airflow-scheduler \
  touch /tmp/zepic-simulate-reconciliation-failure
```

After Airflow shows `reconcile_order_totals` as failed and its two downstream
tasks as blocked, remove the sentinel:

```sh
docker compose exec airflow-scheduler \
  rm -f /tmp/zepic-simulate-reconciliation-failure
```

In the failed task's UI, choose **Clear** and confirm the three affected tasks.
Airflow reruns reconciliation, `build_warehouse`, and `complete` while retaining
the successful extraction and validation task instances. Always remove the
sentinel after the exercise; leaving it present intentionally blocks every
subsequent run at reconciliation.

Do not copy the Airflow quick-start cleanup command `docker compose down
--volumes` into this project: `zepic_postgres_data` contains the operational and
warehouse checkpoint.
