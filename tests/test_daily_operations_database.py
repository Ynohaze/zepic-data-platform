"""Opt in with ZEPIC_DB_TESTS=1; tests create and remove only a unique test DB."""

from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
import json
import os
from pathlib import Path
from threading import Barrier
import unittest
import uuid

from scripts.etl.generate_daily_operations import day_start, run_day
from scripts.etl.generate_initial_data import TABLES, build_data, populate

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.getenv('ZEPIC_DB_TESTS') == '1', 'Set ZEPIC_DB_TESTS=1 for isolated PostgreSQL tests')
class DailyDatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        from psycopg import sql
        from dotenv import load_dotenv
        load_dotenv(ROOT / '.env', override=False)
        cls.psycopg, cls.sql = psycopg, sql
        cls.params = dict(host=os.getenv('POSTGRES_HOST', 'localhost'),
                          port=os.getenv('POSTGRES_PORT', '5432'), user=os.environ['POSTGRES_USER'],
                          password=os.environ['POSTGRES_PASSWORD'], autocommit=True)
        cls.dbname = 'zepic_daily_test_' + uuid.uuid4().hex[:12]
        cls.admin = psycopg.connect(**cls.params, dbname=os.environ['POSTGRES_DB'])
        cls.addClassCleanup(cls.admin.close)
        cls.admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(cls.dbname)))
        cls.addClassCleanup(lambda: cls.admin.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(cls.dbname))))
        cls.conn = psycopg.connect(**cls.params, dbname=cls.dbname)
        cls.addClassCleanup(cls.conn.close)
        cls.conn.execute((ROOT / 'database/schema.sql').read_text())

    def setUp(self):
        self.conn.execute(self.sql.SQL('TRUNCATE {} RESTART IDENTITY CASCADE').format(
            self.sql.SQL(', ').join(self.sql.Identifier(t) for t in TABLES)))
        populate(self.conn, build_data(json.loads((ROOT / 'config/initial_data.json').read_text())))
        self.config = json.loads((ROOT / 'config/daily_operations.json').read_text())
        self.day = date(2026, 1, 2)

    def snapshot(self):
        return {t: self.conn.execute(self.sql.SQL('SELECT * FROM {} ORDER BY 1').format(
            self.sql.Identifier(t))).fetchall() for t in TABLES}

    def test_preview_then_two_days_and_confirmation_timestamps(self):
        before = self.snapshot()
        sequences = self.conn.execute('SELECT sequencename, last_value FROM pg_sequences ORDER BY 1').fetchall()
        preview = run_day(self.conn, self.config, self.day)
        self.assertEqual(before, self.snapshot())
        self.assertEqual(sequences, self.conn.execute('SELECT sequencename, last_value FROM pg_sequences ORDER BY 1').fetchall())
        first = run_day(self.conn, self.config, self.day, apply=True)
        self.assertEqual(first, dict(preview, applied=True))
        first_items = self.conn.execute('SELECT * FROM order_items ORDER BY 1').fetchall()
        next_day = self.day + timedelta(days=1)
        second = run_day(self.conn, self.config, next_day, apply=True)
        self.assertEqual(second['orders_confirmed'], first['orders_created'])
        statuses = dict(self.conn.execute('SELECT status, count(*) FROM orders GROUP BY status').fetchall())
        self.assertEqual(statuses, {'confirmed': first['orders_created'], 'pending': second['orders_created']})
        confirmed = self.conn.execute("SELECT created_at, updated_at FROM orders WHERE status='confirmed'").fetchall()
        self.assertTrue(all(created < updated == day_start(next_day) + timedelta(hours=8) for created, updated in confirmed))
        self.assertEqual(first_items, self.conn.execute('SELECT * FROM order_items ORDER BY 1').fetchall()[:len(first_items)])
        mismatch = self.conn.execute('SELECT o.order_id FROM orders o JOIN order_items i USING(order_id) '
                                     'GROUP BY o.order_id HAVING o.total_amount <> sum(i.quantity * i.unit_price)').fetchall()
        self.assertEqual(mismatch, [])
        after = self.snapshot()
        for table in set(TABLES) - {'orders', 'order_items'}:
            self.assertEqual(before[table], after[table])

    def test_duplicate_skipped_and_backward_dates_are_rejected(self):
        for day in (self.day - timedelta(days=1), self.day + timedelta(days=1)):
            with self.assertRaisesRegex(ValueError, 'Expected next'):
                run_day(self.conn, self.config, day, apply=True)
        run_day(self.conn, self.config, self.day, apply=True)
        before = self.snapshot()
        for day in (self.day, self.day - timedelta(days=1), self.day + timedelta(days=2)):
            with self.assertRaisesRegex(ValueError, 'Expected next'):
                run_day(self.conn, self.config, day, apply=True)
        self.assertEqual(before, self.snapshot())

    def test_late_failure_rolls_back_confirmation_and_new_orders(self):
        run_day(self.conn, self.config, self.day, apply=True)
        before = self.snapshot()
        self.conn.execute("CREATE FUNCTION fail_daily_test() RETURNS trigger LANGUAGE plpgsql AS $$ "
                          "BEGIN RAISE EXCEPTION 'forced test failure'; END $$")
        self.conn.execute('CREATE TRIGGER fail_daily_test BEFORE INSERT ON order_items '
                          'FOR EACH ROW EXECUTE FUNCTION fail_daily_test()')
        try:
            with self.assertRaises(self.psycopg.errors.RaiseException):
                run_day(self.conn, self.config, self.day + timedelta(days=1), apply=True)
            self.assertEqual(before, self.snapshot())
        finally:
            self.conn.execute('DROP TRIGGER fail_daily_test ON order_items')
            self.conn.execute('DROP FUNCTION fail_daily_test()')
        retry = run_day(self.conn, self.config, self.day + timedelta(days=1), apply=True)
        self.assertGreater(retry['orders_created'], 0)

    def test_prices_are_snapshots_and_inactive_products_are_excluded(self):
        self.conn.execute("UPDATE products SET is_active=FALSE, updated_at='2026-01-01T01:00:00Z' WHERE sku='ZEP-001'")
        run_day(self.conn, self.config, self.day, apply=True)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM order_items i JOIN products p USING(product_id) '
                                          'WHERE NOT p.is_active').fetchone()[0], 0)
        items = self.conn.execute('SELECT * FROM order_items ORDER BY 1').fetchall()
        self.conn.execute("UPDATE products SET unit_price=unit_price+1, updated_at='2026-01-02T18:00:00Z'")
        run_day(self.conn, self.config, self.day + timedelta(days=1), apply=True)
        self.assertEqual(items, self.conn.execute('SELECT * FROM order_items ORDER BY 1').fetchall()[:len(items)])
        mismatches = self.conn.execute('SELECT count(*) FROM order_items i JOIN products p USING(product_id) '
                                      'WHERE i.created_at >= %s AND i.unit_price <> p.unit_price',
                                      (day_start(self.day + timedelta(days=1)),)).fetchone()[0]
        self.assertEqual(mismatches, 0)

    def test_two_concurrent_runs_commit_only_one_day(self):
        barrier = Barrier(2)

        def worker():
            with self.psycopg.connect(**self.params, dbname=self.dbname) as conn:
                barrier.wait(timeout=10)
                try:
                    return run_day(conn, self.config, self.day, apply=True)
                except ValueError as exc:
                    return str(exc)

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: worker(), range(2)))
        successful = [r for r in results if isinstance(r, dict)]
        self.assertEqual(len(successful), 1)
        self.assertTrue(any(isinstance(r, str) and 'Expected next' in r for r in results))
        self.assertEqual(self.conn.execute('SELECT count(*) FROM orders').fetchone()[0], successful[0]['orders_created'])


if __name__ == '__main__':
    unittest.main()
