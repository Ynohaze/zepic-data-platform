"""Stable task names and commands shared by the Phase 5 DAG and its tests."""

SOURCE_TABLES = (
    "customers",
    "products",
    "machines",
    "inventory",
    "orders",
    "order_items",
    "production_orders",
    "production_runs",
    "maintenance_records",
)

PROJECT_DIR = "/opt/airflow/zepic"
SOURCE_API_URL = "http://source-api:8000"


def extraction_command(table):
    if table not in SOURCE_TABLES:
        raise ValueError(f"Unsupported source table: {table}")
    return (
        "python scripts/extract_to_raw.py "
        f"--base-url {SOURCE_API_URL} --table {table}"
    )


VALIDATE_COMMAND = "python scripts/validate_raw.py --fail-on-findings"
RECONCILE_COMMAND = "python scripts/reconcile_order_totals.py --fail-on-findings"
WAREHOUSE_COMMAND = "python scripts/run_warehouse.py build"
