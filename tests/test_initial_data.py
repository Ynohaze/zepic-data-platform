import copy
import json
from pathlib import Path
import unittest

from scripts.generate_initial_data import build_data


class InitialDataTests(unittest.TestCase):
    def setUp(self):
        self.config = json.loads((Path(__file__).resolve().parents[1] / 'config/initial_data.json').read_text())

    def test_reproducibility_and_seed_variation(self):
        first = build_data(self.config)
        self.assertEqual(first, build_data(self.config))
        self.config['seed'] += 1
        second = build_data(self.config)
        self.assertNotEqual(first['inventory'], second['inventory'])
        self.assertEqual(first['products'], second['products'])

    def test_default_population(self):
        data = build_data(self.config)
        self.assertEqual([len(data[t]) for t in ('customers', 'products', 'machines', 'inventory')], [100, 20, 5, 20])
        for tier, count in self.config['customer_tiers'].items():
            self.assertEqual(sum(c['customer_tier'] == tier for c in data['customers']), count)
        for product, stock in zip(data['products'], data['inventory']):
            self.assertEqual(product['sku'], stock['sku'])
            self.assertIn(stock['available_quantity'] // product['batch_multiple'], [0, 1, 3, 5])
            self.assertEqual(stock['available_quantity'] % product['batch_multiple'], 0)
        self.assertTrue(any(i['available_quantity'] == 0 for i in data['inventory']))

    def test_customer_changes_do_not_change_inventory(self):
        original = build_data(self.config)
        self.config['customer_tiers']['standard'] += 10
        self.assertEqual(original['inventory'], build_data(self.config)['inventory'])

    def test_reject_invalid_configuration(self):
        invalid = []
        for key, value in [('start_time', '2026-01-01'), ('machine_count', 0),
                           ('seed', True), ('inventory_batch_choices', [-1])]:
            config = copy.deepcopy(self.config)
            config[key] = value
            invalid.append(config)
        for price in ['NaN', '-1', '0.001', '10000000000']:
            config = copy.deepcopy(self.config)
            config['products'][0]['unit_price'] = price
            invalid.append(config)
        config = copy.deepcopy(self.config)
        config['products'][1]['sku'] = config['products'][0]['sku']
        invalid.append(config)
        for config in invalid:
            with self.subTest(config=config), self.assertRaises(ValueError):
                build_data(config)


if __name__ == '__main__':
    unittest.main()
