What was built

docker-compose.yaml (repo root) orchestrating the services + the SecureBank Postgres datasource the runtime needs:

┌─────────────────────┬───────────────────────┬───────────────────────────────────────────────────────────────┬──────┐
│       Service       │     Build context     │                             Role                              │ Port │
├─────────────────────┼───────────────────────┼───────────────────────────────────────────────────────────────┼──────┤
│ skill-builder       │ ./skill-builder       │ docs → rules (FastAPI)                                        │ 8000 │
├─────────────────────┼───────────────────────┼───────────────────────────────────────────────────────────────┼──────┤
│ semantic-layer-api  │ ./semantic-layer      │ design-time API: rules+schema → templates                     │ 8010 │
├─────────────────────┼───────────────────────┼───────────────────────────────────────────────────────────────┼──────┤
│ semantic-mcp-server │ ./semantic-mcp-server │ templates → live MCP query tools (HTTP/SSE)                   │ 8090 │
├─────────────────────┼───────────────────────┼───────────────────────────────────────────────────────────────┼──────┤
│ ui                  │ ./prefront-ui         │ skill-builder front-end (nginx)                               │ 5173 │
├─────────────────────┼───────────────────────┼───────────────────────────────────────────────────────────────┼──────┤
│ securebank-db       │ postgres:16           │ SecureBank datasource (schema+seed from securebank-demo/db/)  │ 5434 │
└─────────────────────┴───────────────────────┴───────────────────────────────────────────────────────────────┴──────┘

Each got a Dockerfile + .dockerignore; added requirements.txt to skill-builder and semantic-layer. The UI is a multi-stage build (Node builds, nginx serves + proxies /design → skill-builder:8000, so no CORS).

One code change was required: the MCP server only spoke stdio, which can't be a network service. I added an HTTP/SSE transport (serve --http) — serve_http() in server.py + flags in cli.py. stdio still works.

Wiring: curated SecureBank demo artifacts are committed at securebank-demo/policy/ and seeded into the shared artifacts volume (into /artifacts/securebank-demo/) by the securebank-seed service; semantic-mcp-server reads them and connects to securebank-db (depends_on: securebank-db healthy + securebank-seed completed). LLM keys come from .env (gitignored; .env.example provided).

Verified

- All images build (the UI image building = the React app compiles cleanly).
- docker compose up: securebank-db healthy → securebank-seed populated the artifacts volume → semantic-mcp-server came up serving the governed tools → skill-builder /healthz ok → ui returns 200.
- MCP SSE client call against localhost:8090 returned real rows from the SecureBank DB container.

Run it

cp .env.example .env          # add GROQ_API_KEY=...
docker compose up --build
# UI            → http://localhost:5173
# skill-builder → http://localhost:8000
# MCP server    → http://localhost:8090/sse   (HTTP/SSE)
docker compose down           # stop  (down -v to wipe volumes)

The stack is currently up in your environment. A couple of notes:
- The runtime serves the curated SecureBank demo artifacts (committed under securebank-demo/policy/, seeded into the artifacts volume); regenerate templates anytime with the semantic-layer build CLI or the semantic-layer-api on :8010 (e.g. after editing the schema/rules).
- semantic-layer-api and skill-builder need an LLM key in .env; the runtime (semantic-mcp-server, securebank-db, ui) doesn't.

Domain independence (multi-tenant)

The same Prefront runs across any customer or domain with zero code edits. The
engine code (semantic-layer, skill-builder, semantic-mcp-server, prefront-ui) is
pure mechanism — it names no table, column, policy, or tenant. All
tenant-specific content lives in three planes OUTSIDE the code:

  1. Database + schema — a datasource. The demo's SecureBank Postgres runs in
     this compose (host :5434) with its schema/seed in-repo at securebank-demo/db/;
     nothing tenant-specific is baked into these images.
  2. Business policy — policy documents become extracted rules and then published
     artifacts (policy.yaml, query_templates.yaml, intent bindings) on the shared
     artifacts volume. Runtime evaluation is deterministic dict-lookups + a
     safe-AST arithmetic evaluator — no LLM, no name guessing.
  3. Deployment identity/config — environment variables: IDENTITY_QUERY, ACT_AS,
     DATABASE_URL, METRICS, CALLER_ROLE / CALLER_REGION.

The runtime is mechanism end to end: governance/writes.py interprets a
declarative write_action spec (column_map / caller_columns / defaults / autofill)
shipped in the template; governance/identity.py resolves the caller via the
IDENTITY_QUERY env var; governance/rules.py evaluates the pre-bound policy.yaml
(an external engine like OPA could drop in behind the same contract).

Onboard a new tenant — no code changes:
  - point the runtime at their database and mount their schema.sql,
  - run skill-builder over their policy documents to publish their artifacts,
  - set their deployment env (IDENTITY_QUERY, ACT_AS, DATABASE_URL, ...).

Conventions that keep it independent:
  - Engine code never contains table / column / policy / tenant literals. Code
    defaults use a neutral `example` slug.
  - Tenant specifics belong in deployment config (docker-compose.yaml,
    .env.example) — the demo wires SecureBank there, not in the packages.

Tracing (Arize Phoenix)

