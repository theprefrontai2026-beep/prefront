# Prefront

A **governed data-access runtime between AI agents and enterprise databases**.
LLMs are used only at **design time**; the runtime is deterministic — it never
does "request → LLM → fresh SQL". Policy documents are compiled into versioned,
human-approved YAML artifacts, and the runtime evaluates those artifacts as pure
mechanism: authz → facts → rule evaluation → decision (mask / block / approve /
execute) → trace.

Alongside that inline path, an **out-of-band evaluation engine** shadow-evaluates
agent traces after the fact against three check families, so an *ungoverned*
deployment can be assessed without sitting on its request path.

See `CLAUDE.md` for the working architecture reference and `design.md` for
positioning.

## Deployments: the engine and each demo are separate Compose projects

The engine's `docker-compose.yaml` defines **no demo's** database, agent or
orchestrator. Each demo has its own compose file and runs as its own Compose
project, attaching to the engine's network and `artifacts` volume as
`external: true`. A plain `docker compose up` therefore starts **no demo**.

### The engine (`docker-compose.yaml`)

| Service | Port | Role |
|---|---|---|
| `ui` | 5173 | React SPA; nginx proxies `/design/` → 8000, `/design/semantic/` → 8010, `/api/` → 8080, `/oob/` → 8110, `/eval/` → 8120, `/pii/` → 8020 |
| `skill-builder` | 8000 | policy compiler: policy doc → clauses → LLM candidate rules → human review → published skill |
| `semantic-layer-api` | 8010 | design-time API: schema introspect, build/publish templates, bind+publish policy |
| `api-server` | 8080 | UI companion: audit log, decision-trace store, review WebSocket |
| `pii-analyzer` | 8020 | Presidio service that guesses which schema columns are PII (design-time aid) |
| `oob-ingest` | 8110 | OOB ingestion + query API (Phoenix → ClickHouse) |
| `eval-engine` | 8120 | out-of-band evaluation: reconstructs sessions, runs the check families, persists verdicts/findings |
| `clickhouse` | 8123 / 9000 | OOB trace store (db `prefront`, table `spans`) |
| `phoenix` | 6006 / 4317 | Arize Phoenix trace collector + UI |
| `skill-builder-db`, `api-db` | — | Postgres (design-time docs/rules; audit + decision traces) |
| `semantic-mcp-server` | 8090 | **behind the `mcp` profile — NOT started by `docker compose up`.** The governed MCP runtime; the demos run their own |

### The demos

