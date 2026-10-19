import unittest

from analytics.query_engine import QueryEngine


class FakeDescription:
    def __init__(self, name):
        self.name = name


class FakeCursor:
    def __init__(self, rows):
        self.rows = rows
        self.statements = []
        self.description = (FakeDescription("order_id"),)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, statement, parameters=None):
        self.statements.append((statement, parameters))

    def fetchall(self):
        return self.rows


class FakeTransaction:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class FakeConnection:
    def __init__(self, rows):
        self.cursor_instance = FakeCursor(rows)
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True
        return False

    def transaction(self):
        return FakeTransaction()

    def cursor(self):
        return self.cursor_instance


class AnalyticsQueryEngineTests(unittest.TestCase):
    def test_executes_in_read_only_transaction_with_timeout_and_row_cap(self):
        connection = FakeConnection([(1,), (2,), (3,)])
        engine = QueryEngine(
            lambda: connection,
            statement_timeout_ms=1_250,
            max_rows=2,
        )

        result = engine.execute(
            "SELECT order_id FROM zepic_analytics.fct_orders ORDER BY order_id"
        )

        self.assertEqual(result.columns, ("order_id",))
        self.assertEqual(result.rows, ((1,), (2,)))
        self.assertTrue(result.truncated)
        self.assertEqual(result.source_models, ("fct_orders",))
        self.assertTrue(connection.closed)
        self.assertEqual(
            connection.cursor_instance.statements[0],
            ("SET TRANSACTION READ ONLY", None),
        )
        self.assertEqual(
            connection.cursor_instance.statements[1],
            (
                "SELECT set_config('statement_timeout', %s, true)",
                ("1250",),
            ),
        )
        bounded_sql = connection.cursor_instance.statements[2][0]
        self.assertIn("AS zepic_bounded_result LIMIT 3", bounded_sql)

    def test_reports_an_untruncated_result(self):
        connection = FakeConnection([(1,)])
        result = QueryEngine(lambda: connection, max_rows=2).execute(
            "SELECT order_id FROM zepic_analytics.fct_orders"
        )

        self.assertFalse(result.truncated)
        self.assertEqual(result.rows, ((1,),))

    def test_rejects_invalid_resource_limits(self):
        with self.assertRaises(ValueError):
            QueryEngine(lambda: None, statement_timeout_ms=0)
        with self.assertRaises(ValueError):
            QueryEngine(lambda: None, max_rows=1_001)


if __name__ == "__main__":
    unittest.main()
