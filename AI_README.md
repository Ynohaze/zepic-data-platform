# AI phase — governed analytics and bounded agent

[Project README](README.md) | [中文项目说明](README.zh-CN.md) | [ETL phase](ETL_README.md)

The AI phase is a governed consumer of the Gold warehouse. It demonstrates how
an LLM can participate in analytics without becoming a database security or
data-governance boundary.

## Two interaction modes

### Level 1: Quick answer

```text
question
  -> one structured SQL proposal or refusal
  -> deterministic SQL guard
  -> one read-only Gold query
  -> evidence-grounded answer
```

The model may compose joins, aggregates, CTEs and approved window functions, but
it cannot execute SQL itself. One request produces at most one executed query.

### Bounded analytical agent beta

`Analysis task` is a small synchronous agent loop. At each step the model chooses
one structured action: propose a query, finish from collected evidence, or
refuse. It may execute at most `ANALYTICS_AGENT_MAX_STEPS` guarded queries
(default three, hard maximum five).

The UI exposes observable events rather than hidden chain-of-thought:

- planned query and objective;
- guard rejection and subsequent correction;
- executed query, source models, row count and duration;
- early finish or model refusal;
- forced convergence when the query budget is reached;
- final evidence-based synthesis.

Repeated SQL is blocked. Rejected plans are never executed and do not consume
query steps. Up to two guard failures may be corrected before the run stops. T0
does not add a redundant query merely to make the trace look agentic.

## Security and data boundary

The controls intentionally overlap:

1. `QueryGuard` parses exactly one PostgreSQL statement and permits only
   read-only expressions, approved analytical functions and the eight Gold
   relations described by `analytics/semantic_context.json`.
2. PostgreSQL independently gives the analytics login `SELECT` access only to
   `zepic_analytics`, sets transactions read-only, restricts the search path and
   applies a statement timeout.
3. `QueryEngine` adds a hard result-row cap and returns only structured evidence.
4. The LLM receives the question, semantic contract and prior evidence—not
   credentials, Raw/Silver rows or unrestricted tools.

Prompt instructions and keyword matching are not treated as security controls.

## Setup

Add unique local values to the ignored `.env` file:

```dotenv
ANALYTICS_DB_USER=zepic_analytics_reader
ANALYTICS_DB_PASSWORD=replace_with_a_unique_local_password
ANALYTICS_QUERY_TIMEOUT_MS=5000
ANALYTICS_MAX_ROWS=200
ANALYTICS_AGENT_MAX_STEPS=3

GROQ_API_KEY=replace_with_your_groq_key
GROQ_MODEL=openai/gpt-oss-20b
GROQ_REQUEST_TIMEOUT_SECONDS=30
```

Provision the restricted role after the Gold build, then start the API:

```sh
docker compose up -d --wait postgres
.venv/bin/python scripts/ai/setup_analytics_role.py
.venv/bin/uvicorn src.analytics_api:app --host 127.0.0.1 --port 8001
```

Open <http://127.0.0.1:8001/dashboard/>. Headline metrics, order trend, status,
production comparison and current inventory use fixed guarded SQL and remain
available when the model provider is not configured.

Without `GROQ_API_KEY`, `/health` reports `configuration_required` and model-backed
requests return HTTP 503. The browser never receives the provider key.

## Semantic contract

The model is given explicit table grains, relationships, metric definitions and
limitations. Important meanings include:

- order value: sum of `fct_orders.order_total_amount` at order grain;
- ordered units: sum of `fct_order_lines.quantity` at line grain;
- customer value: cumulative booked order value by customer;
- high-value customers: the top quartile by cumulative booked order value;
- product preference: units or booked line value, named explicitly;
- current inventory: latest balance only, never historical stock.

This context is part of the product contract. More capable prompting does not
replace precise field definitions or safe aggregation guidance.

## Runtime behavior

Successful responses include generated SQL, source models, evidence rows,
limitations, request ID, model name, database time and total time. Operational
logs contain metadata and status but not the user question, returned rows or
credentials.

All user-facing failures use one error panel and clear stale results. Guard
rejections and model refusals retain the observable event trail so the user can
see where the bounded run stopped.

## Evaluation

Routine tests validate supported, ambiguous, unsupported and hostile cases
without calling the provider. Live acceptance is explicit because it consumes
quota and model wording is nondeterministic.

```sh
# Full deterministic suite
ZEPIC_DB_TESTS=1 .venv/bin/python -m unittest discover -s tests -v

# One real Groq + read-only database acceptance run
.venv/bin/python scripts/ai/run_agent_acceptance.py
```

The final integrated suite passed 110/110 tests. Browser acceptance verified a
real run with `1 query · 4 events · model finished`, visible evidence and no
console warnings or errors.

## Explicit non-goals

This remains a local, single-user analytical application. It has no write tools,
pipeline triggers, authentication, multi-tenant isolation, long-term memory,
vector retrieval, background execution, forecasting or automated operational
recommendations. It must not be exposed to an untrusted network without adding
production authentication, deployment and observability controls.
