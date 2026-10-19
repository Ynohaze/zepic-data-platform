import unittest

from analytics.query_guard import QueryGuard, QueryRejected


class AnalyticsQueryGuardTests(unittest.TestCase):
    def setUp(self):
        self.guard = QueryGuard()

    def assert_rejected(self, sql, code):
        with self.assertRaises(QueryRejected) as raised:
            self.guard.validate(sql)
        self.assertEqual(raised.exception.code, code)

    def test_accepts_gold_aggregation(self):
        result = self.guard.validate(
            """
            SELECT order_date, COUNT(*) AS order_count,
                   SUM(order_total_amount) AS booked_order_value
            FROM zepic_analytics.fct_orders
            GROUP BY order_date
            ORDER BY order_date
            """
        )

        self.assertEqual(result.source_models, ("fct_orders",))
        self.assertIn("zepic_analytics.fct_orders", result.sql)

    def test_accepts_cte_join_and_window_function(self):
        result = self.guard.validate(
            """
            WITH product_sales AS (
                SELECT product_id, SUM(quantity) AS units
                FROM zepic_analytics.fct_order_lines
                GROUP BY product_id
            )
            SELECT products.product_name, sales.units,
                   RANK() OVER (ORDER BY sales.units DESC) AS sales_rank
            FROM product_sales AS sales
            JOIN zepic_analytics.dim_products AS products
              ON products.product_id = sales.product_id
            """
        )

        self.assertEqual(
            result.source_models,
            ("dim_products", "fct_order_lines"),
        )

    def test_accepts_ntile_for_bounded_customer_segmentation(self):
        result = self.guard.validate(
            """
            SELECT customer_id,
                   NTILE(4) OVER (ORDER BY SUM(order_total_amount) DESC) AS value_quartile
            FROM zepic_analytics.fct_orders
            GROUP BY customer_id
            """
        )

        self.assertEqual(result.source_models, ("fct_orders",))

    def test_accepts_read_only_case_expression(self):
        result = self.guard.validate(
            """
            SELECT order_date,
                   CASE
                       WHEN order_total_amount >= 1000 THEN 'large'
                       ELSE 'standard'
                   END AS order_size
            FROM zepic_analytics.fct_orders
            """
        )

        self.assertEqual(result.source_models, ("fct_orders",))

        self.assert_rejected(
            "SELECT IF(order_total_amount > 0, 1, 0) "
            "FROM zepic_analytics.fct_orders",
            "function_access",
        )

    def test_rejects_multiple_statements(self):
        self.assert_rejected(
            "SELECT * FROM zepic_analytics.fct_orders; SELECT 1",
            "multiple_statements",
        )

    def test_rejects_write_hidden_in_cte(self):
        self.assert_rejected(
            """
            WITH changed AS (
                DELETE FROM zepic_analytics.fct_orders RETURNING *
            )
            SELECT * FROM changed
            """,
            "prohibited_operation",
        )

    def test_rejects_select_locking_clause(self):
        self.assert_rejected(
            "SELECT * FROM zepic_analytics.fct_orders FOR UPDATE",
            "prohibited_operation",
        )

    def test_rejects_operational_and_unqualified_relations(self):
        self.assert_rejected(
            "SELECT * FROM public.orders",
            "schema_access",
        )
        self.assert_rejected(
            "SELECT * FROM fct_orders",
            "schema_access",
        )

    def test_rejects_unknown_gold_relation(self):
        self.assert_rejected(
            "SELECT * FROM zepic_analytics.private_metrics",
            "table_access",
        )

    def test_rejects_database_and_unsafe_function_access(self):
        self.assert_rejected(
            "SELECT * FROM other_db.zepic_analytics.fct_orders",
            "catalog_access",
        )
        self.assert_rejected(
            "SELECT PG_SLEEP(1) FROM zepic_analytics.fct_orders LIMIT 1",
            "function_access",
        )

    def test_rejects_non_analytical_select_without_a_gold_source(self):
        self.assert_rejected("SELECT 1", "missing_gold_source")


if __name__ == "__main__":
    unittest.main()
