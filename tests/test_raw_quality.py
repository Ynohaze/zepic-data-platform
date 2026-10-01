from datetime import datetime, timezone
import unittest

from scripts.validate_raw import parse_timestamp, validate_record


class RawQualityUnitTests(unittest.TestCase):
    def setUp(self):
        self.updated = datetime(2026, 1, 1, tzinfo=timezone.utc)
        self.valid_customer = {
            "customer_id": 17,
            "customer_name": "Example",
            "customer_tier": "silver",
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
        }

    def test_accepts_valid_row_and_source_timestamp(self):
        self.assertEqual(validate_record("customers", 17, self.updated, self.valid_customer, {}), [])

    def test_quarantines_invalid_enum_and_malformed_json_type_without_crashing(self):
        invalid = dict(self.valid_customer, customer_tier=["silver"])
        findings = validate_record("customers", 17, self.updated, invalid, {})
        self.assertIn(("customer_tier_invalid", "customer_tier is outside the allowed values"), findings)

    def test_detects_mismatched_key_and_timestamp(self):
        payload = dict(self.valid_customer, customer_id=18,
                       updated_at="2026-01-02T00:00:00Z", customer_tier="standard")
        findings = validate_record("customers", 17, self.updated, payload, {})
        codes = {code for code, _ in findings}
        self.assertIn("primary_key_mismatch", codes)
        self.assertIn("updated_at_mismatch", codes)
        self.assertEqual(parse_timestamp("2026-01-01T08:00:00+08:00"), self.updated)

    def test_detects_missing_foreign_key_parent(self):
        order = {"order_id": 21, "customer_id": 999, "status": "pending",
                 "total_amount": "4.00", "created_at": "2026-01-01T00:00:00Z",
                 "updated_at": "2026-01-01T00:00:00Z"}
        findings = validate_record("orders", 21, self.updated, order, {"customers": set()})
        self.assertIn("customer_id_missing", {code for code, _ in findings})


if __name__ == "__main__":
    unittest.main()
