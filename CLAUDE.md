# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What Prefront is

A **governed data-access runtime between AI agents and enterprise databases**. The thesis (see `design.md`, `prefront_semantic_layer_design.md`): **LLMs are used only at design time; the runtime is deterministic.** The runtime never does "request → LLM → fresh SQL." Instead:

```
DESIGN TIME (LLM-assisted, human-approved, versioned YAML artifacts)
  policy docs ──skill-builder──▶ candidate rules ──human approve──▶ published rules
  schema + rules ──semantic-layer──▶ semantic model + query templates + bound policy bundle
                                          │ (written to the shared `artifacts` volume)
RUNTIME (no LLM, pure mechanism)          ▼
  agent ──MCP tool call──▶ semantic-mcp-server: authz ▶ facts ▶ rule eval ▶ decision ▶ (mask|block|approve|execute) ▶ trace
```

Anything an LLM emits is a **candidate** that must pass schema validation + human approval before it becomes a runtime artifact. When reasoning about a governed decision, the published artifacts (`policy.yaml`, `query_templates.yaml`) are the source of truth — not the LLM.

## Domain independence (this repo's defining principle)

The engine names **no table, column, policy, or tenant** — it is pure mechanism (README §"domain independence"). All application vocabulary lives in the published artifacts/config, not in code: `grep -rin securebank` over the Python/JS finds hits only in `docker-compose.yaml` (the bundled example's DSN / artifact paths) and the demo's published `securebank-demo/policy/` artifacts, never in engine code. Keep it that way — do not hardcode a domain's tables, roles, or thresholds into any service.

The bundled `docker-compose.yaml` wires **SecureBank as the example deployment** (see `securebank-demo/`, in-repo): the runtime `semantic-mcp-server` serves `/artifacts/securebank-demo/` against the SecureBank Postgres (in this compose at `:5434`). The demo ships curated artifacts (`securebank-demo/policy/`) seeded into the shared `artifacts` volume; this repo is the engine plus that one worked example.

## Services (`docker-compose.yaml`)

| Service | Dir / package | Port | Role |
|---|---|---|---|
| skill-builder | `skill-builder/skillbuilder` | 8000 | **policy compiler**: policy doc → clauses → LLM candidate rules → human review → published skill (FastAPI) |
| semantic-layer-api | `semantic-layer/semanticlayer` | 8010 | design-time API: schema introspect/parse, build/publish templates, bind+publish policy |
| semantic-mcp-server | `semantic-mcp-server/semanticmcp` | 8090 | **runtime**: loads published templates as governed MCP tools (HTTP/SSE); runs the governance pipeline per call. Bundled to serve the SecureBank example (`/artifacts/securebank-demo/`). **Behind compose profile `mcp` — `docker compose up` does NOT start it**; the demos run their own MCP (`securebank-mcp` :8100, `loanpro-mcp` :8101) |
| api-server | `prefront-ui/` (Node/Express) | 8080 | UI companion: persistent audit log (`/api/audit`), **decision-trace store** (`/api/decisions`, `/api/stats`, `/api/policies`, `/api/intents`) that backs the live Dashboard, + collaborative-review WebSocket (`/api/ws/review`); backed by Drizzle/Postgres |
| ui | `prefront-ui` | 5173 | React front-end; nginx proxies `/design/semantic/` → :8010, `/design/` → :8000, `/api/` → :8080, `/oob/` → :8110, `/pii/` → :8020 |
| phoenix | (image `arizephoenix/phoenix`) | 6006 | Arize Phoenix trace collector + UI — receives OTLP/HTTP spans from every Python service (see "Tracing" below) |
| clickhouse | (image `clickhouse/clickhouse-server`) | 8123/9000 | **OOB trace store** — db `prefront`, table `spans` (ReplacingMergeTree keyed by trace_id+span_id) |
| oob-ingest | `oob-ingest/oobingest` | 8110 | **OOB ingestion + query API** (FastAPI): tails Phoenix's REST into ClickHouse, receives the OTLP fan-out on `/v1/traces`, serves `/oob/*` for the UI's Observability tab (nginx proxies `/oob/` → here) |

**Databases in the stack** (three distinct Postgres instances by default):
- `skill-builder-db` — SQLAlchemy/psycopg3, design-time docs/rules/atoms (`:5432` inside Docker)
- `api-db` — Drizzle, `rule_audit_log` + the decision-trace tables (`decision_trace`, `decision_stat`, `decision_agent`, `decision_policy`, `decision_intent`); schema is applied by `drizzle-kit push-force` on api-server start (no migration files) (`:5432` inside Docker, different named volume)
- **SecureBank** Postgres inside Docker at `:5434` — the in-repo runtime/demo datasource (`securebank-demo/db/`)

The `semantic-layer` LLM mapper is the **only** agentic step; everything it emits is candidate output gated by schema validation + human approval. The runtime loads only published YAML.

A customer can also bypass the mapper entirely by **importing a dbt semantic model** + a Prefront governance overlay (`semantic-layer/semanticlayer/dbt_import.py`, `pipeline.run_import_pipeline`, `POST /design/semantic/import/dbt`, CLI `import-dbt`). This path is **deterministic (no LLM)**: dbt supplies structure (entities/attributes/joins), the overlay supplies governance (intents, rules, sensitivity, caller scoping, metrics). It rejoins the *same* `build_bindings → build_query_templates → build_tools → validate` tail, so a customer model is held to the identical §19/§23 gate — and dbt's implicit joins are kept **only** when backed by a real FK (others are dropped + reported, never auto-approved).

In the UI, both the LLM-generate and dbt-import paths are unified in one **Semantic** tab (`prefront-ui/artifacts/prefront-app/src/components/Semantic.tsx`): the dbt upload is **optional** — provide a dbt model + overlay for the deterministic import, or leave it empty to generate from the Policy Studio rules via the mapper. The publish-policy step is driven from the overlay (dbt mode) or the Policy Studio rules (LLM mode). Tab order reflects the dependency pipeline: **Data Connector → Data Graph → Business Graph → Policy Studio → Semantic → Runtime**.

## UI layout (`prefront-ui/`)

The UI is a **pnpm workspace** (`pnpm-workspace.yaml`) with packages under `artifacts/` (the React SPA and api-server) and `lib/` (shared: `api-spec`, `api-zod`, `api-client-react`, `db`). The React SPA lives at `prefront-ui/artifacts/prefront-app/src/`.

The `db` lib is the Drizzle schema shared between the `api-server` and the Drizzle migrations (`lib/db/src/`). The OpenAPI spec at `lib/api-spec/openapi.yaml` is the contract; `api-client-react` (generated by orval) is the typed React-Query client.

## Active demo: LoanPro (`loanpro-demo/`), SecureBank is profile-disabled

**LoanPro is the demo a plain `docker compose up` brings up** (orchestrator :8098,
ungoverned agent :8097, app-mcp :8102, engine MCP :8101, Postgres :5435) and the
UI's default (`DEFAULT_DEMO` in `demos.ts`, and the api-server's fallback in
`routes/decisions.ts`).
**SecureBank sits behind compose profile `securebank`** and does not start:

