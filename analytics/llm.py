"""Provider-neutral contracts for the Level 1 analytical LLM."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from analytics.query_engine import QueryResult


@dataclass(frozen=True)
class SqlProposal:
    sql: str


class QuestionUnsupported(ValueError):
    """Raised when the available Gold data cannot support a question."""


class LlmProviderError(RuntimeError):
    """Raised when the configured model provider fails."""


class AnalyticsLlm(Protocol):
    """The only two LLM calls allowed in the Level 1 request path."""

    def propose_sql(
        self,
        *,
        question: str,
        semantic_context: dict[str, Any],
    ) -> SqlProposal:
        """Return one structured SQL proposal or raise QuestionUnsupported."""

    def answer(
        self,
        *,
        question: str,
        query_result: QueryResult,
        semantic_context: dict[str, Any],
    ) -> str:
        """Explain only the executed query result, without new data access."""
