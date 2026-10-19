# Zepic ETL phase closeout

Closeout date: 2026-10-18 (Australia/Perth).

## Outcome

The first portfolio phase now demonstrates a complete, locally operated ETL path:

```text
Operational PostgreSQL
  -> allowlisted FastAPI source
  -> per-table incremental extraction
  -> append-only Raw versions and transactional watermarks
  -> versioned row-quality gate
  -> order-header/line reconciliation gate
  -> dbt physical Silver tables
  -> dbt analytical Gold tables
```

Airflow orchestrates that path as one manual `zepic_raw_to_gold` DAG. The nine
source tables extract in parallel before strict validation, reconciliation and
the dbt build. The business logic remains in independently testable CLI entry
points rather than in the DAG definition.

## Final data refresh

One new simulation day was committed for February 3, 2026:

| Change | Result |
| --- | ---: |
| New orders | 126 |
| New order items | 384 |
| Prior-day orders confirmed and shipped | 108 |
| Production orders / runs | 20 / 20 |
| Units produced | 3,420 |

The next valid simulation date is February 4, 2026. No later date was generated.

## Verified checkpoint

| Layer | Evidence after the closeout run |
| --- | ---: |
| Operational orders / order items | 3,468 / 10,334 |
| Completed production orders / runs | 610 / 610 |
| Shipped / pending orders | 3,342 / 126 |
| Available inventory units | 800 |
| Raw records / completed extraction batches | 15,551 / 72 |
| Completed quality runs / findings | 72 / 0 |
| Reconciled orders | 3,468 matched, 0 mismatched, missing or skipped |
| Gold orders / order lines / production runs | 3,468 / 10,334 / 610 |

The full isolated PostgreSQL regression suite passed 61/61 tests. The warehouse
build completed 111/111 dbt resources with zero warnings, errors or skips.

## Airflow closeout run

Run ID: `manual__etl_phase_closeout_2026-10-18`

- Started: 2026-10-18 18:43:35 Australia/Perth.
- Finished: 2026-10-18 18:44:51 Australia/Perth.
- Result: success, 14/14 task instances successful.
- The DAG was returned to its paused state after verification.

![Successful Airflow Raw-to-Gold DAG](docs/images/airflow-etl-closeout-success.jpg)

The Grid view provides the run identifier, duration and successful task-instance
evidence in one frame:

![Airflow closeout Grid view](docs/images/airflow-etl-closeout-grid.jpg)

### Retry observation and hardening

Several extraction tasks required their configured retries. Logs identified
PostgreSQL `DeadlockDetected` errors while parallel tasks simultaneously applied
the idempotent Raw schema before extraction. All affected transactions rolled
back and the run recovered automatically; no duplicate Raw rows or watermark
advancement occurred.

This validated the retry and transaction boundary, but routine extraction should
not depend on DDL retries. The DAG was therefore hardened without adding another
task: `start` now initializes or upgrades the Raw schema once, and the nine
downstream extraction tasks skip schema DDL and retain their data-level retries.

The corrected empty-increment run
`manual__etl_final_smoke_2026-10-18` completed 14/14 tasks in 9.695 seconds. Every
task succeeded on its first attempt, Raw row count stayed at 15,551, all nine
empty batches were validated, reconciliation remained clean, and dbt again
passed 111/111 resources.

![Final Airflow smoke run after schema hardening](docs/images/airflow-final-smoke-success.jpg)

## Warehouse lineage

The generated dbt catalog contains 18 physical table models, 93 data tests and
three Raw sources. The lineage shows Raw quality-controlled inputs feeding typed
Silver staging tables and then the Gold dimensions, facts and inventory model.

![dbt Raw-to-Gold lineage](docs/images/dbt-lineage-raw-to-gold.jpg)

The generated interactive files remain under the ignored `warehouse/target/`
directory and can be recreated with:

```sh
.venv/bin/python scripts/run_warehouse.py docs generate
.venv/bin/python scripts/run_warehouse.py docs serve
```

## Portfolio narrative

The strongest concise description is:

> Built a restart-safe Raw-to-Gold ETL pipeline using FastAPI, PostgreSQL, dbt
> and Airflow, with incremental cursor extraction, transactional watermarks,
> versioned quarantine rules, cross-table reconciliation, 111 dbt build checks
> and task-level failure recovery.

The project should not be described as CDC: it incrementally extracts current
state, so transitions that occur entirely between pulls are not observable.

## Recommended next phase: analytical data agent

A small dashboard plus chat interface is feasible and strengthens the portfolio
when implemented as a governed consumer of Gold, not as an unrestricted chatbot.
The recommended MVP is:

1. A dashboard backed only by `zepic_analytics`, showing order volume and value,
   fulfilment status, production plan-versus-actual and current inventory.
2. A chat endpoint that maps questions to a small approved semantic layer or
   read-only SQL templates, with row limits and query timeouts.
3. Every response displays the metric definition, generated SQL, source model
   and result timestamp so answers remain auditable.
4. The database role is read-only and restricted to Gold; arbitrary writes,
   operational-table access and unrestricted SQL execution are excluded.

For a resume, this adds the valuable story that the platform does not stop at
warehouse loading: it exposes governed analytical consumption. The impact comes
from traceability and guardrails. A generic chatbox without a semantic contract
would add UI breadth but little data-engineering credibility.

## Closeout state

The ETL phase is technically complete. The DAG is paused, Airflow services remain
available locally for review, and the next simulation date remains February 4,
2026. The next development scope should begin with the governed dashboard/data-
agent MVP rather than extending the ETL source model.
