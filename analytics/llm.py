"""Provider-neutral contracts for governed analytical LLM workflows."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from analytics.query_engine import QueryResult


@dataclass(frozen=True)
class SqlProposal:
    sql: str


@dataclass(frozen=True)
class AgentAction:
    """One model-selected action inside the bounded analytical loop."""

    action: str
    objective: str
    sql: str
    answer: str
    reason: str


@dataclass(frozen=True)
class AgentEvidence:
    """Evidence returned by one previously executed agent query."""

    objective: str
    query_result: QueryResult


class QuestionUnsupported(ValueError):
    """Raised when the available Gold data cannot support a question."""


class LlmProviderError(RuntimeError):
    """Raised when the configured model provider fails."""


class AnalyticsLlm(Protocol):
    """Provider contract for quick answers and bounded analysis tasks."""

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

    def next_agent_action(
        self,
        *,
        question: str,
        evidence: tuple[AgentEvidence, ...],
        semantic_context: dict[str, Any],
        remaining_steps: int,
        guard_feedback: str,
    ) -> AgentAction:
        """Choose one query, finish from evidence, or refuse the task."""

    def answer_agent(
        self,
        *,
        question: str,
        evidence: tuple[AgentEvidence, ...],
        semantic_context: dict[str, Any],
    ) -> str:
        """Synthesize a final answer after the hard query-step limit."""
