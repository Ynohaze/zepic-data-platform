"""Run consecutive complete UTC simulation days; preview the schedule by default."""

import argparse
from datetime import date, timedelta
from decimal import InvalidOperation
import json
import os
from pathlib import Path

if __package__:
    from .generate_daily_operations import day_start, run_day, validate_config as validate_daily
    from .run_production_planning import run_cycle, validate_config as validate_production
else:
    from generate_daily_operations import day_start, run_day, validate_config as validate_daily
    from run_production_planning import run_cycle, validate_config as validate_production

ROOT = Path(__file__).resolve().parents[2]


def dates(start, days):
    if type(days) is not int or not 1 <= days <= 366:
        raise ValueError('days must be an integer between 1 and 366')
    return [start + timedelta(days=i) for i in range(days)]


def schedule(connection, daily_config, start, days, resume=False):
    """Infer progress for the all-success MVP; retained order history is required."""
    requested = dates(start, days)
    first = date.fromisoformat(daily_config['first_date'])
    if start < first:
        raise ValueError(f'Range starts before configured first_date {first}')
    with connection.cursor() as cur:
        cur.execute("SELECT (created_at AT TIME ZONE 'UTC')::date FROM public.orders GROUP BY 1 ORDER BY 1")
        history = [row[0] for row in cur.fetchall()]
        latest = history[-1] if history else None
        if history and (history[0] != first or (latest - first).days + 1 != len(history)):
            raise ValueError('Order history is not contiguous from first_date; cannot infer safe resume')
        cur.execute("SELECT EXISTS (SELECT 1 FROM public.production_orders WHERE status <> 'completed') "
                    "OR EXISTS (SELECT 1 FROM public.production_runs WHERE status='running')")
        if cur.fetchone()[0]:
            raise ValueError('Unfinished production exists; resolve it before multi-day simulation')
        incomplete = False
        if latest:
            cur.execute("SELECT EXISTS (SELECT 1 FROM public.orders WHERE status='pending' AND created_at < %s)",
                        (day_start(latest),))
            if cur.fetchone()[0]:
                raise ValueError('Older pending orders indicate inconsistent simulation history')
            cur.execute("SELECT count(*) FROM public.orders WHERE status='confirmed'")
            incomplete = cur.fetchone()[0] > 0
            if incomplete:
                cur.execute('SELECT EXISTS (SELECT 1 FROM public.production_orders WHERE planned_start_date >= %s) '
                            "OR EXISTS (SELECT 1 FROM public.orders WHERE status='shipped' AND updated_at >= %s)",
                            (latest, day_start(latest)))
                if cur.fetchone()[0]:
                    raise ValueError('Confirmed backlog after recorded production is outside all-success resume rules')
                if not resume or not start <= latest <= requested[-1]:
                    raise ValueError(f'Latest date {latest} needs production; include it with --resume')
        next_date = latest + timedelta(days=1) if latest else first
        if start > next_date:
            raise ValueError(f'Cannot skip days; next date is {next_date}')
        if latest and start <= latest and not resume:
            raise ValueError('Range contains existing dates; use --resume to retain and skip completed days')
        return [dict(date=day.isoformat(), action=(
            'finish_production' if day == latest and incomplete else
            'skip_completed' if latest and day <= latest else 'run_day')) for day in requested]


def simulate(connection, daily_config, production_config, start, days, resume=False, apply=False, on_day=None):
    """Autocommit connection required: each outer transaction commits exactly one day."""
    if not connection.autocommit:
        raise ValueError('Use an autocommit connection so completed days commit independently')
    validate_daily(daily_config)
    validate_production(production_config)
    with connection.transaction():
        connection.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
        actions = schedule(connection, daily_config, start, days, resume)
    if not apply:
        return actions
    results = []
    for index, planned in enumerate(actions):
        day = date.fromisoformat(planned['date'])
        with connection.transaction():
            connection.execute("SET LOCAL lock_timeout = '5s'")
            # Acquire the complete bootstrap lock order before either nested helper.
            # This also prevents inventory/order lock inversion with standalone scripts.
            connection.execute('LOCK TABLE public.customers, public.products, public.machines, '
                               'public.inventory, public.orders, public.order_items, public.production_orders, '
                               'public.production_runs, public.maintenance_records IN EXCLUSIVE MODE')
            # Recheck under locks: another process may have advanced after preview.
            action = schedule(connection, daily_config, day, len(actions) - index, resume)[0]['action']
            result = dict(date=day.isoformat(), action=action)
            if action != 'skip_completed':
                if action == 'run_day':
                    result['orders'] = run_day(connection, daily_config, day, apply=True)
                production = run_cycle(connection, production_config, day, apply=True)
                if production['confirmed_orders_remaining']:
                    raise ValueError('Successful cycle left confirmed demand; rolling back this day')
                result['production'] = {k: v for k, v in production.items() if k != 'plans'}
        results.append(result)
        if on_day:
            on_day(result)  # Report only after the outer commit succeeds.
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--start-date', required=True, type=date.fromisoformat)
    parser.add_argument('--days', type=int, default=30)
    parser.add_argument('--daily-config', type=Path, default=ROOT / 'config/daily_operations.json')
    parser.add_argument('--production-config', type=Path, default=ROOT / 'config/production.json')
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    try:
        dates(args.start_date, args.days)
        daily = json.loads(args.daily_config.read_text())
        production = json.loads(args.production_config.read_text())
        validate_daily(daily)
        validate_production(production)
    except (OSError, ValueError, KeyError, TypeError, InvalidOperation, OverflowError) as exc:
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
            results = simulate(conn, daily, production, args.start_date, args.days, args.resume, args.apply,
                               on_day=lambda row: print(json.dumps(row), flush=True))
    except ValueError as exc:
        parser.exit(1, f'{exc}. Prior committed days remain; no partial new day was committed.\n')
    except psycopg.Error as exc:
        parser.exit(1, f'Simulation stopped ({type(exc).__name__}, SQLSTATE {exc.sqlstate}). '
                    'Prior committed days remain; retry the same range with --resume after resolving the failure.\n')
    if not args.apply:
        print(json.dumps(results, indent=2))
        print('Schedule preview only; no generated-data forecast, no writes or sequence changes.')
    else:
        print(json.dumps(dict(requested_days=len(results),
                              completed_days=sum(r['action'] != 'skip_completed' for r in results),
                              skipped_days=sum(r['action'] == 'skip_completed' for r in results))))


if __name__ == '__main__':
    main()