```bash
docker compose --profile securebank up -d                    # bring SecureBank back
docker compose --profile securebank --profile mcp up -d      # + the engine MCP, which
                                                             #   is wired to SecureBank's DB
```

**LoanPro is the SUBJECT of Prefront's out-of-band checks** — the three
families in `prefront-check-families.md`. It is an ungoverned deployment whose
tools, data and scenario catalogue were designed so that every check has
something concrete to detect in the trace; nothing in `loanpro-demo/` enforces
or judges anything. `loanpro-demo/docs/check-coverage.md` (generated:
`python docs/gen_coverage.py > docs/check-coverage.md`) is the contract —
check → session → span attributes carrying the evidence. Read it before
touching a tool, a scenario, or a span attribute; the evaluator will be built
against it.

| File | Service | Role |
|---|---|---|
| `app_tools.py` | — | the shop's business functions + their SQL, and **`INTENTS`**: the approved-intent catalog as design-time metadata (callers, channels, fields, mandatory filter, volume, side effect, precondition, ordering, closing obligation, toxic combinations). Not enforced — stamped on spans. `None` = off-catalog (`search_applicants`, `get_internal_metrics`). |
| `app_mcp_server.py` | `loanpro-app-mcp` :8102 | serves those functions as **plain MCP tools**; one `tool <name>` span per call with `session.id`, `user.id`, `app.user.role`, `app.channel`, `app.intent`, `app.side_effect`, `app.catalog`, `app.trust`, `input.value`=args, `output.value`=result **including rows** (≤`LOANPRO_TRACE_ROWS`), `app.row_count`, `app.columns`, `status=ERROR` on failure. The SQL the app ran is deliberately NOT in the result or on the span (a real trace would not have it; no check may rely on it). Identity/role/channel/session arrive as `X-LoanPro-*` connection headers. |
| `ungoverned_server.py` | `loanpro-ungoverned` :8097 | the agent: MCP client with **server-side sessions** — `POST /sessions`, `POST /sessions/{id}/messages` (LLM turn), `POST /sessions/{id}/replay` (scripted turn), `GET /sessions/{id}`; `POST /run` is a one-shot shim. `turn <n>` span per turn; `tracing.using_session()` puts `session.id`/`user.id` on the auto-instrumented LLM spans too. Variants `v1` (deployed prompt, temp 0) / `v2` ("proactive" prompt, temp 0.9). |
| `demo_server.py` + `scenarios.py` | `loanpro-orchestrator` :8098 | runs the catalogue as sessions: `GET /api/scenarios` (grouped by family), `GET /api/run?only=&repeat=&variant=` (`/api/diff` is an alias). Opens the `session <id>` root span so one session = one trace. |

**Two turn modes, one trace shape.** An LLM turn lets gpt-4o-mini pick the
tools (authentic, usually reproduced); a **replay** turn executes a scripted
`steps` list through the *same* MCP session and records a scripted answer
(guaranteed finding — used for fabrication, distortion, ordering, repetition).
Both yield `session → turn → ChatCompletion → tool …`; the replay's stand-in LLM
span carries `app.replay=true`. Catalogue ids: `F1-*`, `F2-*`, `F3-*`, `POP-*`
(carry `repeat`/`variant`), `BASE-*` (clean controls); `F2-04R` is hidden
(runnable by id). Each scenario declares `expected_findings` — what the
evaluator SHOULD report — which the Runtime tab shows beside the transcript.

- **`db/*.sql` only run on a fresh volume.** After any schema/seed change:
  `docker compose rm -sf loanpro-db && docker volume rm prefront_loanpro_pgdata`,
  then `up -d`. Seed facts the checks depend on: KYC `pending` for 5006/5009;
  no bureau file / income verification for 5009 (and no income row for 5003) so
  those tools ERROR; document 9003 carries the injected instruction; 20 loans
  across three officers so an unscoped read is bulk.
