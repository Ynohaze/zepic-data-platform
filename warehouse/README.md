# Zepic warehouse

This dbt project turns append-only raw source versions and Phase 3 quality
results into physical Silver tables and analytics-ready Gold tables. It never
reads operational tables directly and does not update raw records, extraction
watermarks, or quality findings.

## Layers

- `zepic_staging`: ten materialized Silver tables. Nine represent typed current
  state for the source entities; `stg_order_versions` retains all observed valid
  order versions for lifecycle analysis.
- `zepic_analytics`: eight Gold tables: three current dimensions, four business
  facts, and current inventory.
- `fct_orders` is one row per order and contains current header measures plus
  observed lifecycle milestones.
- `fct_order_lines` is one row per order item and uses its captured sale price.
- `fct_production_runs` is one row per production run.
- `fct_maintenance` is one row per maintenance record and is currently empty.
- `current_inventory` is a current balance, not a periodic snapshot.

The old `agg_daily_orders` model is intentionally absent: creation-date groups
by current status were mutable and easy to misinterpret. Aggregate `fct_orders`
with an explicitly chosen event date instead.

Read [DESIGN.md](DESIGN.md) for the complete decisions, including physical
Silver materialization, the no-stale-fallback rule, model grains, and lifecycle
limitations.

## Run locally

Install dependencies, validate every raw batch, and then build the warehouse:

```sh
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python scripts/etl/validate_raw.py
.venv/bin/python scripts/etl/reconcile_order_totals.py
.venv/bin/python scripts/etl/run_warehouse.py build
```

The wrapper loads PostgreSQL settings from the ignored root `.env` file and uses
the committed environment-variable profile. Other dbt commands work the same way:

```sh
.venv/bin/python scripts/etl/run_warehouse.py debug
.venv/bin/python scripts/etl/run_warehouse.py test
.venv/bin/python scripts/etl/run_warehouse.py docs generate
.venv/bin/python scripts/etl/run_warehouse.py docs serve
```

The transformation is code-first. `docs generate` produces an interactive dbt
lineage/catalog site at `target/index.html`; Phase 4 has no always-on dashboard.
Tests cover keys, relationships, domains, latest-invalid suppression, lifecycle
chronology, inventory nonnegativity, production terminal fields, and exact order
header versus line-total agreement.
