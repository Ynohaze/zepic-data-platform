# Governed analytics

This package is the safety boundary for Zepic's Level 1 AI analytics feature.
The LLM will propose SQL, but only `QueryEngine` can execute it. `QueryEngine`
always calls `QueryGuard`, opens a read-only transaction, applies a local query
timeout and wraps the accepted statement with a hard result-row limit.

## Local database role

Add unique local values to `.env` before provisioning the role:

```dotenv
ANALYTICS_DB_USER=zepic_analytics_reader
ANALYTICS_DB_PASSWORD=replace_with_a_unique_local_password
ANALYTICS_QUERY_TIMEOUT_MS=5000
ANALYTICS_MAX_ROWS=200
```

After a successful warehouse build, run:

```sh
.venv/bin/python scripts/ai/setup_analytics_role.py
```

The command creates or hardens a non-superuser login, removes its direct
privileges across user schemas, grants `USAGE` and `SELECT` only on
`zepic_analytics`, grants `SELECT` on future Gold tables created by the warehouse
owner, and sets role defaults for read-only transactions, a five-second timeout
and a restricted search path.

The future analytics API must connect with `ANALYTICS_DB_USER` and
`ANALYTICS_DB_PASSWORD`. The administrative `POSTGRES_USER` credentials are only
for provisioning and must never be passed to the LLM-facing application.

## Groq model provider

The initial provider is Groq's free-plan `openai/gpt-oss-20b` endpoint. Add its
server-side API key to `.env`; never expose it in browser code:

```dotenv
GROQ_API_KEY=replace_with_your_groq_key
GROQ_MODEL=openai/gpt-oss-20b
GROQ_REQUEST_TIMEOUT_SECONDS=30
```

`GroqAnalyticsLlm` makes exactly two non-streaming calls per accepted question:
one strict JSON Schema response proposes either a SQL query or a refusal, and a
second strict response explains only the executed rows. Both calls use low
reasoning effort. There is no model-controlled retry or tool loop.

Once both the analytics database role and Groq key are configured, start the API
from the repository root:

```sh
.venv/bin/uvicorn src.analytics_api:app --host 127.0.0.1 --port 8001
```

Open <http://127.0.0.1:8001/dashboard/> for the lightweight Level 1 dashboard.
The headline metrics, order trend, status distribution, production comparison
and low-inventory table come from fixed, guarded Gold queries and remain
independent of the LLM. Their freshness label reports the latest order date and
inventory update represented in Gold, rather than merely the page-load time.

The `Ask data` workspace is single-turn: one question, one validated SQL
statement, one bounded query and one grounded answer. Each successful response
includes a request ID, model name, database time and total time. The application
logs the same operational metadata and status without logging the question,
result rows or credentials. All user-facing failures use one simple error panel.
The disabled `Analysis task` mode reserves the UI boundary for a future bounded
analytical agent without enabling planning or autonomous execution today.

Without `GROQ_API_KEY`, `/health` reports `configuration_required` and the chat
endpoint returns HTTP 503. This keeps ordinary ETL development independent of
external model access.

## Defense in depth

The two controls intentionally overlap:

1. `QueryGuard` parses one PostgreSQL statement and accepts only approved Gold
   relations, read-only query expressions and an explicit analytical-function
   allowlist.
2. PostgreSQL independently prevents writes and does not grant the analytics
   role access to Operational, Raw or Silver tables.

Neither prompt instructions nor keyword matching is treated as a security
boundary.

## Level 1 boundary and evaluation

This is a local, single-user analytical application. It has no login,
multi-tenant isolation, conversational memory, write capability, forecasting,
autonomous planning or background agent loop. Do not expose it directly to an
untrusted network without adding authentication and deployment controls.

`evaluation_cases.json` is the small acceptance contract for supported,
unsupported and hostile questions. Its shape and coverage run with the normal
unit suite; live provider answers remain an explicit acceptance check so routine
tests do not spend API quota or depend on nondeterministic wording.
