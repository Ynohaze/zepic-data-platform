from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import json
import os
from threading import Barrier
import unittest

import test_daily_operations_database as fixture
from scripts.generate_daily_operations import run_day
from scripts.run_simulation import simulate


@unittest.skipUnless(os.getenv('ZEPIC_DB_TESTS') == '1', 'Set ZEPIC_DB_TESTS=1 for isolated PostgreSQL tests')
class SimulationDatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture.DailyDatabaseTests.setUpClass.__func__(cls)

    def setUp(self):
        fixture.DailyDatabaseTests.setUp(self)
        self.production = json.loads((fixture.ROOT / 'config/production.json').read_text())

    def snapshot(self):
        return fixture.DailyDatabaseTests.snapshot(self)

    def test_30_days_balance_timeline_and_repeat_resume(self):
        initial = self.snapshot()
        seq = self.conn.execute('SELECT sequencename,last_value FROM pg_sequences ORDER BY 1').fetchall()
        preview = simulate(self.conn, self.config, self.production, self.day, 30)
        self.assertEqual(len(preview), 30)
        self.assertEqual(initial, self.snapshot())
        self.assertEqual(seq, self.conn.execute('SELECT sequencename,last_value FROM pg_sequences ORDER BY 1').fetchall())
        results = simulate(self.conn, self.config, self.production, self.day, 30, apply=True)
        self.assertEqual(len(results), 30)
        self.assertTrue(all(r['production']['confirmed_orders_remaining'] == 0 for r in results))
        last = self.day + timedelta(days=29)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM orders WHERE status='pending' AND (created_at AT TIME ZONE 'UTC')::date <> %s", (last,)).fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM orders WHERE status='shipped' AND "
                                          "(updated_at <> ((created_at AT TIME ZONE 'UTC')::date + 1 + time '16:00') AT TIME ZONE 'UTC')").fetchone()[0], 0)
        produced = dict(self.conn.execute('SELECT p.product_id,sum(r.actual_quantity) FROM production_runs r JOIN production_orders p USING(production_order_id) GROUP BY 1').fetchall())
        consumed = dict(self.conn.execute("SELECT i.product_id,sum(i.quantity) FROM order_items i JOIN orders o USING(order_id) WHERE o.status='shipped' GROUP BY 1").fetchall())
        opening = {r[0]: r[1] for r in initial['inventory']}
        for pid, qty in self.conn.execute('SELECT product_id,available_quantity FROM inventory'):
            self.assertEqual(qty, opening[pid] + produced.get(pid, 0) - consumed.get(pid, 0))
            self.assertGreaterEqual(qty, 0)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM production_runs WHERE status<>'completed' OR end_time >= date_trunc('day', start_time AT TIME ZONE 'UTC') AT TIME ZONE 'UTC' + interval '16 hours'").fetchone()[0], 0)
        finished = self.snapshot()
        again = simulate(self.conn, self.config, self.production, self.day, 30, resume=True, apply=True)
        self.assertTrue(all(r['action'] == 'skip_completed' for r in again))
        self.assertEqual(finished, self.snapshot())
        for t in ('customers', 'products', 'machines', 'maintenance_records'):
            self.assertEqual(initial[t], finished[t])

    def test_failed_second_day_rolls_back_new_orders_and_resumes(self):
        self.conn.execute("CREATE FUNCTION fail_simulation_test() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'injected shipping failure'; END $$")
        self.conn.execute("CREATE TRIGGER fail_simulation_test BEFORE UPDATE ON orders FOR EACH ROW WHEN (NEW.status='shipped') EXECUTE FUNCTION fail_simulation_test()")
        progress = []
        try:
            with self.assertRaises(self.psycopg.errors.RaiseException):
                simulate(self.conn, self.config, self.production, self.day, 3, apply=True, on_day=progress.append)
            self.assertEqual(len(progress), 1)
            self.assertEqual(self.conn.execute("SELECT DISTINCT (created_at AT TIME ZONE 'UTC')::date FROM orders").fetchall(), [(self.day,)])
            self.assertEqual(self.conn.execute("SELECT count(*) FROM orders WHERE status<>'pending'").fetchone()[0], 0)
            self.assertEqual(self.conn.execute('SELECT count(*) FROM production_orders').fetchone()[0], 0)
        finally:
            self.conn.execute('DROP TRIGGER fail_simulation_test ON orders')
            self.conn.execute('DROP FUNCTION fail_simulation_test()')
        result = simulate(self.conn, self.config, self.production, self.day, 3, resume=True, apply=True)
        self.assertEqual([r['action'] for r in result], ['skip_completed', 'run_day', 'run_day'])

    def test_resume_last_standalone_order_day_without_production(self):
        run_day(self.conn, self.config, self.day, apply=True)
        run_day(self.conn, self.config, self.day + timedelta(days=1), apply=True)
        items = self.conn.execute('SELECT * FROM order_items ORDER BY 1').fetchall()
        with self.assertRaisesRegex(ValueError, 'needs production'):
            simulate(self.conn, self.config, self.production, self.day + timedelta(days=2), 1, resume=True, apply=True)
        result = simulate(self.conn, self.config, self.production, self.day, 2, resume=True, apply=True)
        self.assertEqual([r['action'] for r in result], ['skip_completed', 'finish_production'])
        self.assertEqual(items, self.conn.execute('SELECT * FROM order_items ORDER BY 1').fetchall())
        self.assertEqual(result[-1]['production']['shipped_orders'], 148)

    def test_refuses_gap_and_existing_days_without_resume(self):
        with self.assertRaisesRegex(ValueError, 'skip days'):
            simulate(self.conn, self.config, self.production, self.day + timedelta(days=1), 1, apply=True)
        simulate(self.conn, self.config, self.production, self.day, 2, apply=True)
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, 'existing dates'):
            simulate(self.conn, self.config, self.production, self.day, 2, apply=True)
        self.assertEqual(before, self.snapshot())

    def test_two_runners_resume_without_duplicate_days(self):
        barrier = Barrier(2)
        def worker():
            with self.psycopg.connect(**self.params, dbname=self.dbname) as conn:
                barrier.wait(timeout=10)
                return simulate(conn, self.config, self.production, self.day, 3, resume=True, apply=True)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: worker(), range(2)))
        actions = [r for batch in results for r in batch]
        self.assertEqual(sum(r['action'] == 'run_day' for r in actions), 3)
        expected = sum(r['orders']['orders_created'] for r in actions if r['action'] == 'run_day')
        self.assertEqual(self.conn.execute('SELECT count(*) FROM orders').fetchone()[0], expected)
