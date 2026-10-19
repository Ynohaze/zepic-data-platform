from datetime import datetime, timezone
from email.message import Message
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse
import unittest

from scripts.etl.extract_to_raw import ensure_raw_schema, fetch_batch, parse_timestamp, request_json


class ExtractionUnitTests(unittest.TestCase):
    def test_parse_timestamp_requires_timezone_and_normalizes_utc(self):
        parsed = parse_timestamp("2026-01-01T08:00:00+08:00")
        self.assertEqual(parsed, datetime(2026, 1, 1, 0, tzinfo=timezone.utc))
        with self.assertRaisesRegex(ValueError, "timezone"):
            parse_timestamp("2026-01-01T00:00:00")

    def test_fetch_batch_pages_across_equal_timestamp_primary_keys(self):
        stamp = "2026-01-01T00:00:00Z"
        seen = []

        def request(url):
            parsed = urlparse(url)
            seen.append((parsed.path, parse_qs(parsed.query)))
            if parsed.path.endswith("/bounds"):
                return {"upper_bound": {"updated_at": stamp, "primary_key": 12}}
            query = parse_qs(parsed.query)
            if "after_id" not in query:
                return {"data": [{"order_id": 10, "updated_at": stamp},
                                 {"order_id": 11, "updated_at": stamp}],
                        "next_cursor": {"updated_at": stamp, "primary_key": 11}, "has_more": True}
            self.assertEqual(query["after_id"], ["11"])
            return {"data": [{"order_id": 12, "updated_at": stamp}],
                    "next_cursor": {"updated_at": stamp, "primary_key": 12}, "has_more": False}

        upper, rows = fetch_batch("orders", "http://source.test/", None,
                                  page_size=2, request=request)
        self.assertEqual(upper, {"updated_at": stamp, "primary_key": 12})
        self.assertEqual([row["order_id"] for row in rows], [10, 11, 12])
        self.assertEqual(len(seen), 3)

    def test_fetch_batch_empty_table(self):
        upper, rows = fetch_batch("inventory", "http://source.test", None,
                                  request=lambda _: {"upper_bound": None})
        self.assertIsNone(upper)
        self.assertEqual(rows, [])

    def test_request_json_retries_server_error_then_succeeds(self):
        calls = []

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return b'{"ok": true}'

        def opener(*args, **kwargs):
            calls.append(True)
            if len(calls) == 1:
                raise HTTPError("http://source.test", 503, "unavailable", Message(), None)
            return Response()

        result = request_json("http://source.test", opener=opener, sleeper=lambda _: None)
        self.assertEqual(result, {"ok": True})
        self.assertEqual(len(calls), 2)

    def test_fetch_refuses_unknown_table(self):
        with self.assertRaisesRegex(ValueError, "Unsupported source table"):
            fetch_batch("not_a_source_table", "http://source.test", None)

    def test_schema_lock_is_released_when_initialization_fails(self):
        statements = []

        class Connection:
            def execute(self, statement, params=None):
                statements.append((statement, params))
                if statement == "broken schema":
                    raise RuntimeError("schema failure")

        with self.assertRaisesRegex(RuntimeError, "schema failure"):
            ensure_raw_schema(Connection(), "broken schema")

        self.assertIn("pg_advisory_lock", statements[0][0])
        self.assertEqual(statements[1], ("broken schema", None))
        self.assertIn("pg_advisory_unlock", statements[2][0])


if __name__ == "__main__":
    unittest.main()