| Compose file | Services |
|---|---|
| `loanpro-demo/docker-compose.yml` | `loanpro-orchestrator` :8098, `loanpro-ungoverned` :8097, `loanpro-app-mcp` :8102, `loanpro-db` :5435, `verdict` :5180 (+ `loanpro-mcp` :8101 behind that file's own `mcp` profile, unused by default) |
| `securebank-demo/docker-compose.yml` | `securebank-orchestrator` :8095, `securebank-ungoverned` :8096, `securebank-mcp` :8100, `securebank-db` :5434 |

**LoanPro is the active demo** — an intentionally ungoverned deployment that is
the *subject* of the out-of-band checks. **SecureBank** is the governed
before/after example, with curated artifacts committed at
`securebank-demo/policy/` and seeded into the shared `artifacts` volume.

## Run it

```bash
cp .env.example .env               # LLM key + the EVAL_* artifact paths

# 1. the engine (must come first — demos attach to its network/volume)
docker compose up --build -d
#    UI            → http://localhost:5173
#    skill-builder → http://localhost:8000
#    Phoenix       → http://localhost:6006

# 2. the active demo
docker compose -f loanpro-demo/docker-compose.yml up --build -d
curl 'localhost:8098/api/run?only=F2-05'        # run one scenario
curl 'localhost:8110/oob/sessions?since=3600'   # what OOB ingested

make test                                        # every offline suite

# teardown (demo first, then the engine)
docker compose -f loanpro-demo/docker-compose.yml down
docker compose down                              # add -v to wipe volumes
```

Optional, not started above:

```bash
docker compose -f securebank-demo/docker-compose.yml up --build -d  # SecureBank
docker compose --profile mcp up -d semantic-mcp-server              # the engine's own MCP
```

`skill-builder` and `semantic-layer-api` need an LLM key in `.env`, as do the
demo agents; the runtime and the OOB/eval services do not.

## Domain independence (multi-tenant)

The same Prefront runs across any customer or domain with zero code edits. No
tenant's tables, columns, roles or thresholds drive any engine BEHAVIOUR — every
decision is made from published artifacts and deployment config, so onboarding a
tenant is a config exercise. All tenant-specific content lives in three planes
OUTSIDE the code:

  1. Database + schema — a datasource. Each demo ships its own Postgres in its
     OWN compose file (SecureBank on host :5434 with schema/seed in-repo at
     securebank-demo/db/; LoanPro on :5435), never in the engine's compose;
     nothing tenant-specific is baked into the engine images.
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
  - Engine code never branches on a table / column / policy / tenant literal.
    Code defaults prefer a neutral `example` slug.
  - Tenant specifics belong in deployment config (docker-compose.yaml,
    .env.example) — the demo wires SecureBank there, not in the packages.

Where that holds, measured — it is a gradient, not a blanket property, and an
earlier version of this section overstated it:
  - eval-engine/evalengine names no demo ANYWHERE, not even in a comment, and
    that is the one package with a test enforcing it
    (tests/test_domain_independence.py, which also bars domain nouns like
    "loan" or "credit_score" from executable code). oob-ingest is clean too.
  - Elsewhere demo names DO appear, as config defaults rather than logic:
    semantic-layer's SEMANTICLAYER_KEEP_DATASOURCES ("securebank-demo"),
    lib/db's `demo` column default ("securebank"), skill-builder's
    domain_packs/securebank.yaml, and the UI's demos.ts registry. All are
    overridable; none changes how a decision is made. Widening the guard to
    those packages needs the exemptions designed first (LLM prompts carry
    domain nouns deliberately, as few-shot examples) — see TODO.md entry 5.

## Tracing (Arize Phoenix)

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

Prefront itself is kept OUT of the traced path by the bundled deployment: every
Prefront-owned service (skill-builder, semantic-layer-api, semantic-mcp-server,
securebank-mcp, loanpro-mcp) runs with PREFRONT_TRACING=0, and the demo
orchestrators run with PREFRONT_TRACE_EXCLUDE_SPANS="governed agent", which drops
the governed branch (its LLM and MCP calls included) while keeping the
`scenario <id>` root that the app agent's spans hang off. This is trace-only:
the governed agent still runs and Prefront still enforces every decision. Set
PREFRONT_ENGINE_TRACING=1 to trace Prefront while debugging it.

Config (all optional — the defaults are already wired in docker-compose.yaml):
  PREFRONT_TRACING=0            turn tracing off in every service
  PREFRONT_ENGINE_TRACING=1     trace Prefront's own services (off by default)
  PREFRONT_TRACE_EXCLUDE_SPANS= span-name prefixes never recorded, subtree included
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

## OOB observability (Phoenix → ClickHouse → the Observability tab)

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

Out-of-band means out-of-band: nothing INLINE is ingested — and with the
deployment defaults above, nothing inline is even produced, so Phoenix holds only
harness + app-agent spans. The ingest-side filter is the second line of defence
(for a Phoenix shared with other deployments, or when engine tracing is on).
On the pull path,
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
  Sessions     per-session view: turns, tool calls, writes, off-catalog calls
  Ingestion    ClickHouse / Phoenix poller / OTLP tap health, the inline
               exclusion rules, scenario coverage, "sync now", and "clear all
               trace data" (purges Phoenix's projects AND every ClickHouse
               table — clearing ClickHouse alone is only a pause, since
               oob-ingest re-pulls from Phoenix on its next poll)

Findings — eval-engine's shadow-evaluation log — lives in the Decision Traces
tab (Decisions | Findings), not here: a finding is a governance-decision-log
concept, not a pipeline-health one. eval-engine (:8120, nginx /eval/) reads the
same `spans` table read-only, reconstructs each session, runs the three check
families over it, and persists verdicts, findings and conformance tags.

  clickhouse   http://localhost:8123 (HTTP), :9000 (native); db `prefront`, table `spans`
  oob-ingest   http://localhost:8110  (/oob/status, /oob/overview, /oob/traces, …)

Config (defaults wired in docker-compose.yaml; see .env.example): PREFRONT_TRACE_FANOUT,
OOB_PHOENIX_URL, OOB_PHOENIX_PROJECTS, OOB_POLL_SECONDS, OOB_MODEL_PRICES,
CLICKHOUSE_HTTP_PORT / CLICKHOUSE_TCP_PORT / CLICKHOUSE_PASSWORD.
