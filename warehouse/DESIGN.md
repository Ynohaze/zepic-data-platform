# Phase 4 warehouse design

This file is the durable replacement for the Phase 4 side-chat decisions.

## Layer boundary

```text
operational PostgreSQL
        |
        v
zepic_raw.records + Phase 3 quality results       Bronze / Raw
        |
        | rank latest quality-checked raw version, then apply validity
        v
zepic_staging.*                                   Silver tables
        |
        | join, enrich, calculate business measures
        v
zepic_analytics.*                                 Gold tables
```

`zepic_raw.validated_records` remains an append-only view of every raw version
that passed its batch's latest ruleset. It is not itself the current Silver
snapshot. dbt materializes typed `zepic_staging` tables on every build, which is
the project's physical Silver layer. Full table replacement is intentional: at
this data volume it is simpler and safer than maintaining incremental merge
state inside dbt.

## Current-state validity rule

For each source primary key, dbt:

1. ignores batches that do not yet have a completed quality run;
2. ranks all remaining raw versions by `source_updated_at`, then `batch_id`;
3. considers only the latest ranked version;
4. includes the key in Silver only when that exact version is present in
   `validated_records`.

This is the selected "option B" behavior. A quarantined new version suppresses
the entity and makes relationship tests fail visibly when dependants remain. It
never silently resurrects an older valid value. The implementation lives in
`macros/latest_validated_records.sql`; the singular test
`assert_no_quarantined_latest_fallback.sql` protects the rule.

## Silver models

The nine `stg_*` current-state tables correspond to the nine source tables and
cast JSON payloads into explicit warehouse types. `stg_order_versions` is the
one additional Silver history table. It retains every *observed and validated*
order version so Gold can derive lifecycle milestones.

Silver is rebuilt with PostgreSQL's table materialization rather than appended
batch by batch. Raw remains the replayable history and source of truth.

## Gold models and grain

| Model | Grain | Purpose |
| --- | --- | --- |
| `dim_customers` | customer | current customer attributes |
| `dim_products` | product | current catalog attributes and price |
| `dim_machines` | machine | current machine attributes |
| `fct_orders` | order | header amount, item counts, current status, lifecycle milestones |
| `fct_order_lines` | order item | transaction price, quantity, and line amount |
| `fct_production_runs` | production run | plan versus actual production execution |
| `fct_maintenance` | maintenance record | maintenance execution, cost, and duration |
| `current_inventory` | product | current balance and current-price valuation |

`agg_daily_orders` was removed. Grouping an order's creation date by its current
status rewrote historical groups whenever the status changed and did not
represent either an event fact or a stable snapshot. Consumers can aggregate
`fct_orders` explicitly for their required date and status semantics.

## Order lifecycle semantics

All orders are created as `pending`, so `fct_orders.pending_at` equals the
source `created_at`. `confirmed_at` and `shipped_at` are the earliest timestamps
from corresponding versions actually captured by extraction. The three
`*_status_observed` flags distinguish observed milestones from inferred business
progression, and `lifecycle_fully_observed` is true only when all three versions
exist in raw history.

This is not CDC. A status that begins and ends between extraction runs cannot be
reconstructed. A null `confirmed_at` means "not observed", not "never
confirmed". Current local data demonstrates this limitation: no confirmed
versions were captured.

## Build interface and visualization

The transformation source of truth is SQL/YAML code under `warehouse/`. There
is no always-on orchestration dashboard in Phase 4. dbt can generate a local
interactive lineage and documentation site:

```sh
.venv/bin/python scripts/etl/run_warehouse.py docs generate
.venv/bin/python scripts/etl/run_warehouse.py docs serve
```

The generated static entry point is `warehouse/target/index.html`. Phase 5 will
add Airflow orchestration and its run-monitoring UI; it should call the existing
wrapper rather than duplicate warehouse SQL.
