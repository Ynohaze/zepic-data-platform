from datetime import datetime, timezone
from decimal import Decimal
import unittest

from analytics.llm import QuestionUnsupported, SqlProposal
from analytics.query_engine import QueryResult
from analytics.service import AnalyticsService, InvalidQuestion


class FakeLlm:
    def __init__(self, sql="SELECT COUNT(*) AS order_count FROM zepic_analytics.fct_orders"):
        self.sql = sql
        self.calls = []

    def propose_sql(self, *, question, semantic_context):
        self.calls.append(("propose", question, semantic_context["version"]))
        return SqlProposal(self.sql)

    def answer(self, *, question, query_result, semantic_context):
        self.calls.append(("answer", question, query_result.rows))
        return "There are 3 current orders."


class FakeEngine:
    def __init__(self):
        self.sql = None

    def execute(self, sql):
        self.sql = sql
        return QueryResult(
            columns=("order_count", "booked_order_value", "as_of"),
            rows=((3, Decimal("60.00"), datetime(2026, 2, 3, tzinfo=timezone.utc)),),
            source_models=("fct_orders",),
            executed_sql="SELECT COUNT(*) AS order_count FROM zepic_analytics.fct_orders",
            elapsed_ms=4,
            truncated=False,
        )


class UnsupportedLlm(FakeLlm):
    def propose_sql(self, *, question, semantic_context):
        raise QuestionUnsupported("Historical inventory is not available.")


class AnalyticsServiceTests(unittest.TestCase):
    def test_runs_one_proposal_query_and_grounded_answer(self):
        llm = FakeLlm()
        engine = FakeEngine()
        service = AnalyticsService(llm=llm, query_engine=engine)

        result = service.ask("  How many   orders are there? ")

        self.assertEqual(result.question, "How many orders are there?")
        self.assertEqual(result.answer, "There are 3 current orders.")
        self.assertEqual(result.source_models, ("fct_orders",))
        self.assertEqual(result.row_count, 1)
        self.assertEqual(result.rows[0]["booked_order_value"], Decimal("60.00"))
        self.assertEqual(len(llm.calls), 2)
        self.assertEqual(engine.sql, llm.sql)
        self.assertTrue(result.limitations)

    def test_invalid_question_stops_before_llm_and_database(self):
        llm = FakeLlm()
        engine = FakeEngine()
        service = AnalyticsService(llm=llm, query_engine=engine)

        with self.assertRaises(InvalidQuestion):
            service.ask("   ")

        self.assertEqual(llm.calls, [])
        self.assertIsNone(engine.sql)

    def test_unsupported_question_does_not_execute_a_query(self):
        engine = FakeEngine()
        service = AnalyticsService(llm=UnsupportedLlm(), query_engine=engine)

        with self.assertRaises(QuestionUnsupported):
            service.ask("Show the daily inventory trend.")

        self.assertIsNone(engine.sql)


if __name__ == "__main__":
    unittest.main()
