"""FastAPI surface for the governed Level 1 analytics service."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv
from psycopg import Error as PsycopgError
from pydantic import BaseModel, Field

from analytics.llm import LlmProviderError, QuestionUnsupported
from analytics.database import connect_analytics_database
from analytics.groq import DEFAULT_MODEL, GroqAnalyticsLlm
from analytics.query_engine import QueryEngine
from analytics.query_guard import QueryRejected
from analytics.service import AnalyticsService, InvalidQuestion


ROOT = Path(__file__).resolve().parents[1]
DASHBOARD_DIR = ROOT / "dashboard"
LOGGER = logging.getLogger("zepic.analytics")


class AnalysisRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2_000)


class AnalysisResult(BaseModel):
    request_id: str
    model: str
    question: str
    answer: str
    generated_sql: str
    source_models: list[str]
    columns: list[str]
    rows: list[dict[str, Any]]
    row_count: int
    truncated: bool
    query_elapsed_ms: int
    total_elapsed_ms: int
    result_timestamp: str
    limitations: list[str]


class AgentStepResult(BaseModel):
    step_number: int
    objective: str
    generated_sql: str
    source_models: list[str]
    columns: list[str]
    rows: list[dict[str, Any]]
    row_count: int
    truncated: bool
    query_elapsed_ms: int


class AgentEventResult(BaseModel):
    sequence: int
    event_type: str
    title: str
    detail: str
    status: str


class AgentAnalysisResult(BaseModel):
    request_id: str
    model: str
    question: str
    answer: str
    steps: list[AgentStepResult]
    events: list[AgentEventResult]
    query_count: int
    total_rows: int
    total_query_elapsed_ms: int
    total_elapsed_ms: int
    stop_reason: str
    result_timestamp: str
    limitations: list[str]


class DashboardSummary(BaseModel):
    total_orders: int
    booked_order_value: str
    produced_units: int
    inventory_units: int
    latest_order_date: str | None
    inventory_updated_at: str | None
    result_timestamp: str


class DailyOrdersPoint(BaseModel):
    order_date: str
    order_count: int
    booked_order_value: str


class OrderStatusPoint(BaseModel):
    order_status: str
    order_count: int
    booked_order_value: str


class ProductionPoint(BaseModel):
    planned_start_date: str
    planned_quantity: int
    actual_quantity: int
    quantity_variance: int


class InventoryPoint(BaseModel):
    sku: str
    product_name: str
    available_quantity: int
    inventory_retail_value: str


class DashboardOverview(BaseModel):
    summary: DashboardSummary
    daily_orders: list[DailyOrdersPoint]
    order_status: list[OrderStatusPoint]
    production: list[ProductionPoint]
    low_inventory: list[InventoryPoint]


def serialize_agent_events(events) -> list[dict[str, Any]]:
    return [
        {
            "sequence": event.sequence,
            "event_type": event.event_type,
            "title": event.title,
            "detail": event.detail,
            "status": event.status,
        }
        for event in events
    ]


def build_query_engine_from_env() -> QueryEngine | None:
    required = ("POSTGRES_DB", "ANALYTICS_DB_USER", "ANALYTICS_DB_PASSWORD")
    if any(not os.getenv(name) for name in required):
        return None

    try:
        statement_timeout_ms = int(os.getenv("ANALYTICS_QUERY_TIMEOUT_MS", "5000"))
        max_rows = int(os.getenv("ANALYTICS_MAX_ROWS", "200"))
    except ValueError as exc:
        raise RuntimeError("Analytics query settings must be integers.") from exc

    return QueryEngine(
        connect_analytics_database,
        statement_timeout_ms=statement_timeout_ms,
        max_rows=max_rows,
    )


def build_service_from_env(
    query_engine: QueryEngine | None = None,
) -> AnalyticsService | None:
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        return None

    try:
        provider_timeout = int(os.getenv("GROQ_REQUEST_TIMEOUT_SECONDS", "30"))
        max_agent_steps = int(os.getenv("ANALYTICS_AGENT_MAX_STEPS", "3"))
    except ValueError as exc:
        raise RuntimeError("Analytics agent and provider settings must be integers.") from exc

    configured_query_engine = query_engine or build_query_engine_from_env()
    if configured_query_engine is None:
        return None

    llm = GroqAnalyticsLlm(
        api_key=api_key,
        model=os.getenv("GROQ_MODEL", DEFAULT_MODEL),
        request_timeout_seconds=provider_timeout,
    )
    return AnalyticsService(
        llm=llm,
        query_engine=configured_query_engine,
        max_agent_steps=max_agent_steps,
    )


def create_app(
    service: AnalyticsService | None = None,
    dashboard_query_engine: QueryEngine | None = None,
) -> FastAPI:
    app = FastAPI(title="Zepic governed analytics API", version="0.2.0")
    app.state.analytics_service = service
    app.state.dashboard_query_engine = (
        dashboard_query_engine
        or (service.query_engine if service is not None else None)
    )

    app.mount(
        "/dashboard",
        StaticFiles(directory=DASHBOARD_DIR, html=True),
        name="dashboard",
    )

    @app.get("/", include_in_schema=False)
    def dashboard_redirect() -> RedirectResponse:
        return RedirectResponse(url="/dashboard/")

    @app.get("/health")
    def health() -> dict[str, str]:
        status = "ready" if app.state.analytics_service is not None else "configuration_required"
        return {"status": status}

    @app.get("/api/dashboard/summary", response_model=DashboardSummary)
    def dashboard_summary() -> DashboardSummary:
        query_engine = app.state.dashboard_query_engine
        if query_engine is None:
            raise HTTPException(
                status_code=503,
                detail="The analytics database is not configured.",
            )

        result = query_engine.execute(
            """
            SELECT
                COUNT(*) AS total_orders,
                COALESCE(SUM(order_total_amount), 0) AS booked_order_value,
                (
                    SELECT COALESCE(SUM(actual_quantity), 0)
                    FROM zepic_analytics.fct_production_runs
                ) AS produced_units,
                (
                    SELECT COALESCE(SUM(available_quantity), 0)
                    FROM zepic_analytics.current_inventory
                ) AS inventory_units,
                MAX(order_date) AS latest_order_date,
                (
                    SELECT MAX(inventory_updated_at)
                    FROM zepic_analytics.current_inventory
                ) AS inventory_updated_at
            FROM zepic_analytics.fct_orders
            """
        )
        row = dict(zip(result.columns, result.rows[0], strict=True))
        return DashboardSummary(
            total_orders=int(row["total_orders"]),
            booked_order_value=str(row["booked_order_value"]),
            produced_units=int(row["produced_units"]),
            inventory_units=int(row["inventory_units"]),
            latest_order_date=(
                str(row["latest_order_date"])
                if row["latest_order_date"] is not None
                else None
            ),
            inventory_updated_at=(
                row["inventory_updated_at"].isoformat()
                if row["inventory_updated_at"] is not None
                else None
            ),
            result_timestamp=datetime.now(timezone.utc).isoformat(),
        )

    @app.get("/api/dashboard/overview", response_model=DashboardOverview)
    def dashboard_overview() -> DashboardOverview:
        query_engine = app.state.dashboard_query_engine
        if query_engine is None:
            raise HTTPException(
                status_code=503,
                detail="The analytics database is not configured.",
            )

        daily = query_engine.execute(
            """
            SELECT
                order_date,
                COUNT(*) AS order_count,
                COALESCE(SUM(order_total_amount), 0) AS booked_order_value
            FROM zepic_analytics.fct_orders
            GROUP BY order_date
            ORDER BY order_date
            """
        )
        statuses = query_engine.execute(
            """
            SELECT
                order_status,
                COUNT(*) AS order_count,
                COALESCE(SUM(order_total_amount), 0) AS booked_order_value
            FROM zepic_analytics.fct_orders
            GROUP BY order_status
            ORDER BY order_count DESC, order_status
            """
        )
        production = query_engine.execute(
            """
            SELECT
                planned_start_date,
                COALESCE(SUM(planned_quantity), 0) AS planned_quantity,
                COALESCE(SUM(actual_quantity), 0) AS actual_quantity,
                COALESCE(SUM(quantity_variance), 0) AS quantity_variance
            FROM zepic_analytics.fct_production_runs
            GROUP BY planned_start_date
            ORDER BY planned_start_date
            """
        )
        inventory = query_engine.execute(
            """
            SELECT
                sku,
                product_name,
                available_quantity,
                inventory_retail_value
            FROM zepic_analytics.current_inventory
            ORDER BY available_quantity, product_name
            LIMIT 8
            """
        )

        def records(result):
            return [
                dict(zip(result.columns, row, strict=True))
                for row in result.rows
            ]

        return DashboardOverview(
            summary=dashboard_summary(),
            daily_orders=[
                DailyOrdersPoint(
                    order_date=str(row["order_date"]),
                    order_count=int(row["order_count"]),
                    booked_order_value=str(row["booked_order_value"]),
                )
                for row in records(daily)
            ],
            order_status=[
                OrderStatusPoint(
                    order_status=str(row["order_status"]),
                    order_count=int(row["order_count"]),
                    booked_order_value=str(row["booked_order_value"]),
                )
                for row in records(statuses)
            ],
            production=[
                ProductionPoint(
                    planned_start_date=str(row["planned_start_date"]),
                    planned_quantity=int(row["planned_quantity"]),
                    actual_quantity=int(row["actual_quantity"]),
                    quantity_variance=int(row["quantity_variance"]),
                )
                for row in records(production)
            ],
            low_inventory=[
                InventoryPoint(
                    sku=str(row["sku"]),
                    product_name=str(row["product_name"]),
                    available_quantity=int(row["available_quantity"]),
                    inventory_retail_value=str(row["inventory_retail_value"]),
                )
                for row in records(inventory)
            ],
        )

    @app.post("/api/chat/query", response_model=AnalysisResult)
    def query(request: AnalysisRequest):
        request_id = uuid4().hex[:12]
        started_at = perf_counter()
        configured_service = app.state.analytics_service
        if configured_service is None:
            raise HTTPException(
                status_code=503,
                detail="The analytics LLM provider is not configured.",
            )

        try:
            result = configured_service.ask(request.question)
        except InvalidQuestion as exc:
            LOGGER.info("analytics_request request_id=%s status=invalid", request_id)
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except QuestionUnsupported as exc:
            LOGGER.info("analytics_request request_id=%s status=unsupported", request_id)
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except QueryRejected as exc:
            LOGGER.info(
                "analytics_request request_id=%s status=blocked code=%s",
                request_id,
                exc.code,
            )
            raise HTTPException(
                status_code=422,
                detail={"code": exc.code, "message": str(exc)},
            ) from exc
        except LlmProviderError as exc:
            LOGGER.warning("analytics_request request_id=%s status=provider_error", request_id)
            raise HTTPException(
                status_code=502,
                detail="The configured LLM provider failed.",
            ) from exc
        except PsycopgError as exc:
            LOGGER.warning("analytics_request request_id=%s status=database_error", request_id)
            raise HTTPException(
                status_code=503,
                detail="Analytics is temporarily unavailable.",
            ) from exc

        total_elapsed_ms = int((perf_counter() - started_at) * 1_000)
        model_name = getattr(configured_service.llm, "model", type(configured_service.llm).__name__)
        LOGGER.info(
            "analytics_request request_id=%s status=success model=%s sources=%s "
            "rows=%s query_ms=%s total_ms=%s",
            request_id,
            model_name,
            ",".join(result.source_models),
            result.row_count,
            result.query_elapsed_ms,
            total_elapsed_ms,
        )

        return AnalysisResult(
            request_id=request_id,
            model=model_name,
            question=result.question,
            answer=result.answer,
            generated_sql=result.generated_sql,
            source_models=list(result.source_models),
            columns=list(result.columns),
            rows=list(result.rows),
            row_count=result.row_count,
            truncated=result.truncated,
            query_elapsed_ms=result.query_elapsed_ms,
            total_elapsed_ms=total_elapsed_ms,
            result_timestamp=result.result_timestamp.isoformat(),
            limitations=list(result.limitations),
        )

    @app.post("/api/agent/run", response_model=AgentAnalysisResult)
    def run_agent(request: AnalysisRequest):
        request_id = uuid4().hex[:12]
        started_at = perf_counter()
        configured_service = app.state.analytics_service
        if configured_service is None:
            raise HTTPException(
                status_code=503,
                detail="The analytics LLM provider is not configured.",
            )

        try:
            result = configured_service.run_agent(request.question)
        except InvalidQuestion as exc:
            LOGGER.info("agent_request request_id=%s status=invalid", request_id)
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except QuestionUnsupported as exc:
            LOGGER.info("agent_request request_id=%s status=unsupported", request_id)
            detail: str | dict[str, Any] = str(exc)
            if events := getattr(exc, "events", ()):
                detail = {
                    "code": "question_unsupported",
                    "message": str(exc),
                    "events": serialize_agent_events(events),
                }
            raise HTTPException(status_code=422, detail=detail) from exc
        except QueryRejected as exc:
            LOGGER.info(
                "agent_request request_id=%s status=blocked code=%s",
                request_id,
                exc.code,
            )
            detail: dict[str, Any] = {"code": exc.code, "message": str(exc)}
            if events := getattr(exc, "events", ()):
                detail["events"] = serialize_agent_events(events)
            raise HTTPException(status_code=422, detail=detail) from exc
        except LlmProviderError as exc:
            LOGGER.warning("agent_request request_id=%s status=provider_error", request_id)
            raise HTTPException(
                status_code=502,
                detail="The configured LLM provider failed.",
            ) from exc
        except PsycopgError as exc:
            LOGGER.warning("agent_request request_id=%s status=database_error", request_id)
            raise HTTPException(
                status_code=503,
                detail="Analytics is temporarily unavailable.",
            ) from exc

        total_elapsed_ms = int((perf_counter() - started_at) * 1_000)
        model_name = getattr(
            configured_service.llm,
            "model",
            type(configured_service.llm).__name__,
        )
        total_rows = sum(step.row_count for step in result.steps)
        total_query_elapsed_ms = sum(step.query_elapsed_ms for step in result.steps)
        sources = sorted(
            {source for step in result.steps for source in step.source_models}
        )
        LOGGER.info(
            "agent_request request_id=%s status=success model=%s queries=%s "
            "sources=%s rows=%s query_ms=%s total_ms=%s stop=%s",
            request_id,
            model_name,
            len(result.steps),
            ",".join(sources),
            total_rows,
            total_query_elapsed_ms,
            total_elapsed_ms,
            result.stop_reason,
        )

        return AgentAnalysisResult(
            request_id=request_id,
            model=model_name,
            question=result.question,
            answer=result.answer,
            steps=[
                AgentStepResult(
                    step_number=step.step_number,
                    objective=step.objective,
                    generated_sql=step.generated_sql,
                    source_models=list(step.source_models),
                    columns=list(step.columns),
                    rows=list(step.rows),
                    row_count=step.row_count,
                    truncated=step.truncated,
                    query_elapsed_ms=step.query_elapsed_ms,
                )
                for step in result.steps
            ],
            events=[
                AgentEventResult(
                    sequence=event.sequence,
                    event_type=event.event_type,
                    title=event.title,
                    detail=event.detail,
                    status=event.status,
                )
                for event in result.events
            ],
            query_count=len(result.steps),
            total_rows=total_rows,
            total_query_elapsed_ms=total_query_elapsed_ms,
            total_elapsed_ms=total_elapsed_ms,
            stop_reason=result.stop_reason,
            result_timestamp=result.result_timestamp.isoformat(),
            limitations=list(result.limitations),
        )

    return app


load_dotenv(ROOT / ".env", override=False)
dashboard_query_engine = build_query_engine_from_env()
app = create_app(
    build_service_from_env(dashboard_query_engine),
    dashboard_query_engine,
)