- Identity is trusted-layer only: the LLM cannot set `X-LoanPro-*`, but only
  `get_my_applications()` honours the user — every other gap (IDOR, ssn/tax-id/
  bank-account/credit-score/internal-risk-score leakage, unguarded writes, the
  CEL gateway) is intentional and documented in `check-coverage.md`.
- **The trace is the deliverable.** Renaming a span or an `app.*`/`session.id`
  attribute breaks both the OOB Sessions view (`ch.list_sessions`,
  `model.lift`) and the coverage contract — update `docs/gen_coverage.py` and
  the ingest columns together.
- **`docs/credit_policy.md` is the citable policy.** One numbered heading per
  enforceable clause (`#### 4.1.1 Credit floor`, `### 8.1 Verify before quoting`);
  a finding attributes to the smallest numbered section containing the sentence.
  Numbering is append-only. Ids are wired demo-side only: `app_tools._POLICY` →
  `INTENTS[*]["policy"]`, `scenarios.expected_findings[].policy`, the
  `scenario.policy` attribute on the `session` root span, and the Policy column +
  policy index in `check-coverage.md`. `gen_coverage.py` exits non-zero when a
  cited § has no heading — run it after editing the doc, the catalogue or INTENTS.
  Tool spans/results never carry policy ids (the app is policy-blind).
