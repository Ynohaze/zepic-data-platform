from pathlib import Path
import unittest

from orchestration.pipeline_contract import (
    RECONCILE_COMMAND,
    SOURCE_TABLES,
    VALIDATE_COMMAND,
    WAREHOUSE_COMMAND,
    extraction_command,
)


ROOT = Path(__file__).resolve().parents[1]


class OrchestrationContractTests(unittest.TestCase):
    def test_all_source_tables_have_independent_extraction_commands(self):
        self.assertEqual(len(SOURCE_TABLES), 9)
        commands = [extraction_command(table) for table in SOURCE_TABLES]
        self.assertEqual(len(set(commands)), 9)
        for table, command in zip(SOURCE_TABLES, commands):
            self.assertIn(f"--table {table}", command)
            self.assertIn("http://source-api:8000", command)

    def test_quality_steps_are_strict_before_warehouse_build(self):
        self.assertIn("--fail-on-findings", VALIDATE_COMMAND)
        self.assertIn("--fail-on-findings", RECONCILE_COMMAND)
        self.assertEqual(WAREHOUSE_COMMAND, "python scripts/run_warehouse.py build")

    def test_dag_file_is_valid_python(self):
        dag_path = ROOT / "orchestration/dags/zepic_pipeline.py"
        compile(dag_path.read_text(), str(dag_path), "exec")

    def test_compose_uses_versioned_airflow_and_explicit_dag_folder(self):
        compose = (ROOT / "docker-compose.yml").read_text()
        self.assertIn("image: zepic-airflow:3.3.2", compose)
        self.assertIn("AIRFLOW__CORE__EXECUTOR: LocalExecutor", compose)
        self.assertIn("AIRFLOW__CORE__DAGS_FOLDER: /opt/airflow/zepic/orchestration/dags", compose)
        self.assertIn("DBT_LOG_PATH: /tmp/zepic-dbt/logs", compose)
        self.assertIn("DBT_TARGET_PATH: /tmp/zepic-dbt/target", compose)
        self.assertIn("driver: bridge", compose)

    def test_unknown_source_table_is_rejected(self):
        with self.assertRaises(ValueError):
            extraction_command("not_a_source")


if __name__ == "__main__":
    unittest.main()
