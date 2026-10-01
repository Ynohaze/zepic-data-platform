from datetime import date, datetime, timezone
from decimal import Decimal
import unittest

from scripts.run_production_planning import build_plan, planned_quantity, plan_shipments, validate_config


class ProductionTests(unittest.TestCase):
    def test_batch_rounding_and_nonpositive_shortage(self):
        self.assertEqual(planned_quantity(100, 40, 20, Decimal('0.05')), (60, 80))
        self.assertEqual(planned_quantity(100, 0, 20, Decimal('0')), (100, 100))
        self.assertEqual(planned_quantity(50, 80, 20, Decimal('0.05')), (0, 0))
        self.assertEqual(planned_quantity(50, 50, 20, Decimal('0.05')), (0, 0))
        with self.assertRaises(ValueError):
            planned_quantity(2147483647, 0, 20, Decimal('0.05'))

    def test_insufficient_actual_stock_does_not_partially_ship(self):
        stamp = datetime(2026, 1, 2, tzinfo=timezone.utc)
        orders = [dict(order_id=1, created_at=stamp, items=[dict(product_id=1, quantity=10), dict(product_id=2, quantity=5)]),
                  dict(order_id=2, created_at=stamp, items=[dict(product_id=1, quantity=2)])]
        original = {1: 10, 2: 4}
        shipped, remaining = plan_shipments(orders, original)
        self.assertEqual(shipped, [2])
        self.assertEqual(remaining, {1: 8, 2: 4})
        self.assertEqual(original, {1: 10, 2: 4})
        self.assertEqual(plan_shipments(orders, {1: 0, 2: 0})[0], [])

    def test_machine_schedule_and_cycle_limit(self):
        stamp = datetime(2026, 1, 2, tzinfo=timezone.utc)
        orders = [dict(order_id=1, created_at=stamp, items=[dict(product_id=i, quantity=10) for i in range(3)])]
        products = [dict(product_id=i, sku=f'P{i}', batch_multiple=20, is_active=True, available_quantity=0) for i in range(3)]
        machines = [dict(machine_id=i, machine_code=f'M{i}') for i in range(2)]
        config = dict(seed=42, buffer_rate='0.05', run_minutes=10)
        first = build_plan(config, date(2026, 1, 3), orders, products, machines)
        self.assertEqual(first, build_plan(config, date(2026, 1, 3), orders, products[::-1], machines[::-1]))
        runs = first['production']
        self.assertLessEqual(runs[0]['end_time'], runs[2]['start_time'])
        self.assertEqual(runs[0]['machine_id'], runs[2]['machine_id'])
        self.assertEqual(first['shipped'], [1])
        with self.assertRaisesRegex(ValueError, 'no available machine'):
            build_plan(config, date(2026, 1, 3), orders, products, [])
        config['run_minutes'] = 210
        with self.assertRaisesRegex(ValueError, 'cannot finish'):
            build_plan(config, date(2026, 1, 3), orders, products, machines)

    def test_config_validation(self):
        for rate in ('NaN', 'Infinity', '-0.01', '1', '0.00001'):
            with self.assertRaises(ValueError):
                validate_config(dict(seed=42, buffer_rate=rate, run_minutes=10))


if __name__ == '__main__':
    unittest.main()
