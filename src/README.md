# Source API

`source_api.py` exposes the nine operational PostgreSQL tables through a strict
allowlist. It is the only HTTP source used by incremental extraction.

Requests page in ascending `(updated_at, primary key)` order, accept lower and
upper cursor bounds, and enforce a maximum page size of 1,000. Decimal values are
returned as strings and timestamps as UTC ISO-8601 values so extraction does not
lose source precision.

Run it on the host with:

```sh
.venv/bin/uvicorn src.source_api:app --host 127.0.0.1 --port 8000
```

The Compose `source-api` service exposes the same application on localhost port
8000 and makes it available to Airflow as `http://source-api:8000`. This is a
local-development boundary; the API has no authentication and should not be
published to an untrusted network.