- `loanpro-mcp` (Prefront's governed MCP) is still declared behind the `mcp`
  profile and unused; `policy/*.yaml` are its legacy artifacts and are NOT derived
  from the current `credit_policy.md`.

- **An MCP SSE endpoint MUST return a Response.** Starlette >=1.0 does
  `await (await endpoint(request))(scope, receive, send)`, so an SSE handler that
  returns `None` — the shape every MCP example uses — dies with
  `TypeError: 'NoneType' object is not callable` *after* the stream is served,
  surfacing at the client as an opaque `ExceptionGroup`. Return a bare
  `Response()` after `connect_sse` (it is never sent; the connection is already
  hijacked). This was the same fault as the old "MCP SSE transport flakes" note —
  now fixed in both MCP servers; see that entry.
- **Use `AsyncOpenAI` inside an MCP session.** The agent loop runs in the MCP
  client's task group; `await`-ing the SYNC `OpenAI` client's `create()` raises
  inside the group and surfaces only as `ExceptionGroup: unhandled errors in a
  TaskGroup`, which looks exactly like a transport flake. `_describe()` in
  `ungoverned_server.py` flattens a group to its leaves — reach for it before
  assuming the transport is at fault.

## SecureBank in-repo demo (`securebank-demo/`)

A retail-banking example that ships **inside this repo** and runs from the same compose. It demonstrates the before/after governance contrast using the same engine:

- **`securebank-ungoverned`** (`:8096`) — real LLM + raw SQL (`gpt-4o-mini` with a `run_sql` tool); reads can leak, writes are attempted but rolled back via read-only transaction
- **`securebank-mcp`** (`:8100`) — same `semantic-mcp-server` image pointed at `securebank-demo/policy/`; identity resolved per-connection from `X-Prefront-Act-As`
- **`securebank-orchestrator`** (`:8095`) — fans each scenario out to both, merges results; the UI **Runtime tab** points at whichever demo is selected (LoanPro `:8098` by default now — see `demos.ts`), so reaching this one means selecting SecureBank in the switcher AND starting its profile

The curated artifacts (`securebank-demo/policy/query_templates.yaml`, `policy.yaml`) are committed. The `securebank-seed` one-shot service copies them into the shared `artifacts` volume at startup. `OpenAI API key` required for the ungoverned and orchestrator services.

The test-case catalog lives in `securebank-demo/scenarios.py` (`CALLERS` dict + `get_scenarios()`). `demo_server.py` serves `GET /api/scenarios` (metadata only) and `GET /api/diff?only=B1,B4` (live run both ways). `governed_agent.py` implements the full OpenAI tool-calling loop: LLM picks an MCP tool → Prefront enforces policy → tool result passed back to LLM for natural-language synthesis → `decision["answer"]` set. The Runtime tab (`RuntimeDiff.tsx`) points at `http://localhost:8095` by default (configurable in the UI).

- **Two scenario classes**: **B1–B9** show governance as a **gate** (block/mask/approval/scope). **C1–C2** ("Decision Support: grounded context") show the complement — governance as **enablement**: the outcome is **ALLOW on both sides**, but Prefront's intent returns a *curated context bundle* (C1 `view_account_activity` = an aggregate velocity signal over `transactions`; C2 `view_loan_context` = a `loans`+`users` join + SQL-derived `score_margin`) so the governed agent's answer is grounded/correct where the raw-SQL agent is shallow or ungrounded. The contrast is the two `model` answer lines, not the verdict; `RuntimeDiff.tsx` renders a `pf-grounded-note` caption on any ALLOW-vs-ALLOW row. These need no engine change — just published artifacts + a raw `get_loan` parity tool on the ungoverned side.

- **Concurrency flake — root cause found and fixed.** Each governed run opens its own MCP SSE connection, and firing all scenarios at once (the UI's old "Run all") produced random `ERROR` scenarios. That was the SSE endpoint returning `None` (see the entry below), not concurrency or governance; it is fixed in `semanticmcp/server.py`. The mitigations remain as defence in depth and are still reasonable: `governed_agent.run_agent` **retries** the MCP interaction (a governed decision returns a dict and never raises, so a raised exception is always a transport failure; reads/prechecks are idempotent), and `RuntimeDiff.runAll` **caps concurrency** to a small worker pool.

## Dashboard & decision-trace persistence (`api-server` + `decision_*` tables)

Governed decisions are persisted so the Dashboard and Decision Traces page read history from the DB rather than re-running the LLM on every load. Flow: the Runtime tab (`RuntimeDiff.tsx`) best-effort `POST`s each run to `/api/decisions`, and `POST /api/decisions/refresh` runs the whole SecureBank catalog server-side (via `ORCHESTRATOR_URL`) and stores every result. All routes live in `prefront-ui/artifacts/api-server/src/routes/decisions.ts`.

- **`decision_trace`** — one row per decision (append-only). **Capped at 100**, oldest pruned on every write (`pruneOldTraces`). `GET /api/decisions?limit=N` returns newest-first. `DELETE /api/decisions` (the Dashboard "Clear" control) wipes only this table.
- **Cumulative counters, persistent FOREVER** — never pruned, never reset by Clear: `decision_stat` (per-metric totals → Agent Activity + Decision Outcomes), `decision_agent` (distinct callers → "Agents Active"), `decision_policy` (per-policy trigger counts → the Policies Enforced leaderboard, extracted from the governance trace's fired `rules_evaluated[].rule_key` plus engine-authz reasons), `decision_intent` (per-intent counts + effect buckets → Most Used Intents, with a data-derived risk level). `recordStats`/`recordPolicies`/`recordIntents` fold each insert in via atomic `ON CONFLICT DO UPDATE`.
- `useDecisionFeed` (in `hooks/`) is the shared hook: reads `/api/decisions` + `/api/stats` + `/api/policies` + `/api/intents`, exposes `rows`/`traces`/`stats`/`policies`/`intents` plus `clear`/`populate`. The engine stays domain-neutral — this vocabulary is the SecureBank UI layer, not the runtime.

## Runtime governance pipeline (`semantic-mcp-server/semanticmcp/governance/`)

One MCP tool = one query template. `server.py:call_governed` threads a `GovernanceContext` through stages:

- **identity** (`identity.py`) — resolves the trusted caller from **config, never the agent** (it cannot pass/spoof `caller_*`). Needs `ACT_AS` + `IDENTITY_QUERY` (a SQL `:who` lookup aliasing the deployment's schema onto the contract names `role`/`region`), or the `CALLER_ROLE`/`CALLER_REGION` fallback. No identity ⇒ everything blocks with `no_caller_identity`.
- **facts** (`facts.py`) — value namespace = precheck-row columns ∪ request args ∪ `caller.<attr>` ∪ derived metrics.
- **rules** (`rules.py`) — `evaluate()` runs **every rule whose `intents` includes this intent**, against facts, with a safe-AST arithmetic evaluator.
- **decide** (`decide.py`) — precedence **block > approval_required > allow**. A gating rule that is *indeterminate* (a needed symbol is missing from facts) **fail-safes to approval_required** — drift can gate a call, never silently bypass a control.
- **writes** (`writes.py`) — executes a template's declarative `write_action` only on `allowed`, and **dry-run unless `ENABLE_WRITES=1`**.

Template kinds: `read` (execute SELECT, then mask restricted fields) and `precheck` (run the precheck SELECT → row becomes facts → decision → write on allow). DB access is psycopg3 with `:name` placeholders rewritten to `%(name)s` (`db.py`); reads run read-only.

## Engine mechanics that bite (verified)

- **A rule fires by the template *supplying its fact*, not by listing it.** A template's `required_policies` is documentation only; `evaluate()` keys off the rule's `intents` + whether its condition symbols are present in facts. A precheck that doesn't SELECT the column a rule needs ⇒ that rule goes indeterminate ⇒ fail-safe approval (or never blocks).
- **A symbol must resolve at publish AND match a fact at runtime.** `publish-policy` binds rule symbols against columns / declared request params / metrics / `caller.*` (unresolved ⇒ rejected). At runtime the fact is keyed by the literal column name or the *request-arg name* — so a request param must be named for its column, or it binds but never fires. Over-limit-style conditions need a **simple symbol on the left** (`x > metric`), since the evaluator looks up the left side rather than evaluating an arithmetic expression there.
- **The artifacts volume is read-only in the MCP containers.** `docker exec <mcp-server> cp …` into `/artifacts` fails silently. Edit via a RW helper: `docker run --rm -v <artifacts-vol>:/artifacts -v $PWD/file:/in:ro alpine cp /in /artifacts/<path>` (volume is `prefront_artifacts`).
- **The demo seed jobs only copy when the file is ABSENT** (`[ -f … ] || cp`), so a
  volume created before a demo's artifacts changed keeps serving the OLD ones —
  silently, and `docker compose up --build` will not fix it. Symptom: a scenario
  fails with an outcome like `BLOCK (no approved intent)` because the intent it
  needs was never published. This actually happened: C1/C2 (added in 52c7537)
  blocked for weeks against an Aug-16 volume copy. Diagnose by comparing
  `md5sum securebank-demo/policy/*.yaml` with the same files inside the volume;
  fix with the RW helper above (the MCP hot-reloads on mtime — no restart), or
  `docker compose down -v` to rebuild the volume from scratch.
- **`mcp` must stay `<2`.** mcp 2.0 removed the low-level `Server.list_tools()` /
  `call_tool()` decorators `semanticmcp/server.py` is built on — every MCP container
  dies at startup with `AttributeError: 'Server' object has no attribute 'list_tools'`.
  The requirement was an unpinned `mcp>=1.0`, so ANY cache-invalidating rebuild
  floated it to 2.x. Now pinned `mcp>=1.24,<2` in `semantic-mcp-server/`,
  `semantic-layer/`, the root `requirements.txt`, and both demo Dockerfiles.
- **~~The MCP SSE transport can flake on slower calls~~ — FIXED; it was never the
  transport.** An SSE endpoint that returns `None` (the shape every MCP example
  uses) dies under Starlette >=1.0 with `TypeError: 'NoneType' object is not
  callable`, because the router does
  `await (await endpoint(request))(scope, receive, send)`. It fires at teardown,
  *after* the exchange has completed, so the tool call appears to work and the
  client intermittently sees a dead connection / `ExceptionGroup` /
  `JSONDecodeError` instead. Both MCP servers now `return Response()` after
  `connect_sse` (`semanticmcp/server.py`, `loanpro-demo/app_mcp_server.py`);
  reproduced on the unmodified engine image and verified fixed — server-side
  errors went 1 → 0 over repeated connections. **Any new SSE endpoint must
  return a Response.**
- Because that error surfaced only at the client as an `ExceptionGroup`, two
  unrelated faults used to look identical to it. Before blaming the transport,
  flatten the group to its leaves (`_describe()` in
  `loanpro-demo/ungoverned_server.py`) and read the MCP server's own log.

## Tracing (Arize Phoenix)

Optional OpenTelemetry instrumentation, **on by default in the bundled compose**
(collector + UI at `http://localhost:6006`). All Python services report to ONE
Phoenix project (`prefront`) so a demo scenario reads as one trace:
`scenario B4` → (`ungoverned agent` + `govern <intent>` under `governed agent`),
with the OpenAI calls nested under each agent.

- **Implementation**: `tracing/prefront_tracing.py` is the CANONICAL copy,
  **vendored** into `skillbuilder/`, `semanticlayer/`, `semanticmcp/`,
  `securebank-demo/`, `loanpro-demo/` (each has its own Docker build context, so
  they cannot share an import). Edit the canonical file and run
  `sh tracing/sync.sh`; `sh tracing/sync.sh --check` reports drift.
- **Inert unless configured.** No `PHOENIX_COLLECTOR_ENDPOINT` (or
  `PREFRONT_TRACING=0`) ⇒ nothing is imported and every call site is a no-op, so
  the tracing packages are never required to run a service from a bare venv. It
  is also never fatal: a missing package / unreachable collector degrades to "no
  traces", never to a failed request.
- **LLM spans come free** via `openinference-instrumentation-openai` (patches the
  OpenAI client) — no call-site changes in `skillbuilder/llm.py`,
  `semanticlayer/llm.py`, or the demo agents. `skillbuilder/llm.py`'s
  `extract_clauses` ThreadPoolExecutor **does** carry context explicitly
  (`tracing.current_context()` / `use_context`), or every clause's span would be
  orphaned.
- **`semanticmcp/server.py:call_governed`** wraps `_call_governed` in one
  `govern <intent>` span, annotated from the governance trace: decision, reasons,
  `prefront.rules.fired` / `.indeterminate`, masked field names, approver roles,
  row count. It exports the **decision, never the rows**, and only role/region
  from the caller bag (the full bag can hold PII and stays in `TRACE_PATH`).
  A `blocked`/`approval_required` outcome is *correct*, so the span is not marked
  failed — only `execution_status in (error, write_error)` is.
- **Continuity across hops**: `openinference-instrumentation-mcp` propagates
  context agent → MCP server; `demo_server._ungoverned` injects a W3C
  `traceparent` header and `ungoverned_server.do_POST` re-attaches it, so both
  sides of a before/after scenario share a root.
- **Any MCP client must import `mcp.client.sse` as a MODULE** (applies to
  `securebank-demo/governed_agent.py` and `loanpro-demo/ungoverned_server.py`;
  LoanPro's `governed_agent.py` is deleted). The
  instrumentor patches `mcp.client.sse.sse_client`; a `from … import sse_client`
  binds the original before `setup()` runs, so the governed call silently starts
  a NEW trace instead of continuing the agent's. Symptom: `govern <intent>`
  appears as its own single-span trace. Same trap for any future
  `from <instrumented_module> import <symbol>`.
- **Phoenix's 6006/4317 are commonly already taken** by a Phoenix you run
  locally; the bundled service then fails to attach to `prefront_default` and
  every service gets a DNS failure for `phoenix`. Move it with
  `PHOENIX_UI_PORT` / `PHOENIX_GRPC_PORT`, or drop it and set
  `PHOENIX_COLLECTOR_ENDPOINT=http://host.docker.internal:6006`.
- Caveat: the OpenAI instrumentor records prompts/completions, which for the
  demos include governed rows — `OPENINFERENCE_HIDE_INPUTS/_HIDE_OUTPUTS=true`
  suppress them.
- **Prefront is kept out of the traced path by DEPLOYMENT, not just by filtering.**
  In the bundled compose: every Prefront-owned service (`skill-builder`,
  `semantic-layer-api`, `semantic-mcp-server`, `securebank-mcp`, `loanpro-mcp`)
  sets `PREFRONT_TRACING: ${PREFRONT_ENGINE_TRACING:-0}` — so no `govern <intent>`
  span is ever created — and the demo orchestrators set
  `PREFRONT_TRACE_EXCLUDE_SPANS=governed agent`, which drops the governed branch
  while keeping their `scenario <id>` root. Set `PREFRONT_ENGINE_TRACING=1` to
  trace Prefront itself while debugging it.
- **`PREFRONT_TRACE_EXCLUDE_SPANS` suppresses a whole SUBTREE, not one span.**
  `_Tracer.start_as_current_span` returns a no-op span for a matching name *and*
  attaches OTel's `_SUPPRESS_INSTRUMENTATION_KEY` + OpenInference's
  `suppress_tracing()` for the block — otherwise the auto-instrumented LLM/MCP
  calls underneath would still emit spans and re-parent onto the span above.
  Suppression is trace-only: the governed agent still calls the LLM, Prefront
  still enforces, the decision is unchanged (verified: B1 `allowed` with a
  synthesized answer, B4 `BLOCK (policy)`).
- **YAML merge keys are first-wins.** `x-tracing-env` already defines
  `PREFRONT_TRACING`, so an anchor listed *after* it in `<<: [...]` is silently
  ignored — which is why the per-service override is written as an explicit key
  (explicit always beats merged). Check with
  `docker compose config | grep -A2 PREFRONT_TRACING`.
- The Node `api-server` is **not** instrumented (no LLM calls); the `@opentelemetry`
  entries in `build.mjs` / `pnpm-lock.yaml` are unrelated (an esbuild external and
  a drizzle optional peer dep).

## OOB observability (`oob-ingest/` + ClickHouse + the Observability tab)

Out-of-band = never on a governed call's request path. A dead ClickHouse or
oob-ingest changes no decision; the services keep exporting to Phoenix.

- **Two sources, one table.** `phoenix_source.PhoenixPoller` pages
  `GET {PHOENIX_URL}/v1/projects/{p}/spans?start_time=<watermark − lookback>&cursor=…`
  every `PHOENIX_POLL_SECONDS` for every Phoenix project (or `PHOENIX_PROJECTS`),
  keeping a per-project watermark in `prefront.ingest_state` and an in-memory
  seen-set so the lookback overlap re-reads but never re-inserts. `otlp.py`
  decodes `POST /v1/traces` (protobuf or JSON) from the tracing module's
  `PREFRONT_TRACE_FANOUT` — set in compose on the **app agents only**
  (`securebank-ungoverned` / `loanpro-ungoverned`, via the `x-oob-tap-env`
  anchor), never on Prefront's own services. Both normalize to `model.SpanRow`; `version` = 1
  (phoenix) / 2 (otlp), so the ReplacingMergeTree resolves a span seen twice to
  the OTLP copy. **Reads must use `FINAL`** (`ch.T`) or duplicates leak into counts.
- **Nothing inline is ingested — and since the deployment change, nothing inline is
  even produced.** The compose defaults above mean Phoenix itself now holds only
  harness + app-agent spans; the ingest-side filter below is the second line of
  defence (it still matters for a Phoenix shared with other deployments, or if
  someone flips `PREFRONT_ENGINE_TRACING=1`). `model.is_inline` + `model.drop_inline` drop any
  span with a `prefront.*` attribute or named `governed agent` / `govern …`
  **plus its whole subtree** (the governed agent's LLM calls are inline too), on
  both the pull and push paths. `drop_inline` carries a persistent `dropped` set
  of excluded span ids so a child arriving in a later batch than its excluded
  parent is still excluded. Rules: `OOB_EXCLUDE_ATTR_PREFIXES` /
  `OOB_EXCLUDE_SPAN_NAMES` / `OOB_EXCLUDE_SERVICES`; counts surface in
  `/oob/status` and the Ingestion view. **There is deliberately no governance
  view or decision column here** — governed decisions live in Decision Traces /
  the Dashboard (`/api/decisions`). Do not re-add `prefront.*` to this surface.
- **Two subtleties the exclusion needs, both load-bearing:**
  1. *Attribute scrub* (`model.scrub`, `OOB_STRIP_ATTR_PREFIXES`, default
     `scenario.governed_,scenario.expected`): the demo harness's `scenario <id>`
     root is KEPT (it is what gives the app agent's spans a trace root) but it
     annotates both sides of the run, so its governed-side attributes are
     stripped rather than dropping the whole root.
  2. *Unprovable ancestry* (`PhoenixPoller._hold_orphans`): Phoenix pages are not
     parent-before-child, so a governed-agent LLM call can arrive in an earlier
     batch than the `governed agent` parent that excludes it — it carries no
     `prefront.*` of its own and would slip through as `service: unknown`. A span
     whose parent is neither in the batch, nor already stored, nor known-dropped
     is HELD (up to `ORPHAN_MAX_TRIES` polls) and then dropped — never admitted
     on a guess.
- **A "tool call" in OOB** is `kind='TOOL' OR tool_name != ''` — the inline MCP
  TOOL spans are excluded, so the app agent's own span (carrying `app.tool`) is
  what counts.
- **Phoenix's REST omits `service.name`; the OTLP tap carries it.** In practice a
  span arrives through exactly ONE pipe (verified: 0 spans in the raw table have
  rows from both), so a pulled span cannot rely on its OTLP twin — it has to be
  labelled some other way. Resolution order, best source first:
  1. the OTLP row for that exact `span_id`, if one is already stored
     (`ch.otlp_service_for_spans`) — authoritative;
  2. the **learned alias** `span name -> service`, built from OTLP rows
     (`ch.otlp_service_by_name`): span names are emitted by one tracer in one
     service (`app agent`, `tool <name>`, `ChatCompletion`), so this is stable;
     ties go to the most-seen service, and a name genuinely produced by two
     services is left out rather than guessed wrong;
  3. a labelled ancestor (`inherit_services`, which also looks up stored parents);
  4. `model.infer_service`'s guess, kept as a LAST resort — an orchestrator root
     has no OTLP tap and no parent, and a guess beats `unknown`. (Clearing the
     guess before step 3 without restoring it is exactly how `scenario <id>`
     roots ended up as `unknown`.)
  `ch.relabel_phoenix_from_otlp()` retro-fixes rows already stored under a guess;
  it is a ClickHouse mutation, so it runs at startup and on `POST /oob/sync`,
  never per poll. The scheme **self-heals**: a name with no OTLP counterpart yet
  keeps its guess and flips to the real service on the next sync after one
  arrives (verified live — one span relabelled the moment its sibling landed).
- **NaN is not JSON.** An aggregate over an EMPTY set — a `quantileExact`/`avg`
  where a time bucket holds no root span, or any range with no data — returns
  NaN, and FastAPI's encoder then 500s the whole endpoint. It only shows up on
  SHORT ranges (the UI's 15m button), which is why it survived the first round of
  testing on 24h. Every such aggregate goes through `ch.nan_to_zero()`; note
  ClickHouse has `isNaN(x)` but **no** `ifNaN(x, y)`. `ch.rows()` also coerces any
  non-finite float to 0 as a backstop. Test new endpoints against a range with no
  data, not just the default one.
- **ClickHouse alias trap (bit twice):** `SELECT sum(x) AS x, avg(x)` fails with
  `ILLEGAL_AGGREGATION` — the alias shadows the column for the whole query. Never
  alias an aggregate to a column name another expression in the same SELECT reads
  (`list_traces` aliases to `t_start`/`t_end` and renames in an outer SELECT).
- **Sessions** (`/oob/sessions`, `/oob/sessions/{id}`, `/oob/sessions/population`,
  UI view "Sessions"): a session is every span with the same `session.id`,
  whatever trace it is in. `ch.ensure_schema` runs `ALTER TABLE … ADD COLUMN IF
  NOT EXISTS` for the session columns (`session_id`, `user_id`, `user_role`,
  `channel`, `intent_name`) so an older volume self-heals. `list_sessions`
  counts turns by `name LIKE 'turn %'`, writes by `attributes['app.side_effect']`,
  off-catalog calls by `intent_name = ''` on TOOL spans.
- **Lifted columns** (`model.lift`): `input/output.value`, `llm.model_name`,
  `llm.token_count.*`, `scenario.id`, `tool.name`/`app.tool`. The `decision`,
  `intent`, `caller_role`, `caller_key` columns still exist in the table (the
  schema predates the exclusion) but are never populated now — nothing carrying
  them survives ingestion.
- Cost is a list-price estimate from `config.price_for` (prefix match on model
  name; `OOB_MODEL_PRICES` JSON overrides). Unknown model ⇒ $0.
- UI: `components/Observability.tsx` (tab id `oob`; views Overview / Sessions / Traces /
  LLM / Ingestion), CSS under `.pf-oob-*`. Vite
  dev proxies `/oob` → `VITE_OOB_TARGET` (default `http://localhost:8110`).
- Quick checks: `curl :8110/oob/status` (both sources + counts),
  `curl -X POST :8110/oob/sync` (pull now), `docker exec prefront-clickhouse-1
  clickhouse-client -q "SELECT service, count() FROM prefront.spans FINAL GROUP BY service"`.

## Commands

### Run the bundled stack
```bash
cp .env.example .env          # add an LLM key (e.g. NVIDIA_API_KEY=…; GROQ_API_KEY also supported)
docker compose up --build     # ui:5173  skill-builder:8000  semantic-layer-api:8010
                              # oob-ingest:8110  clickhouse:8123  phoenix:6006
                              # LoanPro (the active demo): orchestrator:8098
                              #   agent:8097  app-mcp:8102  postgres:5435
curl 'localhost:8098/api/run?only=F2-05'          # one LoanPro session (see loanpro-demo/README.md)
curl 'localhost:8110/oob/sessions?since=3600'     # what OOB ingested, per session
docker compose down           # add -v to wipe the artifacts/data volumes

# NOT started by the line above — both are behind profiles:
docker compose --profile securebank up -d                 # the SecureBank demo
docker compose --profile securebank --profile mcp up -d   # + the engine MCP (:8090),
                                                          #   which is wired to SecureBank's DB
```

### Per-package dev (uv-managed venv per package)
```bash
cd <service>
VIRTUAL_ENV=.venv uv venv && VIRTUAL_ENV=.venv uv pip install -r requirements.txt
```
CLIs:
- `python -m skillbuilder build …`
- `python -m semanticlayer build|validate|serve|api`
- `python -m semanticmcp doctor|call <tool> --args '{...}'|serve [--http --port 8090]`
- `python -m oobingest` (serves on `OOB_PORT`, default 8110; needs a reachable `CLICKHOUSE_URL`)

`semanticmcp doctor` checks DB + template loading; `semanticmcp call …` runs one tool with no MCP client.

Python services have no build step, so the fastest edit loop is `docker cp` +
restart (see "Hot-patching running containers"); `oob-ingest` and the demos are
small enough that `docker compose up -d --build <service>` is usually simpler.

### Tests

**`skill-builder/tests/` is the only test suite in the repo.** `semantic-layer`,
`semantic-mcp-server`, `oob-ingest`, and the demos have none — verify changes to
those by running the service (see the per-service verification recipes below and
in the OOB section), not by looking for a pytest run that does not exist.
(`semantic-layer/tests` was deleted in 9cf773a; pytest is not even in its
requirements.)

```bash
# from skill-builder/
VIRTUAL_ENV=.venv .venv/bin/python -m pytest -q
VIRTUAL_ENV=.venv .venv/bin/python -m pytest tests/test_validation.py -q   # one file
VIRTUAL_ENV=.venv .venv/bin/python -m pytest -q -k executability           # one pattern
```

### UI dev
```bash
cd prefront-ui
pnpm install                          # install workspace deps (pnpm only; enforced by preinstall hook)
pnpm run typecheck                    # type-check all packages
pnpm -r --filter ./artifacts/prefront-app run dev   # Vite dev server (needs API proxy or full stack up)

# Regenerate the React-Query client after editing lib/api-spec/openapi.yaml
pnpm --filter ./lib/api-spec run codegen   # runs orval + typecheck:libs
```

**Typechecking gotchas (WSL):** the host `node` may be a Windows shim that fails under WSL (`exec format error`), so `pnpm run typecheck` can't run directly. Run `tsc` in a container instead (host `node_modules` are mounted):
```bash
docker run --rm -v "$PWD":/w -w /w/artifacts/prefront-app node:24-slim \
  node /w/node_modules/typescript/bin/tsc -p tsconfig.json --noEmit
```
`api-server` and `prefront-app` are **composite TS projects that consume `@workspace/db`'s emitted `dist/*.d.ts`** (project references), *not* its src — so after editing a `lib/db` schema, rebuild declarations first or the app typecheck won't see new exports:
```bash
docker run --rm -v "$PWD":/w -w /w node:24-slim node /w/node_modules/typescript/bin/tsc --build lib/db lib/api-zod
```
(The runtime esbuild bundle and `drizzle-kit push` both read `lib/db` *src* directly, so the stale `dist` only affects typechecking.)

**Verifying UI changes in a browser** (no local browser/node): drive the running app with the Playwright image over host networking — install the `playwright` npm package into it (the image ships browsers only) and point at the preinstalled browsers:
```bash
docker run --rm --network host -v /tmp/pf:/work -w /work -e PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
  mcr.microsoft.com/playwright:v1.48.0-jammy bash -lc \
  'PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 npm i playwright@1.48.0 >/dev/null 2>&1; node drive.mjs'
```

### nginx caches upstream IPs (a 502 that is not the backend's fault)

The `ui` container resolves each `proxy_pass` hostname **once at startup**, so
recreating any backend (`docker compose up -d --build skill-builder`, …) leaves
nginx pointing at the old container IP and every proxied call 502s — while the
service itself answers fine on its published port. Symptom: `curl :8000/…` → 200
but `curl :5173/design/…` → 502, and the ui log shows
`connect() failed (111: Connection refused) … upstream: "http://<old-ip>:8000"`.

```bash
docker restart prefront-ui-1     # re-resolves every upstream
```

### Hot-patching running containers
Python services have no build step — copy + restart suffices:
```bash
docker cp <file> prefront-<service>-1:/app/<file> && docker restart prefront-<service>-1
```
The UI is a compiled Vite SPA served by nginx — a restart alone won't pick up `.tsx` changes; a full rebuild is required:
```bash
docker compose build ui && docker compose up -d ui
```

### Publish flow (design-time → runtime)
Artifacts reach the runtime by HTTP, then land in the shared `artifacts` volume the MCP server hot-reloads (on file mtime — no restart):
1. Approve candidates: `POST :8000/design/skills/candidate-rules/{id}/approve`
2. Publish skill: `POST :8000/design/skills/{skill_id}/publish`
3. Build the semantic model + templates from approved rules + schema: `POST :8010/design/semantic/build` then `/publish`
4. Bind + publish the enforceable bundle: `POST :8010/design/semantic/publish-policy` → `policy.yaml`. Rules whose symbols don't resolve are **rejected here**, not shipped.

## UI tab architecture (`prefront-ui/artifacts/prefront-app/src/`)

`App.tsx` owns a single `useState("dashboard")` for the active tab (in the `TABS` array — the source of truth for nav order). All tab bodies are mounted on first visit and toggled via `tab-hidden` CSS (not unmounted), so tab state survives navigation. Current nav order: **Overview → Data Connector → Policy Studio → Business Graph → Data Graph → Runtime → Decision Traces → Intent Flows → Observability**. `completedTabs` in `App.tsx` drives the progress indicators (checkmarks).

`Dashboard.tsx` is **wired to real persisted data** (via `useDecisionFeed`, `/api/*` — see the persistence section below), not fixtures. `DecisionTraces.tsx` (the last tab) is a filterable log over the latest traces; the Dashboard feed's "View all →" navigates to it.

The `skill-builder/CLAUDE.md` is a **sub-CLAUDE.md** with skill-builder-specific architecture notes (pipeline vs API dual-path, domain-pack layering, downstream contract). Read it when working on that service.

## Where to read more
`design.md` (positioning + the LLM-at-design-time-only principle), `prefront_semantic_layer_design.md` (the semantic-contract artifact set), and each service's `README.md`. For a concrete end-to-end domain + a before/after governed-vs-ungoverned harness, see the in-repo `securebank-demo/` example. `prefront-check-families.md` defines the out-of-band evaluation engine's three check families (learnt rules, integrity invariants, intent conformance) that the evaluator being designed against LoanPro will implement; `loanpro-demo/docs/check-coverage.md` is the generated contract mapping each check to a session and the trace evidence that detects it.
