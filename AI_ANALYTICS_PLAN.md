# Zepic AI analytics plan

Planning baseline: 2026-10-19 (Australia/Perth).

## Target outcome

Add a governed, single-turn analytical assistant on top of the existing Gold
warehouse. A user asks a natural-language business question, an LLM produces one
read-only PostgreSQL query, application code validates and executes that query,
and the LLM explains only the returned result.

This is **Level 1 AI participation** for this project:

```text
question -> one LLM SQL proposal -> deterministic validation -> read-only Gold query
         -> one grounded answer
```

The model may compose analytical SQL, including joins, aggregates, CTEs and
window functions. It does not receive database credentials, execute tools
directly, modify data, trigger the pipeline, plan multi-step work or run an
autonomous retry loop.

## Locked boundary

### Included

- Natural-language questions over the eight `zepic_analytics` Gold models.
- One PostgreSQL `SELECT` statement per request.
- Aggregation, filtering, joins, CTEs and window functions needed for analysis.
- A physical read-only database role plus application-side SQL validation.
- Query timeout and result-row limits.
- Answers that show the generated SQL, source models, filters, query timestamp
  and limitations relevant to the result.
- A small dashboard covering orders, fulfilment, production and current
  inventory, plus a single-turn chat interface.
- Explicit refusal when the available data cannot support a claim.

### Excluded

- Writes, DDL, transaction control, stored procedure calls or access outside
  `zepic_analytics`.
- Operational actions, simulation runs or Airflow triggers.
- Autonomous multi-step agents, background work, unrestricted retries, long-term
  memory, vector retrieval or user profiling.
- Forecasting, replenishment decisions and automated recommendations.
- Claims of complete lifecycle or inventory history that the warehouse cannot
  support.
- Production deployment features such as SSO, multitenancy, TLS termination,
  secret management and horizontal scaling.

## Delivery phases

### Phase 1 — contract and semantic context

Define the Level 1 boundary, the Gold table grains and relationships, approved
metrics, default business meanings and known limitations in one machine-readable
contract.

Acceptance:

- All eight Gold models and their columns are described.
- Join cardinalities and safe aggregation guidance are explicit.
- Order value, order count, ordered units, production variance and current
  inventory metrics have unambiguous definitions.
- Non-CDC lifecycle and current-inventory limitations are encoded.
- A fast test rejects incomplete or internally inconsistent contract changes.

### Phase 2 — guarded query engine

Build the deterministic boundary between an LLM SQL proposal and PostgreSQL.

Planned work:

- Provision a least-privilege analytics role with `SELECT` access only to Gold.
- Parse SQL into an AST; allow exactly one read-only query and only approved
  relations and operations.
- Apply a transaction-level read-only setting, statement timeout and row cap.
- Return structured rows, columns, source models, elapsed time and truncation
  status.
- Cover valid analytical SQL and adversarial SQL with unit and PostgreSQL tests.

Acceptance: no prompt or SQL text can cause a write, access a non-Gold relation,
execute multiple statements or bypass resource limits.

### Phase 3 — LLM and analysis API

Add a provider boundary and an HTTP API around the guarded query engine.

Planned work:

- Give the LLM only the semantic contract, question and relevant runtime date
  context; never give it database credentials.
- Require structured SQL-proposal output.
- Execute only after deterministic validation.
- Generate the final answer from the question, executed SQL, result rows and
  applicable limitations.
- Return a structured refusal for unsupported questions.
- Keep the provider configurable through environment variables and keep secrets
  out of logs and Git.

Acceptance: supported questions produce grounded answers and audit metadata;
unsupported or unsafe questions do not reach query execution. The service has
no autonomous tool loop.

### Phase 4 — dashboard and chat UI

Create one lightweight local analytical application with:

- headline order, fulfilment, production and inventory views;
- order trend and production comparison visualizations;
- a current low-inventory table;
- a single-turn question box;
- expandable SQL, sources, metric definitions, query time and warnings.

Acceptance: the deterministic dashboard still works if the LLM is unavailable,
and the chat makes uncertainty and refusals visible rather than inventing an
answer.

The Level 1 UI reserves, but does not implement, a future `Analysis task` mode.
Its reusable seams are the explicit run-state indicator, ordered execution
steps, evidence surface and approval-ready boundary. Until Level 1 is closed,
that mode remains disabled: there is no task decomposition, autonomous loop,
background execution, model retry policy or additional tool authority.

### Phase 5 — evaluation and failure hardening

Create a repeatable evaluation set containing supported, ambiguous,
unanswerable and hostile questions. Verify SQL correctness, aggregation grain,
scope enforcement, timeouts, row limits, prompt-injection handling, empty
results, provider failure and database failure.

The initial lightweight contract is stored in
`analytics/evaluation_cases.json`. Routine tests validate its coverage without
calling the paid or rate-limited provider; a live pass is performed explicitly
at the Level 1 acceptance checkpoint.

Acceptance: the agreed evaluation set passes, and known limitations are shown
in the response when relevant.

### Phase 6 — integrated runtime and portfolio closeout

Add the analytics service to the local runtime, document setup and recovery,
capture evidence, and update the project narrative without overstating the
system as an autonomous agent or production deployment.

Acceptance: a fresh local setup can run the ETL and analytical application from
documented commands, and the portfolio evidence includes successful, refused
and safely blocked examples.

## Implementation order and dependencies

Phases are sequential at their safety boundary: Phase 3 cannot execute model SQL
until Phase 2 is accepted, and Phase 4 does not define new business semantics.
UI scaffolding may overlap late Phase 3, but it must consume the same API and
semantic contract. The existing ETL checkpoint is retained throughout; no new
simulation date is required for this work.

The initial Phase 3 provider is Groq's free-plan `openai/gpt-oss-20b` endpoint,
using strict structured outputs and low reasoning effort. It remains behind the
provider-neutral `AnalyticsLlm` contract; provider choice does not leak into SQL
validation or warehouse access.
