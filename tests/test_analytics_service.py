from datetime import datetime, timezone
from decimal import Decimal
import unittest

from analytics.llm import AgentAction, QuestionUnsupported, SqlProposal
from analytics.query_engine import QueryResult
from analytics.query_guard import QueryRejected
from analytics.service import AgentRunRefused, AnalyticsService, InvalidQuestion


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


class FakeAgentLlm(FakeLlm):
    def __init__(self, actions):
        super().__init__()
        self.actions = iter(actions)
        self.agent_calls = []

    def next_agent_action(
        self, *, question, evidence, semantic_context, remaining_steps, guard_feedback
    ):
        self.agent_calls.append(
            (question, len(evidence), remaining_steps, guard_feedback)
        )
        return next(self.actions)

    def answer_agent(self, *, question, evidence, semantic_context):
        self.agent_calls.append(("answer", len(evidence)))
        return "The bounded evidence supports the final conclusion."


def query_action(sql, objective="Measure the requested outcome"):
    return AgentAction(
        action="query",
        objective=objective,
        sql=sql,
        answer="",
        reason="Useful next step.",
    )


def finish_action(answer="The analysis is complete."):
    return AgentAction(
        action="finish",
        objective="",
        sql="",
        answer=answer,
        reason="Enough evidence.",
    )


def refusal_action(reason="The evidence cannot support the requested conclusion."):
    return AgentAction(
        action="refusal",
        objective="",
        sql="",
        answer="",
        reason=reason,
    )


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

    def test_agent_selects_multiple_queries_then_finishes_from_evidence(self):
        first_sql = "SELECT COUNT(*) AS order_count FROM zepic_analytics.fct_orders"
        second_sql = (
            "SELECT SUM(order_total_amount) AS value "
            "FROM zepic_analytics.fct_orders"
        )
        llm = FakeAgentLlm([
            query_action(first_sql, "Count orders"),
            query_action(second_sql, "Measure value"),
            finish_action("There are 3 orders with supporting value evidence."),
        ])
        engine = FakeEngine()
        service = AnalyticsService(llm=llm, query_engine=engine)

        result = service.run_agent("Investigate orders and value.")

        self.assertEqual(
            result.answer,
            "The bounded evidence supports the final conclusion.",
        )
        self.assertEqual(len(result.steps), 2)
        self.assertEqual(result.steps[0].objective, "Count orders")
        self.assertEqual(result.stop_reason, "model_finished")
        self.assertEqual(
            [event.event_type for event in result.events],
            [
                "query_planned",
                "query_executed",
                "query_planned",
                "query_executed",
                "model_finished",
                "answer_synthesized",
            ],
        )
        self.assertEqual(llm.agent_calls[:3], [
            ("Investigate orders and value.", 0, 3, ""),
            ("Investigate orders and value.", 1, 2, ""),
            ("Investigate orders and value.", 2, 1, ""),
        ])
        self.assertEqual(llm.agent_calls[3], ("answer", 2))

    def test_agent_step_limit_forces_grounded_synthesis(self):
        llm = FakeAgentLlm([query_action(
            "SELECT COUNT(*) AS order_count FROM zepic_analytics.fct_orders"
        )])
        service = AnalyticsService(
            llm=llm,
            query_engine=FakeEngine(),
            max_agent_steps=1,
        )

        result = service.run_agent("Investigate current orders.")

        self.assertEqual(result.stop_reason, "step_limit")
        self.assertEqual(len(result.steps), 1)
        self.assertEqual(llm.agent_calls[-1], ("answer", 1))
        self.assertEqual(
            [event.event_type for event in result.events[-2:]],
            ["step_limit", "answer_synthesized"],
        )
        self.assertIn("hard limit", result.events[-2].detail)

    def test_agent_refusal_after_evidence_preserves_visible_event_trail(self):
        llm = FakeAgentLlm([
            query_action(
                "SELECT COUNT(*) AS order_count FROM zepic_analytics.fct_orders",
                "Check available evidence",
            ),
            refusal_action("The returned evidence cannot support a causal claim."),
        ])
        service = AnalyticsService(llm=llm, query_engine=FakeEngine())

        with self.assertRaises(AgentRunRefused) as raised:
            service.run_agent("Determine the cause of order volume.")

        self.assertEqual(
            [event.event_type for event in raised.exception.events],
            ["query_planned", "query_executed", "model_refused"],
        )
        self.assertEqual(raised.exception.events[-1].status, "stopped")

    def test_agent_allows_one_guarded_plan_repair_without_spending_query_budget(self):
        class RejectOnceEngine(FakeEngine):
            def __init__(self):
                super().__init__()
                self.attempts = 0

            def execute(self, sql):
                self.attempts += 1
                if self.attempts == 1:
                    raise QueryRejected(
                        "schema_access",
                        "Relations must be explicitly qualified.",
                    )
                return super().execute(sql)

        invalid_sql = "SELECT COUNT(*) FROM fct_orders"
        valid_sql = "SELECT COUNT(*) FROM zepic_analytics.fct_orders"
        llm = FakeAgentLlm([
            query_action(invalid_sql),
            query_action(valid_sql),
            finish_action(),
        ])
        engine = RejectOnceEngine()
        service = AnalyticsService(llm=llm, query_engine=engine)

        result = service.run_agent("Investigate orders.")

        self.assertEqual(len(result.steps), 1)
        self.assertEqual(engine.attempts, 2)
        self.assertIn("schema_access", llm.agent_calls[1][3])
        self.assertEqual(llm.agent_calls[1][2], 3)
        self.assertEqual(
            [event.event_type for event in result.events],
            [
                "query_planned",
                "guard_rejected",
                "query_revised",
                "query_executed",
                "model_finished",
                "answer_synthesized",
            ],
        )
        self.assertIn("query was not executed", result.events[1].detail)

    def test_agent_stops_after_two_rejected_plan_repairs(self):
        class RejectingEngine(FakeEngine):
            def __init__(self):
                super().__init__()
                self.attempts = 0

            def execute(self, sql):
                self.attempts += 1
                raise QueryRejected("schema_access", "Use the approved schema.")

        llm = FakeAgentLlm([
            query_action("SELECT COUNT(*) FROM first_table"),
            query_action("SELECT COUNT(*) FROM second_table"),
            query_action("SELECT COUNT(*) FROM third_table"),
        ])
        engine = RejectingEngine()
        service = AnalyticsService(llm=llm, query_engine=engine)

        with self.assertRaises(QueryRejected):
            service.run_agent("Investigate orders.")

        self.assertEqual(engine.attempts, 3)
        self.assertEqual(len(llm.agent_calls), 3)

    def test_agent_rejects_repeated_query(self):
        sql = "SELECT COUNT(*) AS order_count FROM zepic_analytics.fct_orders"
        llm = FakeAgentLlm([query_action(sql), query_action(f"  {sql}  ")])
        service = AnalyticsService(llm=llm, query_engine=FakeEngine())

        with self.assertRaises(QueryRejected) as raised:
            service.run_agent("Keep checking the same count.")

        self.assertEqual(raised.exception.code, "repeated_agent_query")


if __name__ == "__main__":
    unittest.main()
