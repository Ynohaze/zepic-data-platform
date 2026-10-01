from datetime import date
import unittest

from scripts.run_simulation import dates


class SimulationDatesTests(unittest.TestCase):
    def test_inclusive_day_count_and_month_boundary(self):
        days = dates(date(2026, 1, 2), 30)
        self.assertEqual(len(days), 30)
        self.assertEqual(days[-1], date(2026, 1, 31))
        self.assertEqual(dates(date(2028, 2, 28), 3)[-1], date(2028, 3, 1))

    def test_invalid_counts(self):
        for days in (0, -1, True, 1.5, 367):
            with self.assertRaises(ValueError):
                dates(date(2026, 1, 2), days)
