from datetime import datetime, timezone
from decimal import Decimal
import json
import unittest
from unittest.mock import patch

from analytics.groq import GroqAnalyticsLlm
from analytics.llm import LlmProviderError, QuestionUnsupported
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
