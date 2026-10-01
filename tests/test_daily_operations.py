import copy
from datetime import date, timedelta
from decimal import Decimal
import json
from pathlib import Path
import unittest

from scripts.generate_daily_operations import build_orders, day_start, validate_config

ROOT = Path(__file__).resolve().parents[1]


class DailyGenerationTests(unittest.TestCase):
    def setUp(self):
        self.config = json.loads((ROOT / 'config/daily_operations.json').read_text())
        self.day = date(2026, 1, 2)
        self.customers = [101, 104, 107]
        self.products = [dict(product_id=200 + i, sku=f'P-{i:02d}', unit_price=Decimal('0.35'))
                         for i in range(20)]

    def test_repeatable_independent_of_input_order_and_varies_by_day(self):
        first = build_orders(self.config, self.day, self.customers, self.products)
        self.assertEqual(first, build_orders(self.config, self.day, self.customers[::-1], self.products[::-1]))
        self.assertNotEqual(first, build_orders(self.config, self.day + timedelta(days=1), self.customers, self.products))
        self.config['seed'] += 1
        self.assertNotEqual(first, build_orders(self.config, self.day, self.customers, self.products))

    def test_order_rules_and_exact_money(self):
        orders = build_orders(self.config, self.day, self.customers, self.products)
        self.assertTrue(50 <= len(orders) <= 150)
        for order in orders:
            self.assertIn(order['customer_id'], self.customers)
            self.assertTrue(day_start(self.day) + timedelta(hours=9) <= order['created_at']
                            < day_start(self.day) + timedelta(hours=17))
            self.assertTrue(1 <= len(order['items']) <= 5)
            self.assertEqual(len(order['items']), len({i['product_id'] for i in order['items']}))
            self.assertTrue(all(1 <= i['quantity'] <= 20 for i in order['items']))
            self.assertEqual(order['total_amount'], sum(i['quantity'] * Decimal('0.35') for i in order['items']))

    def test_invalid_config_and_missing_master_data(self):
        for bounds in ({'min': 0, 'max': 1}, {'min': 5, 'max': 1}, {'min': True, 'max': 5}):
            config = copy.deepcopy(self.config)
            config['orders_per_day'] = bounds
            with self.assertRaises(ValueError):
                validate_config(config)
        for customers, products in (([], self.products), (self.customers, self.products[:4])):
            with self.assertRaises(ValueError):
                build_orders(self.config, self.day, customers, products)

    def test_reject_total_overflow(self):
        for product in self.products:
            product['unit_price'] = Decimal('9999999999.99')
        self.config['quantity_per_item'] = {'min': 2, 'max': 2}
        with self.assertRaisesRegex(ValueError, 'total exceeds'):
            build_orders(self.config, self.day, self.customers, self.products)


if __name__ == '__main__':
    unittest.main()
