# Operational scripts

`generate_initial_data.py` previews Phase 1.3 data; add `--apply` to initialize
an empty database. Configuration defaults to `config/initial_data.json` relative
to the repository, regardless of the current working directory.

`generate_daily_operations.py --date YYYY-MM-DD` previews daily orders from
current database master data. Add `--apply` to confirm older pending orders and
insert that day's orders/items in one transaction. Configuration defaults to
`config/daily_operations.json`. Run dates consecutively; repeats are refused.

Phase 1.5 production planning remains future work.
