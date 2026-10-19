"""Quick-answer and bounded-agent orchestration for governed AI analytics."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from analytics.llm import (
    AgentEvidence,
    AnalyticsLlm,
    LlmProviderError,
    QuestionUnsupported,
)
from analytics.query_engine import QueryEngine
from analytics.query_guard import DEFAULT_CONTEXT_PATH, QueryRejected


MAX_QUESTION_LENGTH = 2_000
DEFAULT_MAX_AGENT_STEPS = 3
MAX_AGENT_GUARD_REPAIRS = 2


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


@dataclass(frozen=True)
class AgentStep:
    step_number: int
    objective: str
    generated_sql: str
    source_models: tuple[str, ...]
    columns: tuple[str, ...]
    rows: tuple[dict[str, Any], ...]
    row_count: int
    truncated: bool
    query_elapsed_ms: int


@dataclass(frozen=True)
class AgentEvent:
    sequence: int
    event_type: str
    title: str
    detail: str
    status: str


class AgentRunRejected(QueryRejected):
    """A final agent guard rejection with its safe, user-visible event trail."""

    def __init__(self, code: str, message: str, events: tuple[AgentEvent, ...]):
        super().__init__(code, message)
        self.events = events


class AgentRunRefused(QuestionUnsupported):
    """A model refusal with its safe, user-visible event trail."""

    def __init__(self, message: str, events: tuple[AgentEvent, ...]):
        super().__init__(message)
        self.events = events


@dataclass(frozen=True)
class AgentResponse:
    question: str
    answer: str
    steps: tuple[AgentStep, ...]
    events: tuple[AgentEvent, ...]
    stop_reason: str
    result_timestamp: datetime
    limitations: tuple[str, ...]


class AnalyticsService:
    """Run a quick answer or a strictly bounded analytical agent."""

    def __init__(
        self,
        *,
        llm: AnalyticsLlm,
        query_engine: QueryEngine,
        context_path: Path = DEFAULT_CONTEXT_PATH,
        max_agent_steps: int = DEFAULT_MAX_AGENT_STEPS,
    ):
        if not 1 <= max_agent_steps <= 5:
            raise ValueError("max_agent_steps must be between 1 and 5")
        self.llm = llm
        self.query_engine = query_engine
        self.semantic_context = json.loads(context_path.read_text())
        self.max_agent_steps = max_agent_steps

    @staticmethod
    def _normalize_question(question: str) -> str:
        normalized_question = " ".join(question.split())
        if not normalized_question:
            raise InvalidQuestion("The question cannot be empty.")
        if len(normalized_question) > MAX_QUESTION_LENGTH:
            raise InvalidQuestion(
                f"The question cannot exceed {MAX_QUESTION_LENGTH} characters."
            )
        return normalized_question

    @staticmethod
    def _rows(result) -> tuple[dict[str, Any], ...]:
        return tuple(
            dict(zip(result.columns, row, strict=True)) for row in result.rows
        )

    def ask(self, question: str) -> AnalysisResponse:
        normalized_question = self._normalize_question(question)

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

        rows = self._rows(result)
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

    def run_agent(self, question: str) -> AgentResponse:
        """Let the model select up to ``max_agent_steps`` guarded queries."""

        normalized_question = self._normalize_question(question)
        evidence: list[AgentEvidence] = []
        steps: list[AgentStep] = []
        events: list[AgentEvent] = []
        seen_sql: set[str] = set()
        guard_feedback = ""
        guard_repairs_remaining = MAX_AGENT_GUARD_REPAIRS

        while len(steps) < self.max_agent_steps:
            step_number = len(steps) + 1
            is_repair = bool(guard_feedback)
            action = self.llm.next_agent_action(
                question=normalized_question,
                evidence=tuple(evidence),
                semantic_context=self.semantic_context,
                remaining_steps=self.max_agent_steps - len(steps),
                guard_feedback=guard_feedback,
            )

            if action.action == "refusal":
                reason = (
                    action.reason.strip()
                    or "The available Gold data cannot support this analysis task."
                )
                events.append(
                    self._event(
                        events,
                        "model_refused",
                        "Agent refused to conclude",
                        reason,
                        "stopped",
                    )
                )
                raise AgentRunRefused(reason, tuple(events))

            if action.action == "finish":
                if not evidence:
                    raise LlmProviderError(
                        "The agent tried to finish without query evidence."
                    )
                events.append(
                    self._event(
                        events,
                        "model_finished",
                        "Agent chose to finish",
                        (
                            action.reason.strip()
                            or f"The agent judged {len(steps)} evidence set(s) sufficient."
                        ),
                        "complete",
                    )
                )
                answer = self.llm.answer_agent(
                    question=normalized_question,
                    evidence=tuple(evidence),
                    semantic_context=self.semantic_context,
                ).strip()
                if not answer:
                    raise LlmProviderError("The agent returned an empty final answer.")
                events.append(
                    self._event(
                        events,
                        "answer_synthesized",
                        "Synthesized final answer",
                        f"Used {len(evidence)} executed evidence set(s); no new query was run.",
                        "complete",
                    )
                )
                return self._agent_response(
                    normalized_question,
                    answer,
                    steps,
                    events,
                    "model_finished",
                )

            if action.action != "query":
                raise LlmProviderError("The agent returned an unknown action.")

            objective = action.objective.strip()
            sql = action.sql.strip()
            if not objective or not sql:
                raise LlmProviderError("The agent returned an incomplete query action.")
            sql_fingerprint = " ".join(sql.split()).casefold()
            if sql_fingerprint in seen_sql:
                raise QueryRejected(
                    "repeated_agent_query",
                    "The bounded agent attempted to repeat an earlier query.",
                )
            seen_sql.add(sql_fingerprint)
            events.append(
                self._event(
                    events,
                    "query_revised" if is_repair else "query_planned",
                    (
                        f"Revised query {step_number}"
                        if is_repair
                        else f"Planned query {step_number}"
                    ),
                    objective,
                    "revised" if is_repair else "planned",
                )
            )

            try:
                result = self.query_engine.execute(sql)
            except QueryRejected as exc:
                can_repair = guard_repairs_remaining > 0
                repair_number = MAX_AGENT_GUARD_REPAIRS - guard_repairs_remaining + 1
                repair_detail = (
                    f"Correction {repair_number} of {MAX_AGENT_GUARD_REPAIRS} allowed."
                    if can_repair
                    else "No plan corrections remain; the run stopped."
                )
                events.append(
                    self._event(
                        events,
                        "guard_rejected",
                        "SQL guard rejected the plan",
                        (
                            f"{exc.code}: query was not executed. "
                            f"{repair_detail}"
                        ),
                        "correcting" if can_repair else "stopped",
                    )
                )
                if guard_repairs_remaining <= 0:
                    raise AgentRunRejected(
                        exc.code,
                        str(exc),
                        tuple(events),
                    ) from exc
                guard_repairs_remaining -= 1
                guard_feedback = (
                    f"The previous SQL was rejected by the local guard with code "
                    f"{exc.code}: {exc}. Produce a corrected query within the declared "
                    "contract. Every base relation in FROM or JOIN must use the full "
                    "zepic_analytics.<approved_table> name; CTE references are the only "
                    "relations that may be unqualified."
                )
                continue

            guard_feedback = ""
            evidence.append(AgentEvidence(objective=objective, query_result=result))
            rows = self._rows(result)
            steps.append(
                AgentStep(
                    step_number=step_number,
                    objective=objective,
                    generated_sql=result.executed_sql,
                    source_models=result.source_models,
                    columns=result.columns,
                    rows=rows,
                    row_count=len(rows),
                    truncated=result.truncated,
                    query_elapsed_ms=result.elapsed_ms,
                )
            )
            events.append(
                self._event(
                    events,
                    "query_executed",
                    f"Executed query {step_number}",
                    (
                        f"{len(rows)} row(s) in {result.elapsed_ms} ms from "
                        f"{', '.join(result.source_models)}."
                    ),
                    "complete",
                )
            )

        events.append(
            self._event(
                events,
                "step_limit",
                "Forced convergence at query limit",
                (
                    f"The hard limit of {self.max_agent_steps} executed queries was reached. "
                    "No further analysis was allowed; the conclusion uses existing evidence only."
                ),
                "limit",
            )
        )
        answer = self.llm.answer_agent(
            question=normalized_question,
            evidence=tuple(evidence),
            semantic_context=self.semantic_context,
        ).strip()
        if not answer:
            raise LlmProviderError("The agent returned an empty final answer.")
        events.append(
            self._event(
                events,
                "answer_synthesized",
                "Synthesized final answer",
                f"Used {len(evidence)} executed evidence set(s); no new query was run.",
                "complete",
            )
        )
        return self._agent_response(
            normalized_question, answer, steps, events, "step_limit"
        )

    @staticmethod
    def _event(
        existing: list[AgentEvent],
        event_type: str,
        title: str,
        detail: str,
        status: str,
    ) -> AgentEvent:
        return AgentEvent(
            sequence=len(existing) + 1,
            event_type=event_type,
            title=title,
            detail=detail,
            status=status,
        )

    def _agent_response(
        self,
        question: str,
        answer: str,
        steps: list[AgentStep],
        events: list[AgentEvent],
        stop_reason: str,
    ) -> AgentResponse:
        return AgentResponse(
            question=question,
            answer=answer,
            steps=tuple(steps),
            events=tuple(events),
            stop_reason=stop_reason,
            result_timestamp=datetime.now(timezone.utc),
            limitations=tuple(self.semantic_context["global_limitations"]),
        )
