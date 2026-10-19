# Operational scripts

The scripts are stable command-line boundaries used both directly and by the
Airflow DAG. Business logic stays here rather than being duplicated in
orchestration code.

`run_simulation.py --start-date YYYY-MM-DD --days 30` previews a multi-day
schedule. Add `--apply` to commit complete days and `--resume` to skip completed
dates or finish the latest order-only day. A failed new day rolls back both
orders and production; previous days stay committed. Daily JSON summaries are
printed after commit. Use the same configs when resuming.

`generate_initial_data.py` previews Phase 1.3 data; add `--apply` to initialize
an empty database. Configuration defaults to `config/initial_data.json` relative
to the repository, regardless of the current working directory.

`generate_daily_operations.py --date YYYY-MM-DD` previews daily orders from
current database master data. Add `--apply` to confirm older pending orders and
insert that day's orders/items in one transaction. Configuration defaults to
`config/daily_operations.json`. Run dates consecutively; repeats are refused.

`run_production_planning.py --date YYYY-MM-DD` previews production and whole-order
fulfillment for the latest generated order date. Add `--apply` for an atomic
cycle. Defaults live in `config/production.json`; successful output equals plan.
Run daily operations first, then production, before advancing to the next day.

`extract_to_raw.py --table all` pulls every allowlisted API table independently.
Use `--table TABLE` for one source. Each completed batch writes immutable Raw
rows, batch metadata and its table watermark in one transaction. Airflow first
calls `--init-only` in its `start` task, then runs the nine table commands with
schema initialization disabled so parallel data writes never contend with Raw
DDL.

`validate_raw.py --fail-on-findings` applies versioned row-level checks and exits
nonzero while any latest source-key version remains quarantined.

`reconcile_order_totals.py --fail-on-findings` compares the current valid order
headers and items with exact decimal arithmetic. Mismatches, missing items and
quarantine-related skips are stored as findings and fail the strict gate.

`run_warehouse.py build` invokes dbt with the committed project/profile and loads
the root `.env`. It also supports commands such as `parse`, `test`, `debug` and
`docs generate` without requiring callers to reproduce dbt path configuration.
