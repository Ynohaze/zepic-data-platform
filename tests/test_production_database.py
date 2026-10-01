"""Reuse the isolated database fixture; never reset the operational database."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import json
import os
from threading import Barrier
import unittest

import test_daily_operations_database as daily_fixture

ROOT = daily_fixture.ROOT
from scripts.generate_daily_operations import run_day
from scripts.run_production_planning import run_cycle


@unittest.skipUnless(os.getenv('ZEPIC_DB_TESTS') == '1', 'Set ZEPIC_DB_TESTS=1 for isolated PostgreSQL tests')
class ProductionDatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        daily_fixture.DailyDatabaseTests.setUpClass.__func__(cls)

    def setUp(self):
        daily_fixture.DailyDatabaseTests.setUp(self)
        self.daily = self.config
        run_day(self.conn, self.daily, self.day, apply=True)
        self.day += timedelta(days=1)
        run_day(self.conn, self.daily, self.day, apply=True)
        self.config = json.loads((ROOT / 'config/production.json').read_text())

    def snapshot(self):
        return daily_fixture.DailyDatabaseTests.snapshot(self)

    def test_preview_balance_lifecycle_and_next_daily_cycle(self):
        before = self.snapshot()
        sequences = self.conn.execute('SELECT sequencename,last_value FROM pg_sequences ORDER BY 1').fetchall()
        preview = run_cycle(self.conn, self.config, self.day)
        self.assertEqual(before, self.snapshot())
        self.assertEqual(sequences, self.conn.execute('SELECT sequencename,last_value FROM pg_sequences ORDER BY 1').fetchall())
        result = run_cycle(self.conn, self.config, self.day, apply=True)
        self.assertEqual(result, dict(preview, applied=True))
        self.assertEqual(result['shipped_orders'], 148)
        self.assertEqual(result['confirmed_orders_remaining'], 0)
        after = self.snapshot()
        for table in ('customers','products','machines','maintenance_records','order_items'):
            self.assertEqual(before[table], after[table])
        produced = dict(self.conn.execute('SELECT po.product_id,sum(r.actual_quantity) FROM production_runs r '
                                         'JOIN production_orders po USING(production_order_id) GROUP BY po.product_id').fetchall())
        shipped = dict(self.conn.execute("SELECT i.product_id,sum(i.quantity) FROM order_items i JOIN orders o USING(order_id) "
                                        "WHERE o.status='shipped' GROUP BY i.product_id").fetchall())
        prior = {r[0]: r[1] for r in before['inventory']}
        for pid, qty, created, updated in after['inventory']:
            self.assertEqual(qty, prior[pid] + produced.get(pid, 0) - shipped.get(pid, 0))
            self.assertGreaterEqual(qty, 0)
            self.assertEqual(updated.date(), self.day)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM production_orders WHERE status<>'completed'").fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM production_runs r JOIN production_orders p USING(production_order_id) "
                                          "WHERE r.status<>'completed' OR r.actual_quantity<>p.planned_quantity OR r.end_time<=r.start_time").fetchone()[0], 0)
        overlaps = self.conn.execute('SELECT count(*) FROM production_runs a JOIN production_runs b '
                                    'ON a.machine_id=b.machine_id AND a.production_run_id<b.production_run_id '
                                    'AND a.start_time<b.end_time AND b.start_time<a.end_time').fetchone()[0]
        self.assertEqual(overlaps, 0)
        with self.assertRaisesRegex(ValueError, 'already recorded'):
            run_cycle(self.conn, self.config, self.day, apply=True)
        self.assertEqual(after, self.snapshot())
        tomorrow = self.day + timedelta(days=1)
        daily = run_day(self.conn, self.daily, tomorrow, apply=True)
        self.assertEqual(daily['orders_confirmed'], 82)
        self.assertEqual(run_cycle(self.conn, self.config, tomorrow, apply=True)['shipped_orders'], 82)

    def test_no_shortage_can_ship_without_machines(self):
        self.conn.execute("UPDATE inventory SET available_quantity=100000,updated_at='2026-01-03T08:30:00Z'")
        self.conn.execute("UPDATE machines SET status='maintenance',updated_at='2026-01-03T08:30:00Z'")
        result = run_cycle(self.conn, self.config, self.day, apply=True)
        self.assertEqual(result['production_runs'], 0)
        self.assertEqual(result['shipped_orders'], 148)
        with self.assertRaisesRegex(ValueError, 'already recorded'):
            run_cycle(self.conn, self.config, self.day, apply=True)

    def test_afternoon_pending_orders_do_not_block_morning_production(self):
        self.conn.execute("UPDATE orders SET created_at='2026-01-03T17:00:00Z', "
                          "updated_at='2026-01-03T17:00:00Z' WHERE status='pending'")
        self.conn.execute("UPDATE order_items SET created_at='2026-01-03T17:00:00Z', "
                          "updated_at='2026-01-03T17:00:00Z' WHERE order_id IN "
                          "(SELECT order_id FROM orders WHERE status='pending')")
        pending = self.conn.execute("SELECT * FROM orders WHERE status='pending' ORDER BY order_id").fetchall()
        items = self.conn.execute('SELECT * FROM order_items ORDER BY order_item_id').fetchall()
        preview = run_cycle(self.conn, self.config, self.day)
        self.assertEqual(preview['shipped_orders'], 148)
        result = run_cycle(self.conn, self.config, self.day, apply=True)
        self.assertEqual(result, dict(preview, applied=True))
        self.assertEqual(pending, self.conn.execute("SELECT * FROM orders WHERE status='pending' ORDER BY order_id").fetchall())
        self.assertEqual(items, self.conn.execute('SELECT * FROM order_items ORDER BY order_item_id').fetchall())
        self.assertEqual(self.conn.execute("SELECT count(*) FROM production_orders WHERE created_at <> '2026-01-03T09:00:00Z'").fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM production_runs WHERE start_time < '2026-01-03T09:01:00Z' OR end_time >= '2026-01-03T16:00:00Z'").fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM orders WHERE status='shipped' AND updated_at <> '2026-01-03T16:00:00Z'").fetchone()[0], 0)

    def test_future_confirmed_demand_is_still_rejected(self):
        self.conn.execute("UPDATE orders SET updated_at='2026-01-03T10:00:00Z' WHERE status='confirmed'")
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, 'time backwards'):
            run_cycle(self.conn, self.config, self.day, apply=True)
        self.assertEqual(before, self.snapshot())

    def test_unavailable_machines_and_incomplete_cycle_refuse_changes(self):
        self.conn.execute("UPDATE machines SET status='maintenance',updated_at='2026-01-03T08:30:00Z'")
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, 'no available machine'):
            run_cycle(self.conn, self.config, self.day, apply=True)
        self.assertEqual(before, self.snapshot())
        self.conn.execute("UPDATE machines SET status='available',updated_at='2026-01-03T08:31:00Z'")
        self.conn.execute("INSERT INTO production_orders(product_id,shortage_quantity,batch_multiple,planned_quantity,planned_start_date,created_at,updated_at) "
                          "SELECT product_id,1,20,20,'2026-01-03','2026-01-03T08:31:00Z','2026-01-03T08:31:00Z' FROM products LIMIT 1")
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, 'Unfinished production'):
            run_cycle(self.conn, self.config, self.day, apply=True)
        self.assertEqual(before, self.snapshot())

    def test_missing_inventory_and_wrong_day_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'latest generated'):
            run_cycle(self.conn, self.config, self.day - timedelta(days=1), apply=True)
        self.conn.execute('DELETE FROM inventory WHERE product_id=(SELECT min(product_id) FROM order_items)')
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, 'inventory row'):
            run_cycle(self.conn, self.config, self.day, apply=True)
        self.assertEqual(before, self.snapshot())

    def test_late_shipping_error_rolls_back_all_production_and_inventory(self):
        before = self.snapshot()
        self.conn.execute("CREATE FUNCTION fail_shipping_test() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'forced test failure'; END $$")
        self.conn.execute("CREATE TRIGGER fail_shipping_test BEFORE UPDATE ON orders FOR EACH ROW WHEN (NEW.status='shipped') EXECUTE FUNCTION fail_shipping_test()")
        try:
            with self.assertRaises(self.psycopg.errors.RaiseException):
                run_cycle(self.conn, self.config, self.day, apply=True)
            self.assertEqual(before, self.snapshot())
        finally:
            self.conn.execute('DROP TRIGGER fail_shipping_test ON orders')
            self.conn.execute('DROP FUNCTION fail_shipping_test()')
        self.assertEqual(run_cycle(self.conn, self.config, self.day, apply=True)['shipped_orders'], 148)

    def test_concurrent_attempts_do_not_double_produce_or_ship(self):
        barrier = Barrier(2)
        def worker():
            with self.psycopg.connect(**self.params, dbname=self.dbname) as conn:
                barrier.wait(timeout=10)
                try:
                    return run_cycle(conn, self.config, self.day, apply=True)
                except ValueError as exc:
                    return str(exc)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: worker(), range(2)))
        success = [r for r in results if isinstance(r, dict)]
        self.assertEqual(len(success), 1)
        self.assertTrue(any(isinstance(r, str) and 'already recorded' in r for r in results))
        self.assertEqual(self.conn.execute('SELECT count(*) FROM production_runs').fetchone()[0], success[0]['production_runs'])


if __name__ == '__main__':
    unittest.main()
