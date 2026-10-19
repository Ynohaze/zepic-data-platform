"""Build reproducible master data; preview by default, write with --apply."""

import argparse
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
import os
from pathlib import Path
import random

ROOT = Path(__file__).resolve().parents[2]
TABLES = (
    'customers', 'products', 'machines', 'inventory', 'orders', 'order_items',
    'production_orders', 'production_runs', 'maintenance_records',
)


def positive_integer(value, name, minimum=1):
    if type(value) is not int or value < minimum:
        raise ValueError(f'{name} must be an integer >= {minimum}')
    return value


def build_data(config):
    """Validate all configuration before connecting; IDs remain database-owned."""
    seed = config['seed']
    if type(seed) is not int:
        raise ValueError('seed must be an integer')
    start = datetime.fromisoformat(config['start_time'])
    if start.tzinfo is None:
        raise ValueError('start_time must include a timezone')
    start = start.astimezone(timezone.utc)
    tiers = config['customer_tiers']
    if not isinstance(tiers, dict) or set(tiers) != {'standard', 'silver', 'gold'}:
        raise ValueError('customer_tiers must specify standard, silver and gold counts')
    tier_values = []
    for tier in ('standard', 'silver', 'gold'):
        tier_values.extend([tier] * positive_integer(tiers[tier], tier, minimum=0))
    if not tier_values:
        raise ValueError('At least one customer is required')
    machine_count = positive_integer(config['machine_count'], 'machine_count')
    choices = config['inventory_batch_choices']
    if not isinstance(choices, list) or not choices:
        raise ValueError('inventory_batch_choices must be a nonempty list')
    for value in choices:
        positive_integer(value, 'inventory batch multiplier', minimum=0)

    products = []
    seen = set()
    for product in config['products']:
        sku, name = product['sku'], product['product_name']
        if not isinstance(sku, str) or not sku.strip() or len(sku) > 50 or sku in seen:
            raise ValueError('Product SKUs must be nonempty, unique and <= 50 characters')
        if not isinstance(name, str) or not name.strip() or len(name) > 200:
            raise ValueError('Product names must be nonempty and <= 200 characters')
        seen.add(sku)
        if not isinstance(product['unit_price'], str):
            raise ValueError('unit_price must be a decimal string')
        price = Decimal(product['unit_price'])
        if not price.is_finite() or not 0 <= price < Decimal('10000000000'):
            raise ValueError('unit_price must fit nonnegative NUMERIC(12,2)')
        if price != price.quantize(Decimal('0.01')):
            raise ValueError('unit_price must have at most two decimal places')
        batch = positive_integer(product['batch_multiple'], 'batch_multiple')
        if batch > 2147483647 or batch * max(choices) > 2147483647:
            raise ValueError('Batch and inventory quantities must fit PostgreSQL INTEGER')
        products.append(dict(sku=sku, product_name=name, unit_price=price, batch_multiple=batch))
    if not products:
        raise ValueError('At least one product is required')

    # Separate streams keep inventory stable when customer counts change.
    customer_rng, stock_rng = random.Random(seed), random.Random(seed + 1)
    customer_rng.shuffle(tier_values)
    customers = [dict(customer_name=f'Synthetic Customer {i:03d}', customer_tier=tier)
                 for i, tier in enumerate(tier_values, 1)]
    machines = [dict(machine_code=f'MCH-{i:03d}', machine_name=f'General Machine {i:03d}')
                for i in range(1, machine_count + 1)]
    inventory = [dict(sku=p['sku'], available_quantity=stock_rng.choice(choices) * p['batch_multiple'])
                 for p in products]
    return dict(start_time=start, customers=customers, products=products,
                machines=machines, inventory=inventory)


