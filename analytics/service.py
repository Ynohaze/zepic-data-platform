"""Single-turn orchestration for governed AI analytics."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from analytics.llm import AnalyticsLlm
from analytics.query_engine import QueryEngine
from analytics.query_guard import DEFAULT_CONTEXT_PATH


MAX_QUESTION_LENGTH = 2_000


class InvalidQuestion(ValueError):
    """Raised before any LLM or database call for an invalid question."""


@dataclass(frozen=True)
class AnalysisResponse:
    question: str
    answer: str
    generated_sql: str
    source_models: tuple[str, ...]
    columns: tuple[str, ...]
    rows: tuple[dict[str, Any], ...]
    row_count: int
    truncated: bool
    query_elapsed_ms: int
    result_timestamp: datetime
    limitations: tuple[str, ...]


class AnalyticsService:
    """Run exactly one proposal, one guarded query and one grounded answer."""

    def __init__(
        self,
        *,
        llm: AnalyticsLlm,
        query_engine: QueryEngine,
        context_path: Path = DEFAULT_CONTEXT_PATH,
    ):
        self.llm = llm
        self.query_engine = query_engine
        self.semantic_context = json.loads(context_path.read_text())

    def ask(self, question: str) -> AnalysisResponse:
        normalized_question = " ".join(question.split())
        if not normalized_question:
            raise InvalidQuestion("The question cannot be empty.")
        if len(normalized_question) > MAX_QUESTION_LENGTH:
            raise InvalidQuestion(
                f"The question cannot exceed {MAX_QUESTION_LENGTH} characters."
            )

        proposal = self.llm.propose_sql(
            question=normalized_question,
            semantic_context=self.semantic_context,
        )
        result = self.query_engine.execute(proposal.sql)
        answer = self.llm.answer(
            question=normalized_question,
            query_result=result,
            semantic_context=self.semantic_context,
        ).strip()
        if not answer:
            raise ValueError("The LLM returned an empty answer.")

        rows = tuple(
            dict(zip(result.columns, row, strict=True)) for row in result.rows
        )
        return AnalysisResponse(
            question=normalized_question,
            answer=answer,
            generated_sql=result.executed_sql,
            source_models=result.source_models,
            columns=result.columns,
            rows=rows,
            row_count=len(rows),
            truncated=result.truncated,
            query_elapsed_ms=result.elapsed_ms,
            result_timestamp=datetime.now(timezone.utc),
            limitations=tuple(self.semantic_context["global_limitations"]),
        )
