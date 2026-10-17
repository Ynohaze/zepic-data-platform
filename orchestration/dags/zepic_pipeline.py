"""Phase 5 manual DAG: source extraction through the tested Gold build."""

from datetime import timedelta

import pendulum

from airflow.providers.standard.operators.bash import BashOperator
from airflow.providers.standard.operators.empty import EmptyOperator
from airflow.sdk import DAG, TaskGroup

from orchestration.pipeline_contract import (
    PROJECT_DIR,
    RECONCILE_COMMAND,
    SOURCE_TABLES,
    VALIDATE_COMMAND,
    WAREHOUSE_COMMAND,
    extraction_command,
)


with DAG(
    dag_id="zepic_raw_to_gold",
    description="Extract operational state, enforce quality gates, and rebuild Silver/Gold",
    schedule=None,
    start_date=pendulum.datetime(2026, 1, 1, tz="UTC"),
    catchup=False,
    max_active_runs=1,
    default_args={"retries": 0},
    tags=["zepic", "phase5"],
) as dag:
    start = EmptyOperator(task_id="start")

    with TaskGroup(group_id="extract", tooltip="Independently extract all nine source tables"):
        extraction_tasks = [
            BashOperator(
                task_id=table,
                bash_command=extraction_command(table),
                cwd=PROJECT_DIR,
                append_env=True,
                retries=2,
                retry_delay=timedelta(seconds=30),
            )
            for table in SOURCE_TABLES
        ]

    validate_raw = BashOperator(
        task_id="validate_raw",
        bash_command=VALIDATE_COMMAND,
        cwd=PROJECT_DIR,
        append_env=True,
    )
    reconcile_order_totals = BashOperator(
        task_id="reconcile_order_totals",
        bash_command=RECONCILE_COMMAND,
        cwd=PROJECT_DIR,
        append_env=True,
    )
    build_warehouse = BashOperator(
        task_id="build_warehouse",
        bash_command=WAREHOUSE_COMMAND,
        cwd=PROJECT_DIR,
        append_env=True,
    )
    complete = EmptyOperator(task_id="complete")

    start >> extraction_tasks >> validate_raw >> reconcile_order_totals >> build_warehouse >> complete
