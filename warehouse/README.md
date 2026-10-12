# Zepic warehouse

This dbt project transforms Phase 3's `zepic_raw.validated_records` view into
typed current-state models. It never reads the operational tables directly and
does not update raw records, extraction watermarks, or quality findings.

## Layers and grain

- `zepic_staging`: nine views, one per source table. Each view selects the latest
  validated raw version per source primary key using `source_updated_at` and
  `batch_id`, then casts JSON payload fields to warehouse types.
- `zepic_analytics`: current dimensions, order-line and operations facts, current
  inventory, and daily order aggregates.
- `fct_order_lines` is one row per order item and uses its captured unit price.
- `fct_production_runs` is one row per production run.
- `fct_maintenance` is one row per maintenance record and is currently empty.
- `current_inventory` is a balance table, not a periodic snapshot.
- `agg_daily_orders` groups by order creation date and current order status. A
  historical date can therefore change when a pending order is later shipped.

The source is current-state incremental extraction rather than CDC. dbt can
rebuild the latest observed state but cannot reconstruct status transitions or
inventory movements that were never extracted.

## Run locally

Install dependencies, validate every raw batch, and then build the warehouse:

```sh
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python scripts/validate_raw.py
.venv/bin/python scripts/reconcile_order_totals.py
.venv/bin/python scripts/run_warehouse.py build
```

The wrapper loads PostgreSQL settings from the ignored root `.env` file and uses
the committed environment-variable profile. Other dbt commands work the same way:

```sh
.venv/bin/python scripts/run_warehouse.py debug
.venv/bin/python scripts/run_warehouse.py test
.venv/bin/python scripts/run_warehouse.py docs generate
```

`dbt build` creates or replaces only the `zepic_staging` and `zepic_analytics`
schemas. Tests cover keys, relationships, status domains, nonnegative inventory,
production terminal fields, and exact order-header versus line-total agreement.
