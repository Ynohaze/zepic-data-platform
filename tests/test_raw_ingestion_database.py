"""Opt in with ZEPIC_DB_TESTS=1; tests create and remove a unique test DB."""

import os
from pathlib import Path
import unittest
import uuid

from scripts.extract_to_raw import extract_table, parse_timestamp


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.getenv("ZEPIC_DB_TESTS") == "1", "Set ZEPIC_DB_TESTS=1 for isolated PostgreSQL tests")
class RawIngestionDatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        from psycopg import sql
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env", override=False)
        cls.psycopg, cls.sql = psycopg, sql
        cls.params = dict(host=os.getenv("POSTGRES_HOST", "localhost"),
                          port=os.getenv("POSTGRES_PORT", "5432"), user=os.environ["POSTGRES_USER"],
                          password=os.environ["POSTGRES_PASSWORD"], autocommit=True)
        cls.dbname = "zepic_raw_test_" + uuid.uuid4().hex[:12]
        cls.admin = psycopg.connect(**cls.params, dbname=os.environ["POSTGRES_DB"])
        cls.addClassCleanup(cls.admin.close)
        cls.admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(cls.dbname)))
        cls.addClassCleanup(lambda: cls.admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(cls.dbname))))
        cls.conn = psycopg.connect(**cls.params, dbname=cls.dbname)
        cls.addClassCleanup(cls.conn.close)

    def setUp(self):
        self.conn.execute((ROOT / "database/raw_schema.sql").read_text())
        self.conn.execute("TRUNCATE zepic_raw.records, zepic_raw.extraction_batches, "
                          "zepic_raw.watermarks RESTART IDENTITY CASCADE")
        self.source_rows = [
            {"order_id": 100, "updated_at": "2026-01-01T00:00:00Z", "total_amount": "1.20"},
            {"order_id": 101, "updated_at": "2026-01-01T00:00:00Z", "total_amount": "2.30"},
        ]

    def source_request(self, url):
        from urllib.parse import parse_qs, urlparse
        parsed = urlparse(url)
        if parsed.path.endswith("/bounds"):
            row = max(self.source_rows,
                      key=lambda value: (parse_timestamp(value["updated_at"]), value["order_id"])) \
                if self.source_rows else None
            upper = None if row is None else {"updated_at": row["updated_at"], "primary_key": row["order_id"]}
            return {"upper_bound": upper}
        query = parse_qs(parsed.query)
        upper = (parse_timestamp(query["upper_updated_at"][0]), int(query["upper_id"][0]))
        after = ((parse_timestamp(query["after_updated_at"][0]), int(query["after_id"][0]))
                 if "after_id" in query else None)
        selected = [row for row in self.source_rows
                    if (after is None or (parse_timestamp(row["updated_at"]), row["order_id"]) > after)
                    and (parse_timestamp(row["updated_at"]), row["order_id"]) <= upper]
        selected.sort(key=lambda row: (parse_timestamp(row["updated_at"]), row["order_id"]))
        limit = int(query["limit"][0])
        page, more = selected[:limit], len(selected) > limit
        cursor = None if not page else {"updated_at": page[-1]["updated_at"], "primary_key": page[-1]["order_id"]}
        return {"data": page, "next_cursor": cursor, "has_more": more}

    def test_baseline_incremental_empty_batch_and_watermark(self):
        first = extract_table(self.conn, "orders", "http://test", request=self.source_request)
        self.assertEqual(first["rows"], 2)
        self.assertEqual(first["watermark"]["primary_key"], 101)

        self.source_rows = [
            {"order_id": 100, "updated_at": "2026-01-02T00:00:00Z", "total_amount": "1.25"},
            self.source_rows[1],
            {"order_id": 103, "updated_at": "2026-01-02T00:00:00Z", "total_amount": "3.40"},
        ]
        second = extract_table(self.conn, "orders", "http://test", request=self.source_request)
        self.assertEqual(second["rows"], 2)
        self.assertEqual(second["watermark"]["primary_key"], 103)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM zepic_raw.records").fetchone()[0], 4)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM zepic_raw.records WHERE batch_id=2 "
                                           "AND payload->>'total_amount'='1.25'").fetchone()[0], 1)

        before = self.conn.execute("SELECT last_updated_at, last_primary_key, initialized "
                                   "FROM zepic_raw.watermarks WHERE source_table='orders'").fetchone()
        empty = extract_table(self.conn, "orders", "http://test", request=self.source_request)
        after = self.conn.execute("SELECT last_updated_at, last_primary_key, initialized "
                                  "FROM zepic_raw.watermarks WHERE source_table='orders'").fetchone()
        self.assertEqual(empty["rows"], 0)
        self.assertEqual(after, before)
        self.assertEqual(self.conn.execute("SELECT row_count FROM zepic_raw.extraction_batches "
                                           "WHERE batch_id=3").fetchone()[0], 0)

    def test_failed_raw_page_rolls_back_batch_rows_and_watermark_for_retry(self):
        extract_table(self.conn, "orders", "http://test", request=self.source_request)
        self.source_rows.append({"order_id": 103, "updated_at": "2026-01-02T00:00:00Z", "total_amount": "4.50"})
        before = self.conn.execute("SELECT last_updated_at, last_primary_key "
                                   "FROM zepic_raw.watermarks WHERE source_table='orders'").fetchone()
        self.conn.execute("CREATE FUNCTION zepic_raw.fail_test_insert() RETURNS trigger LANGUAGE plpgsql AS $$ "
                          "BEGIN IF NEW.source_primary_key = 103 THEN RAISE EXCEPTION 'forced raw write failure'; END IF; "
                          "RETURN NEW; END $$")
        self.conn.execute("CREATE TRIGGER fail_test_insert BEFORE INSERT ON zepic_raw.records "
                          "FOR EACH ROW EXECUTE FUNCTION zepic_raw.fail_test_insert()")
        try:
            with self.assertRaises(self.psycopg.errors.RaiseException):
                extract_table(self.conn, "orders", "http://test", request=self.source_request)
            self.assertEqual(self.conn.execute("SELECT count(*) FROM zepic_raw.extraction_batches").fetchone()[0], 1)
            self.assertEqual(self.conn.execute("SELECT count(*) FROM zepic_raw.records").fetchone()[0], 2)
            self.assertEqual(self.conn.execute("SELECT last_updated_at, last_primary_key "
                                               "FROM zepic_raw.watermarks WHERE source_table='orders'").fetchone(), before)
        finally:
            self.conn.execute("DROP TRIGGER fail_test_insert ON zepic_raw.records")
            self.conn.execute("DROP FUNCTION zepic_raw.fail_test_insert()")
        retry = extract_table(self.conn, "orders", "http://test", request=self.source_request)
        self.assertEqual(retry["rows"], 1)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM zepic_raw.records").fetchone()[0], 3)

    def test_empty_maintenance_table_initializes_without_a_fake_watermark(self):
        empty = extract_table(self.conn, "maintenance_records", "http://test",
                              request=lambda _: {"upper_bound": None})
        self.assertEqual(empty["rows"], 0)
        self.assertIsNone(empty["watermark"])
        self.assertEqual(self.conn.execute(
            "SELECT last_updated_at, last_primary_key, initialized FROM zepic_raw.watermarks "
            "WHERE source_table='maintenance_records'").fetchone(), (None, None, True))
        self.assertEqual(self.conn.execute("SELECT source_table, row_count FROM zepic_raw.extraction_batches")
                         .fetchone(), ("maintenance_records", 0))

        stamp = "2026-02-01T10:00:00Z"

        def newly_populated(url):
            if url.endswith("/bounds"):
                return {"upper_bound": {"updated_at": stamp, "primary_key": 9}}
            return {"data": [{"maintenance_record_id": 9, "updated_at": stamp,
                               "status": "scheduled"}],
                    "next_cursor": {"updated_at": stamp, "primary_key": 9}, "has_more": False}

        later = extract_table(self.conn, "maintenance_records", "http://test", request=newly_populated)
        self.assertEqual(later["rows"], 1)
        self.assertEqual(later["watermark"]["primary_key"], 9)


if __name__ == "__main__":
    unittest.main()
