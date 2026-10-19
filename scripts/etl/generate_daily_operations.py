"""Preview or apply one UTC simulation day of order creation and confirmation."""

import argparse
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
import json
import os
from pathlib import Path
import random

ROOT = Path(__file__).resolve().parents[2]
MONEY_LIMIT = Decimal('10000000000')


def validate_config(config):
    if type(config['seed']) is not int:
        raise ValueError('seed must be an integer')
    date.fromisoformat(config['first_date'])
    for name in ('orders_per_day', 'items_per_order', 'quantity_per_item'):
        low, high = config[name]['min'], config[name]['max']
        if type(low) is not int or type(high) is not int or not 1 <= low <= high <= 2147483647:
            raise ValueError(f'{name} must have integer bounds 1 <= min <= max <= 2147483647')


def day_start(day):
    return datetime.combine(day, time.min, tzinfo=timezone.utc)


def build_orders(config, day, customers, products):
    """Repeatable for the same day, config and ordered source snapshot."""
    validate_config(config)
    if not customers:
        raise ValueError('No customers found; initialize Phase 1.3 first')
    customers = sorted(customers)
    products = sorted(products, key=lambda p: p['sku'])
    if len(products) < config['items_per_order']['max']:
        raise ValueError('Not enough active products for items_per_order.max')
    for product in products:
        price = product['unit_price']
        if not isinstance(price, Decimal) or not price.is_finite() or not 0 <= price < MONEY_LIMIT:
            raise ValueError('Product prices must be finite nonnegative Decimal amounts')
    # String seed is stable across processes; do not use Python's salted hash().
    rng = random.Random(f"zepic-daily-v1:{config['seed']}:{day.isoformat()}")

    def draw(name):
        return rng.randint(config[name]['min'], config[name]['max'])

    orders = []
    for _ in range(draw('orders_per_day')):
        customer_id = rng.choice(customers)
        stamp = day_start(day) + timedelta(hours=9, seconds=rng.randrange(8 * 3600))
        items = [dict(product_id=p['product_id'], quantity=draw('quantity_per_item'),
                      unit_price=p['unit_price'])
                 for p in rng.sample(products, draw('items_per_order'))]
        total = sum((i['quantity'] * i['unit_price'] for i in items), Decimal('0.00'))
        if total >= MONEY_LIMIT:
            raise ValueError('Generated order total exceeds NUMERIC(12,2); reduce quantities or prices')
        orders.append(dict(customer_id=customer_id, created_at=stamp, total_amount=total, items=items))
    return sorted(orders, key=lambda order: order['created_at'])


