"""Run a small live acceptance sample against the configured Level 1 service."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from analytics.llm import LlmProviderError, QuestionUnsupported  # noqa: E402
from analytics.query_guard import QueryRejected  # noqa: E402
from src.analytics_api import build_query_engine_from_env, build_service_from_env  # noqa: E402


DEFAULT_CASES = (
    "orders_total",
    "inventory_history",
    "prompt_injection_raw",
)


def load_cases() -> dict[str, dict]:
    contract = json.loads(
        (ROOT / "analytics" / "evaluation_cases.json").read_text()
    )
    return {case["id"]: case for case in contract["cases"]}


def observed_outcome(service, question: str) -> tuple[str, tuple[str, ...]]:
    try:
        result = service.ask(question)
    except QuestionUnsupported:
        return "refusal", ()
    except QueryRejected:
        return "blocked", ()
    return "answer", result.source_models


def outcome_matches(expected: str, observed: str) -> bool:
    if expected == "blocked":
        return observed in {"blocked", "refusal"}
    return expected == observed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--case",
        action="append",
        dest="case_ids",
        help="Evaluation case ID to run; repeat for multiple cases.",
    )
    args = parser.parse_args()

    load_dotenv(ROOT / ".env", override=False)
    query_engine = build_query_engine_from_env()
    service = build_service_from_env(query_engine)
    if query_engine is None or service is None:
        raise SystemExit("Analytics database and Groq settings are required.")

    cases = load_cases()
    selected_ids = tuple(args.case_ids or DEFAULT_CASES)
    unknown = [case_id for case_id in selected_ids if case_id not in cases]
    if unknown:
        raise SystemExit("Unknown evaluation cases: " + ", ".join(unknown))

    failures = 0
    for case_id in selected_ids:
        case = cases[case_id]
        try:
            observed, sources = observed_outcome(service, case["question"])
        except LlmProviderError:
            observed, sources = "provider_error", ()

        passed = outcome_matches(case["expected"], observed)
        if passed and case["expected"] == "answer":
            passed = set(case["required_sources"]) <= set(sources)
        failures += not passed
        print(
            f"{case_id}: {'PASS' if passed else 'FAIL'} "
            f"expected={case['expected']} observed={observed}"
        )

    if failures:
        raise SystemExit(f"Level 1 acceptance failed: {failures} case(s).")
    print(f"Level 1 acceptance passed: {len(selected_ids)} case(s).")


if __name__ == "__main__":
    main()
