"""Run one live, sanitized acceptance task through the bounded agent."""

from __future__ import annotations

import sys
from pathlib import Path

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from analytics.llm import LlmProviderError, QuestionUnsupported  # noqa: E402
from analytics.query_guard import QueryRejected  # noqa: E402
from src.analytics_api import build_query_engine_from_env, build_service_from_env  # noqa: E402


QUESTION = (
    "Investigate which products drive ordered units, compare the leading products "
    "with their current inventory, and summarize the evidence."
)


def main() -> None:
    load_dotenv(ROOT / ".env", override=False)
    query_engine = build_query_engine_from_env()
    service = build_service_from_env(query_engine)
    if query_engine is None or service is None:
        raise SystemExit("Analytics database and Groq settings are required.")

    try:
        result = service.run_agent(QUESTION)
    except (LlmProviderError, QuestionUnsupported, QueryRejected) as exc:
        raise SystemExit(
            f"Bounded agent acceptance failed: {type(exc).__name__}: {exc}"
        ) from exc

    fingerprints = {
        " ".join(step.generated_sql.split()).casefold() for step in result.steps
    }
    event_types = [event.event_type for event in result.events]
    passed = (
        1 <= len(result.steps) <= service.max_agent_steps
        and len(fingerprints) == len(result.steps)
        and bool(result.answer.strip())
        and result.stop_reason in {"model_finished", "step_limit"}
        and "query_planned" in event_types
        and "query_executed" in event_types
        and "answer_synthesized" in event_types
        and (
            "model_finished" in event_types
            or "step_limit" in event_types
        )
    )
    sources = sorted(
        {source for step in result.steps for source in step.source_models}
    )
    print(
        f"bounded_agent: {'PASS' if passed else 'FAIL'} "
        f"queries={len(result.steps)} stop={result.stop_reason} "
        f"events={len(result.events)} sources={','.join(sources)}"
    )
    if not passed:
        raise SystemExit("Bounded agent acceptance failed contract checks.")


if __name__ == "__main__":
    main()
