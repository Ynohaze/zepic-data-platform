"""Preview or apply one complete production/fulfillment cycle at 18:00 UTC."""

import argparse
from collections import Counter
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_CEILING
import json
import os
from pathlib import Path
import random

ROOT = Path(__file__).resolve().parents[1]
INTEGER_MAX = 2147483647


def validate_config(config):
    if type(config['seed']) is not int:
        raise ValueError('seed must be an integer')
    if not isinstance(config['buffer_rate'], str):
        raise ValueError('buffer_rate must be a decimal string')
    rate = Decimal(config['buffer_rate'])
    if not rate.is_finite() or not 0 <= rate < 1 or rate != rate.quantize(Decimal('0.0001')):
        raise ValueError('buffer_rate must be in [0, 1) with at most four decimal places')
    if type(config['run_minutes']) is not int or not 1 <= config['run_minutes'] <= 357:
        raise ValueError('run_minutes must be an integer between 1 and 357')
    return rate


def planned_quantity(demand, available, batch, rate):
    shortage = max(0, demand - available)
    if not shortage:
        return 0, 0
    quantity = int((Decimal(shortage) * (1 + rate) / batch).to_integral_value(rounding=ROUND_CEILING)) * batch
    if shortage > INTEGER_MAX or quantity > INTEGER_MAX or available + quantity > INTEGER_MAX:
        raise ValueError('Planned production or resulting inventory exceeds INTEGER capacity')
    return shortage, quantity


def plan_shipments(orders, stock):
    """Oldest-first attempts: an unfulfillable order consumes nothing."""
    remaining = dict(stock)
    shipped = []
    for order in sorted(orders, key=lambda o: (o['created_at'], o['order_id'])):
        if not order['items']:
            raise ValueError('Confirmed order has no items')
        if all(remaining.get(i['product_id'], 0) >= i['quantity'] for i in order['items']):
            for item in order['items']:
                remaining[item['product_id']] -= item['quantity']
            shipped.append(order['order_id'])
    return shipped, remaining


