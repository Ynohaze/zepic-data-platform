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
`zepic_raw_to_gold`. Starting the long-running services and demonstrating a DAG
failure/recovery are later checkpoints.

Do not copy the Airflow quick-start cleanup command `docker compose down
--volumes` into this project: `zepic_postgres_data` contains the operational and
warehouse checkpoint.