The stack ships a Phoenix collector + trace UI at http://localhost:6006. Every
Python service exports OTLP/HTTP spans to it, all under one Phoenix project
(default `prefront`) so a single run reads as one trace end to end:

  scenario B4                       (orchestrator: the demo test case)
   ├─ ungoverned agent             ← the "before": typed app functions, no policy
   │   └─ ChatCompletion           (auto-instrumented OpenAI call)
   └─ governed agent               ← the "after"
       ├─ ChatCompletion           (the LLM picks an approved intent)
       ├─ govern view_users        ← the DECISION, from semantic-mcp-server
       └─ ChatCompletion           (synthesises the governed result)

What is instrumented, and how:
  - LLM calls — openinference-instrumentation-openai patches the OpenAI client,
    so skill-builder's per-clause rule extraction, semantic-layer's mapper, and
    both demo agents are traced with no call-site changes.
  - Governance decisions — semantic-mcp-server emits one `govern <intent>` span
    per governed call, carrying outcome, reasons, fired/indeterminate rule keys,
    masked field names, approver roles, and row count. It records the decision,
    never the rows. A block or approval_required is a correct outcome, so the
    span is NOT marked failed; only a failed precheck/query/write is.
  - Trace continuity — openinference-instrumentation-mcp carries context across
    the MCP hop (agent → Prefront), and the orchestrator injects a W3C
    traceparent header on its plain-HTTP call to the ungoverned service, so both
    sides of a before/after scenario sit under one root span.

Config (all optional — the defaults are already wired in docker-compose.yaml):
  PREFRONT_TRACING=0            turn tracing off in every service
  PHOENIX_COLLECTOR_ENDPOINT=   point at your own OTLP/HTTP collector
  PHOENIX_PROJECT_NAME=         group traces under a different project
  OPENINFERENCE_HIDE_INPUTS / _HIDE_OUTPUTS=true
                                keep prompts + completions out of Phoenix (they
                                include governed query results for the demos)

Port note: 6006/4317 are Phoenix's defaults, so a Phoenix you already run locally
owns them and the bundled container cannot publish (it ends up unattached and the
services can't resolve `phoenix`). Either move the bundled one with
PHOENIX_UI_PORT / PHOENIX_GRPC_PORT, or skip it and export to the one you have
with PHOENIX_COLLECTOR_ENDPOINT=http://host.docker.internal:6006.

With no endpoint configured — a bare venv, a CI run, someone else's compose —
tracing imports nothing and every call site is a no-op. The implementation lives
in tracing/prefront_tracing.py, vendored into each service by tracing/sync.sh
(each service has its own Docker build context, so they can't share an import).
Run `sh tracing/sync.sh --check` to detect drift.

OOB observability (Phoenix → ClickHouse → the Observability tab)

Phoenix is the collector; the durable, queryable store is ClickHouse, fed
out-of-band by the `oob-ingest` service (nothing here is on a governed call's
request path — a dead ClickHouse changes no decision). Two sources feed it:

  pull   oob-ingest tails Phoenix's REST API (GET /v1/projects/<p>/spans, cursor
         + start_time watermark, persisted in ClickHouse) every OOB_POLL_SECONDS.
         Works with zero changes to the services and backfills history.
  push   the APP AGENTS (securebank-ungoverned / loanpro-ungoverned) also fan a
         copy of each span straight to oob-ingest:8110/v1/traces
         (PREFRONT_TRACE_FANOUT — set on those services only, never on Prefront's
         own services). This copy carries service.name, which the Phoenix REST
         omits; when both arrive the table (ReplacingMergeTree keyed by
         trace_id+span_id) keeps the push copy.

Out-of-band means out-of-band: nothing INLINE is ingested. On the pull path,
oob-ingest drops any span carrying a `prefront.*` attribute or named
`governed agent` / `govern <intent>` — AND that span's whole subtree, so the
governed agent's own LLM calls go with it. Configurable per deployment with
OOB_EXCLUDE_ATTR_PREFIXES / OOB_EXCLUDE_SPAN_NAMES / OOB_EXCLUDE_SERVICES; the
Ingestion view shows the active rules and how many spans they dropped. The demo
harness's `scenario <id>` root is kept (it gives the app agent's spans a root) but
its governed-side attributes are scrubbed (OOB_STRIP_ATTR_PREFIXES), and a span
whose parentage cannot be proven kept is held, then dropped, rather than admitted.
Prefront's governed decisions have their own surfaces (Decision Traces, Dashboard)
and are deliberately absent here.

The UI's Observability tab (nginx: /oob/ → oob-ingest) is the AEOP view over it:
  Overview     traces, error rate, p50/p95, LLM calls, tokens, est. cost, tool
               calls; throughput + p95 time series; per-service / kind / model /
               tool breakdowns
  Traces       filter by service, kind, status, scenario, free text → waterfall →
               span inspector (I/O, LLM messages, attributes, events)
  LLM          per-model usage/tokens/cost/latency, tool-call rate, recent calls
  Ingestion    ClickHouse / Phoenix poller / OTLP tap health, the inline
               exclusion rules, scenario coverage, "sync now" and "clear"

  clickhouse   http://localhost:8123 (HTTP), :9000 (native); db `prefront`, table `spans`
  oob-ingest   http://localhost:8110  (/oob/status, /oob/overview, /oob/traces, …)

Config (defaults wired in docker-compose.yaml; see .env.example): PREFRONT_TRACE_FANOUT,
OOB_PHOENIX_URL, OOB_PHOENIX_PROJECTS, OOB_POLL_SECONDS, OOB_MODEL_PRICES,
CLICKHOUSE_HTTP_PORT / CLICKHOUSE_TCP_PORT / CLICKHOUSE_PASSWORD.
