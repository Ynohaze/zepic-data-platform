from datetime import datetime, timezone
from decimal import Decimal
import json
import unittest
from unittest.mock import patch

from analytics.groq import GroqAnalyticsLlm
from analytics.llm import AgentEvidence, LlmProviderError, QuestionUnsupported
from analytics.query_engine import QueryResult


class RecordingTransport:
    def __init__(self, contents):
        self.contents = iter(contents)
        self.payloads = []

    def __call__(self, payload):
        self.payloads.append(payload)
        content = next(self.contents)
        return {"choices": [{"message": {"content": content}}]}


class GroqAnalyticsLlmTests(unittest.TestCase):
    def test_http_transport_identifies_the_application(self):
        provider = GroqAnalyticsLlm(api_key="test-key")

        with patch("analytics.groq.urlopen") as mocked_urlopen:
            response = mocked_urlopen.return_value.__enter__.return_value
            response.read.return_value = b'{"choices": []}'

            provider._request({"model": "test-model"})

        request = mocked_urlopen.call_args.args[0]
        headers = {name.lower(): value for name, value in request.header_items()}
        self.assertEqual(headers["accept"], "application/json")
        self.assertEqual(headers["user-agent"], "zepic-data-platform/0.1")

    def test_proposes_sql_with_strict_schema_and_low_reasoning(self):
        transport = RecordingTransport([
            json.dumps({
                "status": "query",
                "sql": "SELECT COUNT(*) FROM zepic_analytics.fct_orders",
                "reason": "Counts current orders.",
            })
        ])
        provider = GroqAnalyticsLlm(api_key="test-key", transport=transport)

        proposal = provider.propose_sql(
            question="How many orders are there?",
            semantic_context={
                "version": 1,
                "tables": {"fct_orders": {"columns": ["order_id"]}},
            },
        )

        self.assertEqual(
            proposal.sql,
            "SELECT COUNT(*) FROM zepic_analytics.fct_orders",
        )
        payload = transport.payloads[0]
        self.assertEqual(payload["model"], "openai/gpt-oss-20b")
        self.assertEqual(payload["reasoning_effort"], "low")
        self.assertTrue(payload["response_format"]["json_schema"]["strict"])
        self.assertNotIn("test-key", json.dumps(payload))
        self.assertIn("first segment customers", payload["messages"][0]["content"])
        self.assertIn("top quartile", payload["messages"][0]["content"])

    def test_refusal_never_returns_sql_for_execution(self):
        transport = RecordingTransport([
            json.dumps({
                "status": "refusal",
                "sql": "",
                "reason": "Historical inventory snapshots are unavailable.",
            })
        ])
        provider = GroqAnalyticsLlm(api_key="test-key", transport=transport)

        with self.assertRaises(QuestionUnsupported):
            provider.propose_sql(
                question="Show inventory history.",
                semantic_context={"version": 1, "tables": {}},
            )

    def test_answer_serializes_database_values_and_uses_only_query_evidence(self):
        transport = RecordingTransport([
            json.dumps({"answer": "Booked order value is 60.00."})
        ])
        provider = GroqAnalyticsLlm(api_key="test-key", transport=transport)
        result = QueryResult(
            columns=("booked_order_value", "as_of"),
            rows=((Decimal("60.00"), datetime(2026, 2, 3, tzinfo=timezone.utc)),),
            source_models=("fct_orders",),
            executed_sql="SELECT SUM(order_total_amount) FROM zepic_analytics.fct_orders",
            elapsed_ms=3,
            truncated=False,
        )

        answer = provider.answer(
            question="What is booked order value?",
            query_result=result,
            semantic_context={"global_limitations": ["Not CDC."]},
        )

        self.assertEqual(answer, "Booked order value is 60.00.")
        evidence_prompt = transport.payloads[0]["messages"][1]["content"]
        self.assertIn('"60.00"', evidence_prompt)
        self.assertIn("2026-02-03T00:00:00+00:00", evidence_prompt)

    def test_agent_action_uses_prior_evidence_and_strict_step_budget(self):
        transport = RecordingTransport([
            json.dumps({
                "action": "finish",
                "objective": "",
                "sql": "",
                "answer": "The evidence is sufficient.",
                "reason": "The requested totals are present.",
            })
        ])
        provider = GroqAnalyticsLlm(api_key="test-key", transport=transport)
        result = QueryResult(
            columns=("order_count",),
            rows=((3,),),
            source_models=("fct_orders",),
            executed_sql="SELECT COUNT(*) FROM zepic_analytics.fct_orders",
            elapsed_ms=2,
            truncated=False,
        )

        action = provider.next_agent_action(
            question="Investigate orders.",
            evidence=(AgentEvidence("Count orders", result),),
            semantic_context={"version": 1, "tables": {}, "global_limitations": []},
            remaining_steps=2,
            guard_feedback="schema_access",
        )

        self.assertEqual(action.action, "finish")
        self.assertEqual(action.answer, "The evidence is sufficient.")
        payload = transport.payloads[0]
        self.assertIn("REMAINING_QUERY_STEPS=2", payload["messages"][0]["content"])
        self.assertIn("do not prove stock coverage", payload["messages"][0]["content"])
        self.assertIn("line_amount alone", payload["messages"][0]["content"])
        self.assertIn('"rows":[[3]]', payload["messages"][1]["content"])
        self.assertIn("GUARD_FEEDBACK=schema_access", payload["messages"][1]["content"])
        self.assertTrue(payload["response_format"]["json_schema"]["strict"])

    def test_agent_step_limit_answer_uses_all_evidence(self):
        transport = RecordingTransport([
            json.dumps({"answer": "Three orders were observed."})
        ])
        provider = GroqAnalyticsLlm(api_key="test-key", transport=transport)
        result = QueryResult(
            columns=("order_count",),
            rows=((3,),),
            source_models=("fct_orders",),
            executed_sql="SELECT COUNT(*) FROM zepic_analytics.fct_orders",
            elapsed_ms=2,
            truncated=False,
        )

        answer = provider.answer_agent(
            question="Investigate orders.",
            evidence=(AgentEvidence("Count orders", result),),
            semantic_context={"global_limitations": ["Current snapshot only."]},
        )

        self.assertEqual(answer, "Three orders were observed.")
        self.assertIn("AGENT_EVIDENCE=", transport.payloads[0]["messages"][1]["content"])

    def test_agent_answer_requires_time_window_caveat_for_orders_and_inventory(self):
        transport = RecordingTransport([
            json.dumps({"answer": "The windows differ, so no stock risk is established."})
        ])
        provider = GroqAnalyticsLlm(api_key="test-key", transport=transport)
        result = QueryResult(
            columns=("units", "inventory"),
            rows=((100, 10),),
            source_models=("current_inventory", "fct_order_lines"),
            executed_sql="SELECT 100 AS units, 10 AS inventory FROM zepic_analytics.current_inventory",
            elapsed_ms=2,
            truncated=False,
        )

        provider.answer_agent(
            question="Compare orders and inventory.",
            evidence=(AgentEvidence("Compare snapshots", result),),
            semantic_context={"global_limitations": ["Current inventory only."]},
        )

        system_prompt = transport.payloads[0]["messages"][0]["content"]
        self.assertIn("time windows differ", system_prompt)
        self.assertIn("Do not make any of those claims", system_prompt)

    def test_invalid_provider_response_is_sanitized(self):
        provider = GroqAnalyticsLlm(
            api_key="test-key",
            transport=RecordingTransport(["not json"]),
        )

        with self.assertRaises(LlmProviderError) as raised:
            provider.propose_sql(
                question="How many orders?",
                semantic_context={"version": 1, "tables": {}},
            )

        self.assertNotIn("test-key", str(raised.exception))

    def test_locally_rejects_structured_payload_with_wrong_fields(self):
        provider = GroqAnalyticsLlm(
            api_key="test-key",
            transport=RecordingTransport([
                json.dumps({"status": "query", "sql": 42, "reason": "bad"})
            ]),
        )

        with self.assertRaises(LlmProviderError):
            provider.propose_sql(
                question="How many orders?",
                semantic_context={"version": 1, "tables": {}},
            )


if __name__ == "__main__":
    unittest.main()