def run_day(connection, config, day, apply=False):
    """Commit a complete day, or read a consistent snapshot without any writes."""
    validate_config(config)
    start = day_start(day)
    confirmation_time = start + timedelta(hours=8)
    with connection.transaction():
        with connection.cursor() as cur:
            if apply:
                cur.execute("SET LOCAL lock_timeout = '5s'")
                # Match Phase 1.3's lock ordering. Freeze source prices/customer IDs
                # and serialize the day guard with all order writes.
                cur.execute('LOCK TABLE public.customers, public.products IN SHARE MODE')
                cur.execute('LOCK TABLE public.orders, public.order_items IN EXCLUSIVE MODE')
            else:
                cur.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')

            cur.execute('SELECT max(created_at), max(updated_at) FROM public.orders')
            latest_created, latest_updated = cur.fetchone()
            expected = date.fromisoformat(config['first_date'])
            if latest_created is not None:
                expected = latest_created.astimezone(timezone.utc).date() + timedelta(days=1)
            if day != expected:
                raise ValueError(f'Expected next simulation date {expected}; got {day}. '
                                 'Repeated, skipped and out-of-order days are refused.')
            if latest_updated is not None and latest_updated >= confirmation_time:
                raise ValueError('Existing order updates reach this simulation day or later; refusing to move time backwards')

            cur.execute('SELECT customer_id, created_at FROM public.customers ORDER BY customer_id')
            customer_rows = cur.fetchall()
            cur.execute('SELECT product_id, sku, unit_price, created_at FROM public.products '
                        'WHERE is_active = TRUE ORDER BY sku')
            product_rows = cur.fetchall()
            if any(row[-1] > start for row in customer_rows + product_rows):
                raise ValueError('Simulation date precedes master data creation; adjust first_date')
            products = [dict(product_id=p[0], sku=p[1], unit_price=p[2]) for p in product_rows]
            orders = build_orders(config, day, [c[0] for c in customer_rows], products)
            cur.execute("SELECT count(*) FROM public.orders WHERE status = 'pending' AND created_at < %s", (start,))
            confirmations = cur.fetchone()[0]
            if apply:
                cur.execute("UPDATE public.orders SET status = 'confirmed', updated_at = %s "
                            "WHERE status = 'pending' AND created_at < %s", (confirmation_time, start))
                for order in orders:
                    stamp = order['created_at']
                    cur.execute(
                        'INSERT INTO public.orders (customer_id, status, total_amount, created_at, updated_at) '
                        "VALUES (%s,'pending',%s,%s,%s) RETURNING order_id",
                        (order['customer_id'], order['total_amount'], stamp, stamp))
                    order_id = cur.fetchone()[0]
                    cur.executemany(
                        'INSERT INTO public.order_items '
                        '(order_id, product_id, quantity, unit_price, created_at, updated_at) '
                        'VALUES (%s,%s,%s,%s,%s,%s)',
                        [(order_id, i['product_id'], i['quantity'], i['unit_price'], stamp, stamp)
                         for i in order['items']])
            summary = dict(date=day.isoformat(), seed=config['seed'], applied=apply,
                           orders_created=len(orders),
                           order_items_created=sum(len(o['items']) for o in orders),
                           orders_confirmed=confirmations,
                           new_order_total=str(sum((o['total_amount'] for o in orders), Decimal('0.00'))),
                           confirmation_time=confirmation_time.isoformat())
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--date', required=True, type=date.fromisoformat, help='Simulation date, YYYY-MM-DD (UTC)')
    parser.add_argument('--config', type=Path, default=ROOT / 'config/daily_operations.json')
    parser.add_argument('--apply', action='store_true', help='Confirm older orders and insert this day atomically')
    args = parser.parse_args()
    try:
        config = json.loads(args.config.read_text())
        validate_config(config)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(2, f'Invalid configuration: {exc}\n')
    try:
        import psycopg
        from dotenv import load_dotenv
    except ImportError:
        parser.exit(2, 'Install requirements.txt before running this script.\n')
    load_dotenv(ROOT / '.env', override=False)
    if any(not os.getenv(key) for key in ('POSTGRES_DB', 'POSTGRES_USER', 'POSTGRES_PASSWORD')):
        parser.exit(2, 'Set POSTGRES_DB, POSTGRES_USER and POSTGRES_PASSWORD in .env or environment.\n')
    try:
        with psycopg.connect(dbname=os.environ['POSTGRES_DB'], user=os.environ['POSTGRES_USER'],
                              password=os.environ['POSTGRES_PASSWORD'],
                              host=os.getenv('POSTGRES_HOST', 'localhost'),
                              port=os.getenv('POSTGRES_PORT', '5432'), connect_timeout=5,
                              autocommit=True) as conn:
            summary = run_day(conn, config, args.date, apply=args.apply)
    except ValueError as exc:
        parser.exit(1, f'{exc}\n')
    except psycopg.Error as exc:
        parser.exit(1, f'Daily operations failed ({type(exc).__name__}, SQLSTATE {exc.sqlstate}). '
                    'No partial day was committed; check database connectivity and schema.\n')
    print(json.dumps(summary, indent=2))
    print('Day committed.' if args.apply else 'Preview only; no rows or identity sequences changed. Use --apply to write.')


if __name__ == '__main__':
    main()