def populate(connection, data):
    """All-or-nothing bootstrap; serialize writes before checking every table."""
    from psycopg import sql

    with connection.transaction():
        with connection.cursor() as cur:
            cur.execute("SET LOCAL lock_timeout = '5s'")
            cur.execute(sql.SQL('LOCK TABLE {} IN EXCLUSIVE MODE').format(
                sql.SQL(', ').join(sql.Identifier('public', t) for t in TABLES)))
            for table in TABLES:
                cur.execute(sql.SQL('SELECT EXISTS (SELECT 1 FROM {})').format(
                    sql.Identifier('public', table)))
                if cur.fetchone()[0]:
                    raise ValueError(f'Initialization refused: {table} is not empty. No rows changed.')
            stamp = data['start_time']
            cur.executemany(
                'INSERT INTO public.customers (customer_name, customer_tier, created_at, updated_at) VALUES (%s,%s,%s,%s)',
                [(c['customer_name'], c['customer_tier'], stamp, stamp) for c in data['customers']])
            product_ids = {}
            for p in data['products']:
                cur.execute(
                    'INSERT INTO public.products (sku, product_name, unit_price, batch_multiple, is_active, created_at, updated_at) '
                    'VALUES (%s,%s,%s,%s,TRUE,%s,%s) RETURNING product_id',
                    (p['sku'], p['product_name'], p['unit_price'], p['batch_multiple'], stamp, stamp))
                product_ids[p['sku']] = cur.fetchone()[0]
            cur.executemany(
                'INSERT INTO public.machines (machine_code, machine_name, status, created_at, updated_at) '
                "VALUES (%s,%s,'available',%s,%s)",
                [(m['machine_code'], m['machine_name'], stamp, stamp) for m in data['machines']])
            cur.executemany(
                'INSERT INTO public.inventory (product_id, available_quantity, created_at, updated_at) VALUES (%s,%s,%s,%s)',
                [(product_ids[i['sku']], i['available_quantity'], stamp, stamp) for i in data['inventory']])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=ROOT / 'config/initial_data.json')
    parser.add_argument('--apply', action='store_true', help='Write to an empty database in one transaction')
    args = parser.parse_args()
    try:
        config = json.loads(args.config.read_text())
        data = build_data(config)
    except (OSError, ValueError, KeyError, TypeError, InvalidOperation) as exc:
        parser.exit(2, f'Invalid configuration: {exc}\n')
    summary = dict(seed=config['seed'], start_time=data['start_time'].isoformat(),
                   counts={t: len(data[t]) for t in ('customers', 'products', 'machines', 'inventory')},
                   customer_tiers=dict(Counter(c['customer_tier'] for c in data['customers'])),
                   inventory=data['inventory'])
    print(json.dumps(summary, indent=2))
    if not args.apply:
        print('Preview only. Use --apply to initialize an empty database.')
        return
    try:
        import psycopg
        from dotenv import load_dotenv
    except ImportError:
        parser.exit(2, 'Install requirements.txt before using --apply.\n')
    load_dotenv(ROOT / '.env', override=False)
    required = ('POSTGRES_DB', 'POSTGRES_USER', 'POSTGRES_PASSWORD')
    if any(not os.getenv(key) for key in required):
        parser.exit(2, 'Set POSTGRES_DB, POSTGRES_USER and POSTGRES_PASSWORD in .env or environment.\n')
    try:
        with psycopg.connect(dbname=os.environ['POSTGRES_DB'], user=os.environ['POSTGRES_USER'],
                              password=os.environ['POSTGRES_PASSWORD'],
                              host=os.getenv('POSTGRES_HOST', 'localhost'),
                              port=os.getenv('POSTGRES_PORT', '5432'), connect_timeout=5) as conn:
            populate(conn, data)
    except ValueError as exc:
        parser.exit(1, f'{exc}\n')
    except psycopg.Error as exc:
        # Avoid emitting credentials or connection strings in logs.
        parser.exit(1, f'Database initialization failed ({type(exc).__name__}, SQLSTATE {exc.sqlstate}). '
                    'Transaction rolled back; check connectivity and schema.\n')
    print('Committed initial customers, products, machines and inventory.')


if __name__ == '__main__':
    main()