def build_plan(config, day, orders, products, machines):
    rate = validate_config(config)
    stamp = datetime.combine(day, time(18), tzinfo=timezone.utc)
    demand = Counter()
    for order in orders:
        for item in order['items']:
            demand[item['product_id']] += item['quantity']
    by_id = {p['product_id']: p for p in products}
    if any(pid not in by_id or by_id[pid]['available_quantity'] is None for pid in demand):
        raise ValueError('Every demanded product must have an inventory row')
    plans = []
    for pid in sorted(demand, key=lambda pid: by_id[pid]['sku']):
        product = by_id[pid]
        shortage, quantity = planned_quantity(demand[pid], product['available_quantity'], product['batch_multiple'], rate)
        if shortage:
            if not product['is_active']:
                raise ValueError(f"Inactive product {product['sku']} needs production; resolve it first")
            plans.append(dict(product_id=pid, sku=product['sku'], shortage_quantity=shortage,
                              planned_quantity=quantity, batch_multiple=product['batch_multiple']))
    if plans and not machines:
        raise ValueError('Shortages exist but no available machine can run; no changes made')
    machines = sorted(machines, key=lambda m: m['machine_code'])
    random.Random(f"zepic-production-v1:{config['seed']}:{day}").shuffle(machines)
    stock = {p['product_id']: p['available_quantity'] for p in products if p['available_quantity'] is not None}
    for index, plan in enumerate(plans):
        start = stamp + timedelta(minutes=1 + (index // len(machines)) * config['run_minutes'])
        end = start + timedelta(minutes=config['run_minutes'])
        plan.update(machine_id=machines[index % len(machines)]['machine_id'], start_time=start, end_time=end)
        stock[plan['product_id']] += plan['planned_quantity']
    ship_time = max((p['end_time'] for p in plans), default=stamp) + timedelta(minutes=1)
    if ship_time.date() != day:
        raise ValueError('Cycle cannot finish today; shorten runs or provide more available machines')
    shipped, remaining = plan_shipments(orders, stock)
    return dict(planned_at=stamp, buffer_rate=rate, production=plans, shipped=shipped,
                shipping_time=ship_time, stock_after_production=stock, remaining_stock=remaining,
                confirmed_orders=len(orders))


def run_cycle(connection, config, day, apply=False):
    validate_config(config)
    stamp = datetime.combine(day, time(18), tzinfo=timezone.utc)
    with connection.transaction():
        with connection.cursor() as cur:
            if apply:
                cur.execute("SET LOCAL lock_timeout = '5s'")
                # Same relative table order as initial bootstrap and daily orders.
                cur.execute('LOCK TABLE public.products, public.machines IN SHARE MODE')
                cur.execute('LOCK TABLE public.inventory, public.orders, public.order_items, '
                            'public.production_orders, public.production_runs IN EXCLUSIVE MODE')
                cur.execute('LOCK TABLE public.maintenance_records IN SHARE MODE')
            else:
                cur.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
            cur.execute('SELECT max(created_at) FROM public.orders')
            latest = cur.fetchone()[0]
            if latest is None or latest.astimezone(timezone.utc).date() != day:
                raise ValueError('Run production for the latest generated order date, after daily operations')
            cur.execute("SELECT EXISTS (SELECT 1 FROM public.production_orders WHERE status <> 'completed') "
                        "OR EXISTS (SELECT 1 FROM public.production_runs WHERE status = 'running')")
            if cur.fetchone()[0]:
                raise ValueError('Unfinished production exists; resolve it before the next cycle')
            cur.execute('SELECT EXISTS (SELECT 1 FROM public.production_orders WHERE planned_start_date >= %s) '
                        "OR EXISTS (SELECT 1 FROM public.orders WHERE status = 'shipped' AND updated_at >= %s)", (day, stamp))
            if cur.fetchone()[0]:
                raise ValueError('Production/fulfillment cycle already recorded for this date or later')
            # Never rewrite a later source state with historical simulation times.
            cur.execute('SELECT max(updated_at) FROM ('
                        'SELECT updated_at FROM public.orders UNION ALL SELECT updated_at FROM public.order_items '
                        'UNION ALL SELECT updated_at FROM public.inventory UNION ALL SELECT updated_at FROM public.products '
                        'UNION ALL SELECT updated_at FROM public.machines UNION ALL SELECT updated_at FROM public.production_orders '
                        'UNION ALL SELECT updated_at FROM public.production_runs UNION ALL SELECT updated_at FROM public.maintenance_records) events')
            newest = cur.fetchone()[0]
            if newest is not None and newest >= stamp:
                raise ValueError('Source updates reach cycle time or later; refusing to move time backwards')
            cur.execute("SELECT order_id, created_at FROM public.orders WHERE status = 'confirmed' ORDER BY created_at, order_id")
            orders = {r[0]: dict(order_id=r[0], created_at=r[1], items=[]) for r in cur.fetchall()}
            cur.execute("SELECT i.order_id, i.product_id, i.quantity FROM public.order_items i "
                        "JOIN public.orders o USING(order_id) WHERE o.status = 'confirmed' ORDER BY i.order_id, i.product_id")
            for oid, pid, quantity in cur.fetchall():
                orders[oid]['items'].append(dict(product_id=pid, quantity=quantity))
            cur.execute('SELECT p.product_id, p.sku, p.batch_multiple, p.is_active, i.available_quantity '
                        'FROM public.products p LEFT JOIN public.inventory i USING(product_id) ORDER BY p.sku')
            products = [dict(zip(('product_id', 'sku', 'batch_multiple', 'is_active', 'available_quantity'), r)) for r in cur.fetchall()]
            cur.execute("SELECT m.machine_id, m.machine_code FROM public.machines m WHERE m.status = 'available' "
                        "AND NOT EXISTS (SELECT 1 FROM public.maintenance_records r WHERE r.machine_id=m.machine_id AND r.status='in_progress')")
            machines = [dict(machine_id=r[0], machine_code=r[1]) for r in cur.fetchall()]
            plan = build_plan(config, day, list(orders.values()), products, machines)
            if apply:
                for p in plan['production']:
                    cur.execute('INSERT INTO public.production_orders '
                                '(product_id, shortage_quantity, batch_multiple, buffer_rate, planned_quantity, planned_start_date, status, created_at, updated_at) '
                                "VALUES (%s,%s,%s,%s,%s,%s,'planned',%s,%s) RETURNING production_order_id",
                                (p['product_id'], p['shortage_quantity'], p['batch_multiple'], plan['buffer_rate'], p['planned_quantity'], day, stamp, stamp))
                    poid = cur.fetchone()[0]
                    cur.execute("UPDATE public.production_orders SET status='released', updated_at=%s WHERE production_order_id=%s",
                                (p['start_time'], poid))
                    cur.execute('INSERT INTO public.production_runs (production_order_id,machine_id,start_time,status,created_at,updated_at) '
                                "VALUES (%s,%s,%s,'running',%s,%s) RETURNING production_run_id",
                                (poid, p['machine_id'], p['start_time'], p['start_time'], p['start_time']))
                    rid = cur.fetchone()[0]
                    cur.execute("UPDATE public.production_runs SET status='completed', actual_quantity=%s, end_time=%s, updated_at=%s WHERE production_run_id=%s",
                                (p['planned_quantity'], p['end_time'], p['end_time'], rid))
                    cur.execute('UPDATE public.inventory SET available_quantity=available_quantity+%s, updated_at=%s WHERE product_id=%s',
                                (p['planned_quantity'], p['end_time'], p['product_id']))
                    cur.execute("UPDATE public.production_orders SET status='completed', updated_at=%s WHERE production_order_id=%s", (p['end_time'], poid))
                # Deduct once per product, at a distinct timestamp after all runs.
                for pid, after in plan['remaining_stock'].items():
                    consumed = plan['stock_after_production'][pid] - after
                    if consumed:
                        cur.execute('UPDATE public.inventory SET available_quantity=available_quantity-%s, updated_at=%s WHERE product_id=%s',
                                    (consumed, plan['shipping_time'], pid))
                if plan['shipped']:
                    cur.execute("UPDATE public.orders SET status='shipped', updated_at=%s WHERE order_id=ANY(%s)",
                                (plan['shipping_time'], plan['shipped']))
            summary = dict(date=day.isoformat(), applied=apply, production_orders=len(plan['production']),
                           production_runs=len(plan['production']),
                           produced_units=sum(p['planned_quantity'] for p in plan['production']),
                           shipped_orders=len(plan['shipped']), confirmed_orders_remaining=len(orders)-len(plan['shipped']),
                           shipping_time=plan['shipping_time'].isoformat(), plans=plan['production'])
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--date', required=True, type=date.fromisoformat)
    parser.add_argument('--config', type=Path, default=ROOT / 'config/production.json')
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    try:
        config = json.loads(args.config.read_text())
        validate_config(config)
    except (OSError, ValueError, KeyError, TypeError, InvalidOperation) as exc:
        parser.exit(2, f'Invalid configuration: {exc}\n')
    import psycopg
    from dotenv import load_dotenv
    load_dotenv(ROOT / '.env', override=False)
    if any(not os.getenv(k) for k in ('POSTGRES_DB', 'POSTGRES_USER', 'POSTGRES_PASSWORD')):
        parser.exit(2, 'Set POSTGRES_DB, POSTGRES_USER and POSTGRES_PASSWORD.\n')
    try:
        with psycopg.connect(dbname=os.environ['POSTGRES_DB'], user=os.environ['POSTGRES_USER'],
                              password=os.environ['POSTGRES_PASSWORD'], host=os.getenv('POSTGRES_HOST', 'localhost'),
                              port=os.getenv('POSTGRES_PORT', '5432'), connect_timeout=5, autocommit=True) as conn:
            summary = run_cycle(conn, config, args.date, apply=args.apply)
    except ValueError as exc:
        parser.exit(1, f'{exc}\n')
    except psycopg.Error as exc:
        parser.exit(1, f'Production failed ({type(exc).__name__}, SQLSTATE {exc.sqlstate}); transaction rolled back.\n')
    print(json.dumps(summary, indent=2, default=str))
    print('Cycle committed.' if args.apply else 'Preview only; no rows or sequences changed.')


if __name__ == '__main__':
    main()
