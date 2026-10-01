from datetime import datetime, timezone
from decimal import Decimal
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from src.source_api import json_value, rows


class SourceApiUnitTests(unittest.TestCase):
    def test_serializes_decimal_and_timestamps_without_precision_loss(self):
        self.assertEqual(json_value(Decimal("100.20")), "100.20")
        self.assertEqual(json_value(datetime(2026, 1, 1, tzinfo=timezone.utc)),
                         "2026-01-01T00:00:00Z")

    def test_page_returns_stable_next_cursor_and_fixed_upper_bound(self):
        stamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
        records = [{"order_id": 101, "updated_at": "2026-01-01T00:00:00Z"},
                   {"order_id": 102, "updated_at": "2026-01-01T00:00:00Z"}]
        with patch("src.source_api.read_page", return_value=records):
            result = rows("orders", stamp, 200, limit=2)
        self.assertEqual(result["next_cursor"], {"updated_at": records[-1]["updated_at"], "primary_key": 102})
        self.assertEqual(result["upper_bound"], {"updated_at": "2026-01-01T00:00:00Z", "primary_key": 200})
        self.assertTrue(result["has_more"])

    def test_rejects_unsupported_tables_and_malformed_cursor(self):
        stamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
        with self.assertRaises(HTTPException) as unknown:
            rows("customers", stamp, 10)
        self.assertEqual(unknown.exception.status_code, 404)
        with self.assertRaises(HTTPException) as malformed:
            rows("orders", stamp, 10, after_updated_at=stamp)
        self.assertEqual(malformed.exception.status_code, 422)

    def test_requires_timezone_aware_upper_and_cursor_not_past_upper(self):
        naive = datetime(2026, 1, 1)
        aware = datetime(2026, 1, 1, tzinfo=timezone.utc)
        with self.assertRaises(HTTPException):
            rows("orders", naive, 10)
        with self.assertRaises(HTTPException):
            rows("orders", aware, 10, after_updated_at=aware, after_id=11)


if __name__ == "__main__":
    unittest.main()
