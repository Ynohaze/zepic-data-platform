import unittest
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import HTTPException

from analytics.query_engine import QueryResult
from analytics.query_guard import QueryRejected
from src.analytics_api import AnalysisRequest, create_app
from tests.test_analytics_service import (
    FakeAgentLlm,
    FakeEngine,
    FakeLlm,
    finish_action,
    query_action,
    refusal_action,
)
from analytics.service import AnalyticsService


class AnalyticsApiTests(unittest.TestCase):
    @staticmethod
    def endpoint(app, path):
        return next(route.endpoint for route in app.routes if route.path == path)

    def test_health_reports_when_provider_configuration_is_missing(self):
        app = create_app()
        response = self.endpoint(app, "/health")()

        self.assertEqual(response, {"status": "configuration_required"})

    def test_dashboard_routes_are_registered(self):
        app = create_app()
        redirect = self.endpoint(app, "/")()

        self.assertEqual(redirect.status_code, 307)
        self.assertEqual(redirect.headers["location"], "/dashboard/")
        self.assertTrue(any(route.path == "/dashboard" for route in app.routes))

    def test_query_returns_answer_and_audit_fields(self):
        service = AnalyticsService(llm=FakeLlm(), query_engine=FakeEngine())
        app = create_app(service)

        response = self.endpoint(app, "/api/chat/query")(
            AnalysisRequest(question="How many orders are there?")
        )

        body = response.model_dump(mode="json")
        self.assertEqual(body["answer"], "There are 3 current orders.")
        self.assertEqual(body["source_models"], ["fct_orders"])
        self.assertEqual(body["row_count"], 1)
        self.assertEqual(body["rows"][0]["booked_order_value"], "60.00")
        self.assertIn("generated_sql", body)
        self.assertIn("result_timestamp", body)
        self.assertEqual(body["model"], "FakeLlm")
        self.assertEqual(len(body["request_id"]), 12)
        self.assertGreaterEqual(body["total_elapsed_ms"], 0)

    def test_agent_returns_bounded_steps_and_aggregate_audit_fields(self):
        llm = FakeAgentLlm([
            query_action(
                "SELECT COUNT(*) AS order_count FROM zepic_analytics.fct_orders",
                "Count current orders",
            ),
            finish_action("The current order evidence is sufficient."),
        ])
        service = AnalyticsService(llm=llm, query_engine=FakeEngine())
        app = create_app(service)

        response = self.endpoint(app, "/api/agent/run")(
            AnalysisRequest(question="Investigate current orders.")
        )

        body = response.model_dump(mode="json")
        self.assertEqual(
            body["answer"],
            "The bounded evidence supports the final conclusion.",
        )
        self.assertEqual(body["query_count"], 1)
        self.assertEqual(body["total_rows"], 1)
        self.assertEqual(body["steps"][0]["objective"], "Count current orders")
        self.assertEqual(body["steps"][0]["source_models"], ["fct_orders"])
        self.assertEqual(body["events"][0]["event_type"], "query_planned")
        self.assertEqual(body["events"][-1]["event_type"], "answer_synthesized")
        self.assertEqual(
            [event["sequence"] for event in body["events"]],
            list(range(1, len(body["events"]) + 1)),
        )
        self.assertEqual(body["stop_reason"], "model_finished")
        self.assertEqual(len(body["request_id"]), 12)

    def test_agent_guard_failure_returns_visible_events(self):
        class RejectingEngine(FakeEngine):
            def execute(self, sql):
                raise QueryRejected(
                    "function_access",
                    "Function PERCENTILE_CONT is not approved.",
                )

        llm = FakeAgentLlm([
            query_action("SELECT first_attempt FROM zepic_analytics.fct_orders"),
            query_action("SELECT second_attempt FROM zepic_analytics.fct_orders"),
            query_action("SELECT third_attempt FROM zepic_analytics.fct_orders"),
        ])
        app = create_app(AnalyticsService(llm=llm, query_engine=RejectingEngine()))

        with self.assertRaises(HTTPException) as raised:
            self.endpoint(app, "/api/agent/run")(
                AnalysisRequest(question="Segment valuable customers.")
            )

        self.assertEqual(raised.exception.status_code, 422)
        events = raised.exception.detail["events"]
        self.assertEqual(events[-1]["event_type"], "guard_rejected")
        self.assertEqual(events[-1]["status"], "stopped")
        self.assertEqual(
            [event["event_type"] for event in events].count("guard_rejected"),
            3,
        )

    def test_agent_refusal_after_evidence_returns_visible_events(self):
        llm = FakeAgentLlm([
            query_action(
                "SELECT COUNT(*) AS order_count FROM zepic_analytics.fct_orders",
                "Inspect available evidence",
            ),
            refusal_action("The evidence cannot support the requested cause."),
        ])
        app = create_app(AnalyticsService(llm=llm, query_engine=FakeEngine()))

        with self.assertRaises(HTTPException) as raised:
            self.endpoint(app, "/api/agent/run")(
                AnalysisRequest(question="Explain the cause of order volume.")
            )

        self.assertEqual(raised.exception.status_code, 422)
        self.assertEqual(
            [event["event_type"] for event in raised.exception.detail["events"]],
            ["query_planned", "query_executed", "model_refused"],
        )

    def test_dashboard_summary_uses_deterministic_query_engine(self):
        class SummaryEngine:
            def execute(self, sql):
                return QueryResult(
                    columns=(
                        "total_orders",
                        "booked_order_value",
                        "produced_units",
                        "inventory_units",
                        "latest_order_date",
                        "inventory_updated_at",
                    ),
                    rows=((
                        3,
                        Decimal("60.00"),
                        45,
                        20,
                        "2026-02-03",
                        datetime(2026, 2, 3, tzinfo=timezone.utc),
                    ),),
                    source_models=(
                        "current_inventory",
                        "fct_orders",
                        "fct_production_runs",
                    ),
                    executed_sql=sql,
                    elapsed_ms=2,
                    truncated=False,
                )

        app = create_app(dashboard_query_engine=SummaryEngine())

        response = self.endpoint(app, "/api/dashboard/summary")()

        self.assertEqual(response.total_orders, 3)
        self.assertEqual(response.booked_order_value, "60.00")
        self.assertEqual(response.produced_units, 45)
        self.assertEqual(response.inventory_units, 20)
        self.assertEqual(response.latest_order_date, "2026-02-03")

    def test_dashboard_overview_returns_deterministic_chart_series(self):
        class OverviewEngine:
            @staticmethod
            def result(columns, rows, sql):
                return QueryResult(
                    columns=columns,
                    rows=rows,
                    source_models=("fct_orders",),
                    executed_sql=sql,
                    elapsed_ms=2,
                    truncated=False,
                )

            def execute(self, sql):
                if "GROUP BY order_date" in sql:
                    return self.result(
                        ("order_date", "order_count", "booked_order_value"),
                        (("2026-01-02", 2, Decimal("40.00")),),
                        sql,
                    )
                if "GROUP BY order_status" in sql:
                    return self.result(
                        ("order_status", "order_count", "booked_order_value"),
                        (("shipped", 2, Decimal("40.00")),),
                        sql,
                    )
                if "GROUP BY planned_start_date" in sql:
                    return self.result(
                        (
                            "planned_start_date",
                            "planned_quantity",
                            "actual_quantity",
                            "quantity_variance",
                        ),
                        (("2026-01-02", 10, 11, 1),),
                        sql,
                    )
                if "ORDER BY available_quantity" in sql:
                    return self.result(
                        (
                            "sku",
                            "product_name",
                            "available_quantity",
                            "inventory_retail_value",
                        ),
                        (("SKU-1", "Widget", 5, Decimal("25.00")),),
                        sql,
                    )
                return self.result(
                    (
                        "total_orders",
                        "booked_order_value",
                        "produced_units",
                        "inventory_units",
                        "latest_order_date",
                        "inventory_updated_at",
                    ),
                    ((
                        2,
                        Decimal("40.00"),
                        11,
                        5,
                        "2026-01-02",
                        datetime(2026, 1, 2, tzinfo=timezone.utc),
                    ),),
                    sql,
                )

        app = create_app(dashboard_query_engine=OverviewEngine())

        response = self.endpoint(app, "/api/dashboard/overview")()

        self.assertEqual(response.summary.total_orders, 2)
        self.assertEqual(response.daily_orders[0].order_count, 2)
        self.assertEqual(response.order_status[0].order_status, "shipped")
        self.assertEqual(response.production[0].quantity_variance, 1)
        self.assertEqual(response.low_inventory[0].sku, "SKU-1")

    def test_query_requires_a_configured_provider(self):
        app = create_app()

        with self.assertRaises(HTTPException) as raised:
            self.endpoint(app, "/api/chat/query")(
                AnalysisRequest(question="How many orders are there?")
            )

        self.assertEqual(raised.exception.status_code, 503)


if __name__ == "__main__":
    unittest.main()
