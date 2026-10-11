"""Opt in with ZEPIC_DB_TESTS=1; tests create and remove a unique test DB."""

from datetime import datetime, timezone
from decimal import Decimal
import os
from pathlib import Path
import unittest
import uuid

from psycopg.types.json import Jsonb

from scripts.reconcile_order_totals import reconcile_order_totals
from scripts.validate_raw import validate_batch


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.getenv("ZEPIC_DB_TESTS") == "1", "Set ZEPIC_DB_TESTS=1 for isolated PostgreSQL tests")
class RawQualityDatabaseTests(unittest.TestCase):
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
        cls.dbname = "zepic_quality_test_" + uuid.uuid4().hex[:12]
        cls.admin = psycopg.connect(**cls.params, dbname=os.environ["POSTGRES_DB"])
        cls.addClassCleanup(cls.admin.close)
        cls.admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(cls.dbname)))
        cls.addClassCleanup(lambda: cls.admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(cls.dbname))))
        cls.conn = psycopg.connect(**cls.params, dbname=cls.dbname)
        cls.addClassCleanup(cls.conn.close)

    def setUp(self):
        self.conn.execute((ROOT / "database/raw_schema.sql").read_text())
        self.conn.execute((ROOT / "database/quality_schema.sql").read_text())
        self.conn.execute("TRUNCATE zepic_raw.quality_findings, zepic_raw.quality_runs, "
                          "zepic_raw.records, zepic_raw.extraction_batches, zepic_raw.watermarks "
                          "RESTART IDENTITY CASCADE")
        self.updated = datetime(2026, 1, 1, tzinfo=timezone.utc)
        self.conn.execute("INSERT INTO zepic_raw.extraction_batches "
                          "(source_table, upper_updated_at, upper_primary_key, row_count, started_at, completed_at) "
                          "VALUES ('customers', %s, 18, 2, %s, %s) RETURNING batch_id",
                          (self.updated, self.updated, self.updated))
        self.batch_id = self.conn.execute("SELECT max(batch_id) FROM zepic_raw.extraction_batches").fetchone()[0]
        good = {"customer_id": 17, "customer_name": "A", "customer_tier": "standard",
                "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z"}
        bad = {"customer_id": 18, "customer_name": "B", "customer_tier": "vip",
               "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z"}
        with self.conn.cursor() as cur:
            cur.executemany("INSERT INTO zepic_raw.records "
                            "(batch_id, source_table, source_primary_key, source_updated_at, payload, extracted_at) "
                            "VALUES (%s,'customers',%s,%s,%s,%s)",
                            [(self.batch_id, 17, self.updated, Jsonb(good), self.updated),
                             (self.batch_id, 18, self.updated, Jsonb(bad), self.updated)])
        self.conn.execute("INSERT INTO zepic_raw.watermarks "
                          "(source_table, last_updated_at, last_primary_key, initialized) "
                          "VALUES ('customers', %s, 18, TRUE)", (self.updated,))

    def test_quarantines_bad_row_keeps_raw_watermark_and_is_idempotent(self):
        first = validate_batch(self.conn, self.batch_id)
        self.assertEqual((first["valid_rows"], first["quarantined_rows"], first["finding_count"]), (1, 1, 1))
        raw_count = self.conn.execute("SELECT count(*) FROM zepic_raw.records").fetchone()[0]
        watermark = self.conn.execute("SELECT last_updated_at, last_primary_key, initialized "
                                      "FROM zepic_raw.watermarks WHERE source_table='customers'").fetchone()
        self.assertEqual(self.conn.execute("SELECT rule_code FROM zepic_raw.quality_findings "
                                           "WHERE batch_id=%s", (self.batch_id,)).fetchone()[0],
                         "customer_tier_invalid")
        self.assertEqual(self.conn.execute("SELECT count(*) FROM zepic_raw.quarantined_records "
                                           "WHERE batch_id=%s", (self.batch_id,)).fetchone()[0], 1)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM zepic_raw.validated_records "
                                           "WHERE batch_id=%s", (self.batch_id,)).fetchone()[0], 1)

        repeated = validate_batch(self.conn, self.batch_id)
        self.assertTrue(repeated["skipped"])
        self.assertEqual(repeated["finding_count"], 1)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM zepic_raw.quality_runs").fetchone()[0], 1)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM zepic_raw.quality_findings").fetchone()[0], 1)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM zepic_raw.records").fetchone()[0], raw_count)
        self.assertEqual(self.conn.execute("SELECT last_updated_at, last_primary_key, initialized "
                                           "FROM zepic_raw.watermarks WHERE source_table='customers'").fetchone(),
                         watermark)

    def test_quarantines_record_whose_table_does_not_match_its_batch(self):
        self.conn.execute("UPDATE zepic_raw.records SET source_table='products' "
                          "WHERE batch_id=%s AND source_primary_key=17", (self.batch_id,))

        result = validate_batch(self.conn, self.batch_id)

        codes = {row[0] for row in self.conn.execute(
            "SELECT rule_code FROM zepic_raw.quality_findings WHERE batch_id=%s",
            (self.batch_id,)).fetchall()}
        self.assertEqual((result["valid_rows"], result["quarantined_rows"]), (0, 2), codes)
        self.assertIn("source_table_mismatch",
                      {row[0] for row in self.conn.execute(
                          "SELECT rule_code FROM zepic_raw.quality_findings WHERE batch_id=%s "
                          "AND source_primary_key=17", (self.batch_id,)).fetchall()})
        self.assertEqual(self.conn.execute("SELECT count(*) FROM zepic_raw.quarantined_records "
                                           "WHERE batch_id=%s AND source_primary_key=17",
                                           (self.batch_id,)).fetchone()[0], 1)

    def _insert_validated_batch(self, table, rows, updated=None):
        updated = updated or self.updated
        primary_keys = {
            "products": "product_id",
            "orders": "order_id",
            "order_items": "order_item_id",
        }
        primary_key = primary_keys[table]
        keys = [row[primary_key] for row in rows]
        batch_id = self.conn.execute(
            "INSERT INTO zepic_raw.extraction_batches "
            "(source_table, upper_updated_at, upper_primary_key, row_count, started_at, completed_at) "
            "VALUES (%s,%s,%s,%s,%s,%s) RETURNING batch_id",
            (table, updated, max(keys), len(rows), updated, updated),
        ).fetchone()[0]
        with self.conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO zepic_raw.records "
                "(batch_id, source_table, source_primary_key, source_updated_at, payload, extracted_at) "
                "VALUES (%s,%s,%s,%s,%s,%s)",
                [(batch_id, table, row[primary_key], updated, Jsonb(row), updated) for row in rows],
            )
        validate_batch(self.conn, batch_id)
        return batch_id

    def _seed_reconciliation_data(self):
        validate_batch(self.conn, self.batch_id)
        self._insert_validated_batch("products", [{
            "product_id": 1, "sku": "P-1", "product_name": "Product",
            "unit_price": "2.00", "batch_multiple": 1, "is_active": True,
            "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z",
        }])
        self._insert_validated_batch("orders", [
            {"order_id": 100, "customer_id": 17, "status": "pending", "total_amount": "4.00",
             "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z"},
            {"order_id": 101, "customer_id": 17, "status": "pending", "total_amount": "8.00",
             "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z"},
            {"order_id": 102, "customer_id": 17, "status": "pending", "total_amount": "0.00",
             "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z"},
        ])
        self._insert_validated_batch("order_items", [
            {"order_item_id": 301, "order_id": 100, "product_id": 1, "quantity": 2,
             "unit_price": "2.00", "created_at": "2026-01-01T00:00:00Z",
             "updated_at": "2026-01-01T00:00:00Z"},
            {"order_item_id": 302, "order_id": 101, "product_id": 1, "quantity": 3,
             "unit_price": "2.00", "created_at": "2026-01-01T00:00:00Z",
             "updated_at": "2026-01-01T00:00:00Z"},
        ])

    def test_reconciles_latest_order_and_item_totals_idempotently(self):
        self._seed_reconciliation_data()

        first = reconcile_order_totals(self.conn)

        self.assertEqual((first["input_orders"], first["checked_orders"], first["matched_orders"],
                          first["mismatched_orders"], first["missing_item_orders"], first["skipped_orders"]),
                         (3, 3, 1, 1, 1, 0))
        issues = self.conn.execute(
            "SELECT order_id, rule_code, order_total, items_total FROM zepic_raw.order_reconciliation_issues "
            "ORDER BY order_id").fetchall()
        self.assertEqual([(row[0], row[1]) for row in issues],
                         [(101, "order_total_mismatch"), (102, "order_items_missing")])
        self.assertEqual((issues[0][2], issues[0][3]), (Decimal("8.00"), Decimal("6.00")))

        repeated = reconcile_order_totals(self.conn)
        self.assertTrue(repeated["skipped"])
        self.assertEqual(repeated["run_id"], first["run_id"])
        self.assertEqual(self.conn.execute("SELECT count(*) FROM zepic_raw.order_reconciliation_runs").fetchone()[0], 1)

        corrected = {"order_item_id": 302, "order_id": 101, "product_id": 1, "quantity": 4,
                     "unit_price": "2.00", "created_at": "2026-01-01T00:00:00Z",
                     "updated_at": "2026-01-02T00:00:00Z"}
        self._insert_validated_batch("order_items", [corrected],
                                     updated=datetime(2026, 1, 2, tzinfo=timezone.utc))
        second = reconcile_order_totals(self.conn)
        self.assertFalse(second["skipped"])
        self.assertEqual((second["matched_orders"], second["mismatched_orders"],
                          second["missing_item_orders"]), (2, 0, 1))
        self.assertEqual(self.conn.execute("SELECT count(*) FROM zepic_raw.order_reconciliation_issues "
                                           "WHERE rule_code='order_total_mismatch'").fetchone()[0], 0)

    def test_skips_reconciliation_when_an_order_has_a_quarantined_item(self):
        self._seed_reconciliation_data()
        bad_item = {"order_item_id": 303, "order_id": 100, "product_id": 1, "quantity": 0,
                    "unit_price": "10.00", "created_at": "2026-01-01T00:00:00Z",
                    "updated_at": "2026-01-01T00:00:00Z"}
        self._insert_validated_batch("order_items", [bad_item])

        result = reconcile_order_totals(self.conn)

        self.assertEqual((result["checked_orders"], result["skipped_orders"],
                          result["mismatched_orders"]), (2, 1, 1))
        self.assertEqual(self.conn.execute("SELECT count(*) FROM zepic_raw.order_reconciliation_issues "
                                           "WHERE order_id=100").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
