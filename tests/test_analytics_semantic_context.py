import json
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONTEXT_PATH = PROJECT_ROOT / "analytics" / "semantic_context.json"

EXPECTED_GOLD_MODELS = {
    "current_inventory",
    "dim_customers",
    "dim_machines",
    "dim_products",
    "fct_maintenance",
    "fct_order_lines",
    "fct_orders",
    "fct_production_runs",
}


class AnalyticsSemanticContextTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.context = json.loads(CONTEXT_PATH.read_text())

    def test_contract_is_restricted_to_read_only_gold_queries(self):
        self.assertEqual(self.context["allowed_schema"], "zepic_analytics")
        self.assertEqual(
            self.context["query_contract"],
            {
                "statement_type": "select",
                "maximum_statements": 1,
                "allow_ctes": True,
                "allow_window_functions": True,
                "allow_database_writes": False,
                "allow_cross_schema_access": False,
            },
        )

    def test_every_gold_model_has_a_complete_basic_contract(self):
        tables = self.context["tables"]
        self.assertEqual(set(tables), EXPECTED_GOLD_MODELS)

        for name, table in tables.items():
            with self.subTest(table=name):
                self.assertTrue(table["grain"])
                self.assertTrue(table["description"])
                self.assertTrue(table["primary_key"])
                self.assertEqual(len(table["columns"]), len(set(table["columns"])))
                self.assertTrue(set(table["primary_key"]).issubset(table["columns"]))

    def test_relationship_endpoints_reference_declared_columns(self):
        tables = self.context["tables"]

        for relationship in self.context["relationships"]:
            for endpoint_name in ("from", "to"):
                table_name, column_name = relationship[endpoint_name].split(".", 1)
                with self.subTest(
                    relationship=relationship, endpoint=endpoint_name
                ):
                    self.assertIn(table_name, tables)
                    self.assertIn(column_name, tables[table_name]["columns"])

    def test_known_history_limitations_are_explicit(self):
        limitations = " ".join(self.context["global_limitations"]).lower()
        self.assertIn("not cdc", limitations)
        self.assertIn("inventory trends", limitations)
        self.assertIn("lifecycle", limitations)


if __name__ == "__main__":
    unittest.main()
