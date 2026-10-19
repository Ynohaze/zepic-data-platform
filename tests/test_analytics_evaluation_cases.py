import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EVALUATION_PATH = ROOT / "analytics" / "evaluation_cases.json"


class AnalyticsEvaluationCasesTests(unittest.TestCase):
    def setUp(self):
        self.contract = json.loads(EVALUATION_PATH.read_text())
        self.cases = self.contract["cases"]

    def test_evaluation_contract_is_small_complete_and_unique(self):
        self.assertEqual(self.contract["version"], 1)
        self.assertGreaterEqual(len(self.cases), 10)
        self.assertLessEqual(len(self.cases), 30)
        ids = [case["id"] for case in self.cases]
        self.assertEqual(len(ids), len(set(ids)))

        for case in self.cases:
            self.assertTrue(case["question"].strip())
            self.assertIn(
                case["category"],
                {"supported", "ambiguous", "unsupported", "hostile"},
            )
            self.assertIn(case["expected"], {"answer", "refusal", "blocked"})

    def test_contract_covers_level_one_outcomes(self):
        outcomes = {case["expected"] for case in self.cases}
        self.assertEqual(outcomes, {"answer", "refusal", "blocked"})
        self.assertIn("ambiguous", {case["category"] for case in self.cases})
        supported_sources = {
            source
            for case in self.cases
            if case["expected"] == "answer"
            for source in case["required_sources"]
        }
        self.assertTrue(
            {"fct_orders", "fct_order_lines", "fct_production_runs", "current_inventory"}
            <= supported_sources
        )


if __name__ == "__main__":
    unittest.main()
