# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Orientation (read this first)

```bash
cp .env.example .env                                        # LLM key + EVAL_* artifact paths
docker compose up --build -d                                # the ENGINE (ui:5173 skill-builder:8000
                                                            #   semantic-layer:8010 oob-ingest:8110
                                                            #   eval-engine:8120 clickhouse phoenix:6006)
docker compose -f loanpro-demo/docker-compose.yml up --build -d   # the ACTIVE DEMO (orchestrator:8098,
                                                            #   agent:8097, app-mcp:8102, verdict:5180)
make test                                                   # every offline suite + vendoring drift check
```

Four facts that catch people out, each expanded below:

1. **The engine and each demo are SEPARATE Compose projects.** A plain
   `docker compose up` starts no demo; demo files attach to the engine's
   network and `artifacts` volume as `external: true`. See "Active demo:
   LoanPro".
2. **`semantic-mcp-server` is behind the `mcp` profile** and is NOT started by
   `docker compose up`. The demos run their own MCP. See "Services".
3. **The engine names no domain.** No table, column, role, tenant or threshold
   from any demo may appear in engine code — enforced by a test. See "Domain
   independence".
4. **LLMs run at design time only; the runtime is deterministic.** Anything an
   LLM emits is a candidate needing schema validation + human approval before
   it becomes a runtime artifact. See "What Prefront is".

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

The engine names **no table, column, policy, or tenant** — it is pure mechanism (README §"domain independence"). All application vocabulary lives in the published artifacts/config, not in code: `grep -rin securebank` over the Python/JS finds hits only in `docker-compose.yaml` (one DSN default — see below) and the demo's OWN `securebank-demo/` directory (its `docker-compose.yml`, code, and published `policy/` artifacts), never in engine code proper. Keep it that way — do not hardcode a domain's tables, roles, or thresholds into any service.

This now extends to the DEPLOYMENT layer too: the engine's `docker-compose.yaml` defines no demo's Postgres, agent, or orchestrator — LoanPro and SecureBank each have their own compose file (`loanpro-demo/docker-compose.yml`, `securebank-demo/docker-compose.yml`), separate Compose projects that attach to the engine's network + `artifacts` volume as `external: true` (see either file's header, or "Active demo: LoanPro" below). The one remaining demo-shaped default left IN the engine file is `semantic-mcp-server`'s `DATABASE_URL`/artifact paths, which still point at SecureBank by default (see that service's own comment for why this one is a deliberate exception) — it is a URL/path *string*, not demo code, and resolves to nothing until you've also brought `securebank-demo/docker-compose.yml` up.

## Services (`docker-compose.yaml`)

| Service | Dir / package | Port | Role |
|---|---|---|---|
| skill-builder | `skill-builder/skillbuilder` | 8000 | **policy compiler**: policy doc → clauses → LLM candidate rules → human review → published skill (FastAPI) |
| semantic-layer-api | `semantic-layer/semanticlayer`| 8010| design-time API: schema introspect/parse, build/publish templates, bind+publish policy. Also owns `intent_catalog.py` (Family 3's schema/generator) and `preflight.py` (LLM-proposed candidate test scenarios — see `eval-engine/CLAUDE.md` § step 19) |
| semantic-mcp-server | `semantic-mcp-server/semanticmcp`| 8090| **runtime**: loads published templates as governed MCP tools (HTTP/SSE); per call runs `governance/rules.py`/`decide.py` over `policy.yaml`, **plus** eval-engine's single-call-safe checks inline (`governance/inline_checks.py` — what runs inline and why `param_provenance` cannot, in `eval-engine/CLAUDE.md` § Phase D). Bundled to serve the SecureBank example from `/artifacts/securebank-demo/`. **Behind compose profile `mcp` — `docker compose up` does NOT start it**; each demo runs its own MCP (`securebank-mcp` :8100, `loanpro-mcp` :8101) |
| api-server | `prefront-ui/` (Node/Express) | 8080 | UI companion: persistent audit log (`/api/audit`), **decision-trace store** (`/api/decisions`, `/api/stats`, `/api/policies`, `/api/intents`) that backs the live Dashboard, + collaborative-review WebSocket (`/api/ws/review`); backed by Drizzle/Postgres |
| ui | `prefront-ui` | 5173 | React front-end; nginx proxies `/design/semantic/` → :8010, `/design/` → :8000, `/api/` → :8080, `/oob/` → :8110, `/pii/` → :8020 |
| verdict | `prefront-ui/artifacts/verdict`| 5180| **Verdict** — standalone "business decision evaluator": runs LoanPro's scenario catalogue interactively and shows each session's transcript beside its expected findings. Its own Vite/React app sharing no code or stylesheet with `prefront-app` (a style change meant for both must be made twice, by hand). Talks to `loanpro-orchestrator` by absolute URL; deployed from `loanpro-demo/docker-compose.yml` |
| phoenix | (image `arizephoenix/phoenix`) | 6006 | Arize Phoenix trace collector + UI — receives OTLP/HTTP spans from every Python service (see "Tracing" below) |
| clickhouse | (image `clickhouse/clickhouse-server`) | 8123/9000 | **OOB trace store** — db `prefront`, table `spans` (ReplacingMergeTree keyed by trace_id+span_id) |
| oob-ingest | `oob-ingest/oobingest` | 8110 | **OOB ingestion + query API** (FastAPI): tails Phoenix's REST into ClickHouse, receives the OTLP fan-out on `/v1/traces`, serves `/oob/*` for the UI's Observability tab (nginx proxies `/oob/` → here) |
| eval-engine | `eval-engine/evalengine`| 8120| **evaluation engine** (FastAPI + background worker): reads the shared `spans` table read-only, reconstructs each session, runs the three check families over it, and persists version-stamped verdicts/findings/conformance tags via `/eval/*`. Family 2 needs no onboarding; Family 1 needs `rule_pack.yaml` (`EVAL_RULE_PACK_PATH`) and Family 3 an `intent_catalog.yaml` (`EVAL_INTENT_CATALOG_PATH`), both degrading to zero verdicts when unconfigured. Full 39-scenario grading run is 39/39 (`loanpro-demo/docs/eval-coverage.md`). See `eval-engine/CLAUDE.md` |

**Databases in the stack** (three distinct Postgres instances by default):
- `skill-builder-db` — SQLAlchemy/psycopg3, design-time docs/rules/atoms (`:5432` inside Docker)
- `api-db` — Drizzle, `rule_audit_log` + the decision-trace tables (`decision_trace`, `decision_stat`, `decision_agent`, `decision_policy`, `decision_intent`); schema is applied by `drizzle-kit push-force` on api-server start (no migration files) (`:5432` inside Docker, different named volume)
- **SecureBank** Postgres inside Docker at `:5434` — the in-repo runtime/demo datasource (`securebank-demo/db/`)

The `semantic-layer` LLM mapper is the **only** agentic step; everything it emits is candidate output gated by schema validation + human approval. The runtime loads only published YAML.

A customer can also bypass the mapper entirely by **importing a dbt semantic model** + a Prefront governance overlay (`semantic-layer/semanticlayer/dbt_import.py`, `pipeline.run_import_pipeline`, `POST /design/semantic/import/dbt`, CLI `import-dbt`). This path is **deterministic (no LLM)**: dbt supplies structure (entities/attributes/joins), the overlay supplies governance (intents, rules, sensitivity, caller scoping, metrics). It rejoins the *same* `build_bindings → build_query_templates → build_tools → validate` tail, so a customer model is held to the identical §19/§23 gate — and dbt's implicit joins are kept **only** when backed by a real FK (others are dropped + reported, never auto-approved).

In the UI, both the LLM-generate and dbt-import paths are unified in one **Semantic** tab (`prefront-ui/artifacts/prefront-app/src/components/Semantic.tsx`): the dbt upload is **optional** — provide a dbt model + overlay for the deterministic import, or leave it empty to generate from the Policy Studio rules via the mapper. The publish-policy step is driven from the overlay (dbt mode) or the Policy Studio rules (LLM mode). Tab order reflects the dependency pipeline: **Data Connector → Data Graph → Business Graph → Policy Studio → Semantic**.

## UI layout (`prefront-ui/`)

The UI is a **pnpm workspace** (`pnpm-workspace.yaml`) with packages under `artifacts/` (the React SPA, api-server, and `verdict` — see below) and `lib/` (shared: `api-spec`, `api-zod`, `api-client-react`, `db`). The React SPA lives at `prefront-ui/artifacts/prefront-app/src/`.

`artifacts/verdict/` is a second, independent Vite/React app (`@workspace/verdict`) with its own `package.json`/`vite.config.ts`/`Dockerfile.verdict`/`verdict-nginx.conf` and **no shared code or stylesheet with `prefront-app`** — a change meant for both apps must be made in both places by hand. `prefront-app` has no Runtime tab: Verdict is the only place to run LoanPro's scenario catalogue interactively.

The `db` lib is the Drizzle schema shared between the `api-server` and the Drizzle migrations (`lib/db/src/`). The OpenAPI spec at `lib/api-spec/openapi.yaml` is the contract; `api-client-react` (generated by orval) is the typed React-Query client.

## Active demo: LoanPro (`loanpro-demo/`)

**LoanPro and SecureBank each have their OWN `docker-compose.yml`, separate
from the engine's `docker-compose.yaml`** (see "Demo deployments are separate
from the engine" below) — a plain `docker compose up` (the engine's own file)
starts NEITHER any more. Bring LoanPro up explicitly:

```bash
docker compose -f loanpro-demo/docker-compose.yml up --build -d
# orchestrator :8098, ungoverned agent :8097, app-mcp :8102, Postgres :5435,
# verdict :5180 (now lives here — see that file's own header). loanpro-mcp
# (the engine MCP, unused) is behind that file's OWN `mcp` profile:
docker compose -f loanpro-demo/docker-compose.yml --profile mcp up -d loanpro-mcp
```

LoanPro is still the UI's default demo (`DEFAULT_DEMO` in `demos.ts`, and the
api-server's fallback in `routes/decisions.ts`) — that's a UI-layer default,
unrelated to which compose file(s) you've actually brought up.

```bash
docker compose -f securebank-demo/docker-compose.yml up --build -d   # bring SecureBank up
```

`securebank-mcp` (that demo's own governed MCP, NOT unused — the
orchestrator depends on it) has no profile gate; the engine's OWN
`semantic-mcp-server` (defaulting to SecureBank's DSN/artifacts as a
convenience — see `docker-compose.yaml`'s header) is a separate, optional
instance behind the engine file's `mcp` profile, only useful if you also
have `securebank-demo/docker-compose.yml` up.

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
evaluator SHOULD report — which Verdict shows beside the transcript (see
"Verdict" in the services table above; the main Prefront UI has no Runtime
tab any more).

- **`db/*.sql` only run on a fresh volume.** After any schema/seed change:
  `docker compose -f loanpro-demo/docker-compose.yml rm -sf loanpro-db && docker volume rm prefront-loanpro-demo_loanpro_pgdata`,
  then `up -d`. Seed facts the checks depend on: KYC `pending` for 5006/5009;
  no bureau file / income verification for 5009 (and no income row for 5003) so
  those tools ERROR; document 9003 carries the injected instruction; 21 loans
  across three officers so an unscoped read is bulk; applicant 5013 is
  superprime (810) with a verified income of $28,000 whose loan 7021 asks
  $160,000 — over the 5x cap of $140,000, so F1-11 can test that a strong
  score does not lift a hard affordability limit (F1-09 covers the same rule
  on a prime borrower).
- Identity is trusted-layer only: the LLM cannot set `X-LoanPro-*`, but only
  `get_my_applications()` honours the user — every other gap (IDOR, ssn/tax-id/
  bank-account/credit-score/internal-risk-score leakage, unguarded writes, the
  CEL gateway) is intentional and documented in `check-coverage.md`.
- **The trace is the deliverable.** Renaming a span or an `app.*`/`session.id`
  attribute breaks both the OOB Sessions view (`ch.list_sessions`,
  `model.lift`) and the coverage contract — update `docs/gen_coverage.py` and
  the ingest columns together.
- **`docs/loan_underwriting_policy.md` is the citable policy** (supersedes the
  retired `docs/credit_policy.md`, tagged `loanpro-pre-credit-policy-rewrite`
  before the cutover). One numbered heading per enforceable clause — the
  document mixes markdown headers (`## 4. Applicant Eligibility Requirements`,
  `### 9.4 Auto-Approval Conditions`) for top-level/some second-level sections
  with a bold full-line paragraph (`**9.2.1 Credit Score Below Minimum
  Threshold**`) for third-level and the rest of the second-level ones —
  `docs/gen_coverage.py`'s heading regex matches both styles. A finding
  attributes to the smallest numbered section containing the sentence.
  Numbering is append-only. Ids are wired demo-side only: `app_tools._POLICY` →
  `INTENTS[*]["policy"]`, `scenarios.expected_findings[].policy`, the
  `scenario.policy` attribute on the `session` root span, and the Policy column +
  policy index in `check-coverage.md`. `gen_coverage.py` exits non-zero when a
  cited § has no heading — run it after editing the doc, the catalogue or INTENTS.
  Tool spans/results never carry policy ids (the app is policy-blind).
- `loanpro-mcp` (Prefront's governed MCP, now declared in
  `loanpro-demo/docker-compose.yml`) is still behind that file's own `mcp`
  profile and unused; `policy.yaml`/`query_templates.yaml` are its legacy
  artifacts and are NOT derived from the current `loan_underwriting_policy.md`.
  `policy/intent_catalog.yaml` is a THIRD, unrelated file in the same
  directory — eval-engine's Family 3 artifact (`EVAL_INTENT_CATALOG_PATH`,
  read from the shared `artifacts` volume now, not a bind mount — see the
  engine `docker-compose.yaml`'s eval-engine comment), hand-transcribed from
  `app_tools.py`'s `INTENTS` + `_POLICY`, read only by eval-engine, never by
  `loanpro-mcp` (which bind-mounts `./policy` directly for its OWN inline
  reuse — a different, unrelated mechanism, see that service's own comment).

- **Use `AsyncOpenAI` inside an MCP session.** The agent loop runs in the MCP
  client's task group; `await`-ing the SYNC `OpenAI` client's `create()` raises
  inside the group and surfaces only as `ExceptionGroup: unhandled errors in a
  TaskGroup`, which looks exactly like a transport flake. See "Engine mechanics
  that bite" for that whole failure class, and for the SSE-endpoint rule
  `app_mcp_server.py` also depends on.

## SecureBank in-repo demo (`securebank-demo/`)

A retail-banking example that ships **inside this repo** and runs from its
**own** `securebank-demo/docker-compose.yml` (separate from the engine's
`docker-compose.yaml` — see "Active demo: LoanPro" above for the split).
It demonstrates the before/after governance contrast using the same engine:

- **`securebank-ungoverned`** (`:8096`) — real LLM + raw SQL (`gpt-4o-mini` with a `run_sql` tool); reads can leak, writes are attempted but rolled back via read-only transaction
- **`securebank-mcp`** (`:8100`) — same `semantic-mcp-server` image pointed at `securebank-demo/policy/`; identity resolved per-connection from `X-Prefront-Act-As`
- **`securebank-orchestrator`** (`:8095`) — fans each scenario out to both, merges results. **No UI tab reaches this any more** — `RuntimeDiff.tsx` (the component that rendered this governed-vs-ungoverned diff, alongside LoanPro's SessionRunner) was removed from `prefront-app` once Verdict took over the LoanPro side; this orchestrator is now driven only via its own HTTP API (`curl`, or `POST /api/decisions/refresh` below) or a future dedicated UI, not the main Prefront app

The curated artifacts (`securebank-demo/policy/query_templates.yaml`, `policy.yaml`) are committed. The `securebank-seed` one-shot service copies them into the shared `artifacts` volume at startup. `OpenAI API key` required for the ungoverned and orchestrator services.

The test-case catalog lives in `securebank-demo/scenarios.py` (`CALLERS` dict + `get_scenarios()`). `demo_server.py` serves `GET /api/scenarios` (metadata only) and `GET /api/diff?only=B1,B4` (live run both ways) — `http://localhost:8095` by default, `curl` it directly.

- **Two scenario classes**: **B1–B9** show governance as a **gate** (block/mask/approval/scope). **C1–C2** ("Decision Support: grounded context") show the complement — governance as **enablement**: the outcome is **ALLOW on both sides**, but Prefront's intent returns a *curated context bundle* (C1 `view_account_activity` = an aggregate velocity signal over `transactions`; C2 `view_loan_context` = a `loans`+`users` join + SQL-derived `score_margin`) so the governed agent's answer is grounded/correct where the raw-SQL agent is shallow or ungrounded. The contrast is the two `model` answer lines, not the verdict. These need no engine change — just published artifacts + a raw `get_loan` parity tool on the ungoverned side.

- **Concurrency flake — root cause found and fixed.** Each governed run opens its own MCP SSE connection, and firing all scenarios at once (the old UI's "Run all") produced random `ERROR` scenarios. That was the SSE endpoint returning `None` (see the entry below), not concurrency or governance; it is fixed in `semanticmcp/server.py`. The mitigation remains as defence in depth and is still reasonable: `governed_agent.run_agent` **retries** the MCP interaction (a governed decision returns a dict and never raises, so a raised exception is always a transport failure; reads/prechecks are idempotent).

## Dashboard & decision-trace persistence (`api-server` + `decision_*` tables)

Governed decisions are persisted so the Dashboard and Decision Traces page read history from the DB rather than re-running the LLM on every load. Flow: `POST /api/decisions/refresh` runs the whole SecureBank catalog server-side (via `ORCHESTRATOR_URL`) and stores every result — this is now the only path in, since the UI component that used to best-effort `POST` each interactive run (`RuntimeDiff.tsx`) has been removed. All routes live in `prefront-ui/artifacts/api-server/src/routes/decisions.ts`.

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

Template kinds: `read` (execute SELECT, then mask restricted fields), `precheck` (run the precheck SELECT → row becomes facts → decision → write on allow), and `mcp` (no SQL at all — see below). DB access is psycopg3 with `:name` placeholders rewritten to `%(name)s` (`db.py`); reads run read-only.

## Generic MCP data-source connector

A second connector alongside Postgres: point the Data Connector tab's **MCP Server** tab at any API-based MCP server's SSE URL and Prefront learns its tools the same way it learns a Postgres schema — `semantic-layer/semanticlayer/mcp_connect.py` connects (`mcp.client.sse`, imported as a **module** — see the tracing gotcha below), lists tools, and represents **each tool as a `PhysicalTable`, each input-schema property as a `PhysicalColumn`** (`build_catalog_from_mcp`). That one representational trick is what lets ~90% of the existing pipeline — `bindings.py`, the LLM entity mapper, `validate.py`'s catalog checks, `mcptools.build_tools` — run **completely unmodified** against an MCP-sourced catalog; no parallel pipeline was built.

- **Design time**: `POST /design/semantic/mcp/introspect` (`{server_url, headers, datasource_id}`) is the MCP analog of `/catalog/introspect`; it persists the source in the `datasources` SQLite table (`source_type='mcp'`, `config_json={server_url, headers}`) so `/build`, `/import/dbt` and `/publish-policy` can re-derive the catalog from just a `datasource_id` afterwards — see `api.py:_resolve_catalog`, which now backs **all three** of those endpoints (fixing, as a side effect, the pre-existing bug where they only worked with a raw `ddl`/`dsn` string and `Semantic.tsx` never actually had one).
- **The catalog projection is lossy; the `mcp_tools` payload is not.** One table
  per tool / one column per INPUT property is what the deterministic pipeline
  consumes, and it necessarily drops everything about what a tool RETURNS and
  how it behaves. So `mcp_connect.tool_record` returns the COMPLETE record
  alongside it — `{name, title, description, input_schema, output_schema,
  parameters, output_fields, annotations, destructive, meta}` — which
  `/mcp/introspect` returns as `payload["mcp_tools"]`, `DataConnector.tsx`
  carries into `schema.mcpTools`, and the **Data Graph** renders for an MCP
  source instead of the table view (`buildFromMcpTools` / `GraphToolNode` /
  `ToolDetailPanel`; masonry layout, no edges — tools aren't joinable).
  Two distinctions the shape preserves on purpose, because both are findings
  about the upstream server rather than presentation details: `output_schema is
  None` (declared nothing) is **not** `{}` (declared an empty one), and every
  annotation hint is **tri-state** — `True`/`False`/`None` for undeclared.
  Nothing is ever inferred from observed responses. LoanPro's own app-mcp
  declares neither, so it renders as 19 tools / 19 "no output schema declared" —
  which is exactly why an intent's `fields:` still has to be hand-transcribed
  (see the `field_scope` findings in `eval-engine/CLAUDE.md`).
- **A missing hint is not a `false` hint.** `destructive` was computed as
  `destructiveHint or not readOnlyHint` with a default of `True` for a missing
  `readOnlyHint`, so a server that set an annotations object at all — even just
  a display `title` — had EVERY tool read as destructive, flipping its default
  governance from `allow` to `approval_required` in `policy_hints_from_mcp`.
  `_is_destructive` now marks a tool destructive only on a POSITIVE declaration;
  silence never does, which is what the module always claimed.
- **No LLM entity-guessing for MCP tools** (`api.py:build_interfaces`): a tool already IS the operation 1:1, so building a candidate model runs the same trivial "1 table = 1 entity" construction `_build_functions` already used, never `mapper.suggest()` — merging unrelated tools into a fictitious entity would be actively wrong, not just wasteful.
- **Default governance from MCP tool annotations, not skill-builder rules**: `policy.py:policy_hints_from_mcp` reads each tool's `destructiveHint`/`readOnlyHint` (when the upstream server sets them — absent is treated as "not destructive", never guessed) and synthesizes one rule per tool (`approval_required` vs `allow`). Used only when the caller supplies no rules of its own — a human can still override with curated Policy Studio rules, same as any source.
- **`querygen._compose_mcp`** builds a `QueryTemplate(kind="mcp", sql="", mcp_server_url=..., mcp_tool_name=..., mcp_destructive=...)` directly from the table's columns — no join/SQL synthesis, since there's nothing to join (MCP tools aren't joinable; no PK/FK is ever invented for one). `sqlcheck.py` skips AST parsing entirely for `kind="mcp"` and instead checks the declared parameters are a subset of the tool's own columns (a construction-consistency check, since `_compose_mcp` built `parameters` from those same columns).
- **Runtime execution**: `semantic-mcp-server/semanticmcp/mcp_proxy.py` (`call_upstream_tool`) proxies a governed call to the real upstream tool. Governance itself needed **zero changes** — `facts.build_facts` already tolerates `row=None` (a plain `read` tool already has no precheck row), so an MCP call is governed from `args ∪ caller.*` alone. A destructive MCP tool reuses the **same `ENABLE_WRITES` gate** as a local SQL write (dry-run by default), with the tool's own parameter names standing in for `write_fields` since there's no local column to name.
- **`server.py`'s `_call_governed`/`call_governed`/`call_template` are now `async def`**, awaiting `mcp_proxy` directly; the two previously-synchronous DB calls (`db.run_select`, `writes.perform`) now run via `anyio.to_thread.run_sync` so they no longer block the event loop either. `call_tool`'s two dispatch branches (`await call_governed(...)` / `await call_template(...)`) and the CLI's `_cmd_call` (`asyncio.run(call_template(...))`) were updated to match — **any new blocking call added to this file must go through `anyio.to_thread.run_sync`, and any new async external call must be `await`ed directly**, never bridged with a nested `asyncio.run()` (the ASGI app already owns the running loop; a nested `asyncio.run()` from inside it raises).
- A binding's root-entity inference (`bindings.py:_required_entities`) now checks for an **exact** `intent == entity_key` match before its noun-guessing fallback — true for every MCP intent (tool name == entity key == table name, by construction) but never true for the fallback's single-noun heuristic, which was silently defaulting every MCP intent's binding to `model.entities[0]`.
- `policybind.bind_rules`'s `root_table_by_intent` (used to disambiguate a same-named field across different tools/tables when binding a rule) falls back to a template's `mcp_tool_name` when there's no SQL `FROM` to parse one out of.

## Engine mechanics that bite (verified)

- **A rule fires by the template *supplying its fact*, not by listing it.** A template's `required_policies` is documentation only; `evaluate()` keys off the rule's `intents` + whether its condition symbols are present in facts. A precheck that doesn't SELECT the column a rule needs ⇒ that rule goes indeterminate ⇒ fail-safe approval (or never blocks).
- **A symbol must resolve at publish AND match a fact at runtime.** `publish-policy` binds rule symbols against columns / declared request params / metrics / `caller.*` (unresolved ⇒ rejected). At runtime the fact is keyed by the literal column name or the *request-arg name* — so a request param must be named for its column, or it binds but never fires. Over-limit-style conditions need a **simple symbol on the left** (`x > metric`), since the evaluator looks up the left side rather than evaluating an arithmetic expression there.
- **The artifacts volume is read-only in the MCP containers.** `docker exec <mcp-server> cp …` into `/artifacts` fails silently. Edit via a RW helper: `docker run --rm -v <artifacts-vol>:/artifacts -v $PWD/file:/in:ro alpine cp /in /artifacts/<path>` (volume is `prefront_artifacts`).
- **The demo seed jobs only copy when the file is ABSENT** (`[ -f … ] || cp`), so a
  volume created before a demo's artifacts changed keeps serving the OLD ones —
  silently, and `docker compose up --build` will not fix it. Symptom: a scenario
  fails with an outcome like `BLOCK (no approved intent)` because the intent it
  needs was never published. Diagnose by comparing
  `md5sum securebank-demo/policy/*.yaml` with the same files inside the volume;
  fix with the RW helper above (the MCP hot-reloads on mtime — no restart), or
  rebuild the volume from scratch — since the demo/engine compose split, `artifacts`
  is declared `external: true` in every demo compose file (the ENGINE project
  owns it), so `docker compose -f loanpro-demo/docker-compose.yml down -v` will
  NOT touch it (external volumes are never removed by `down -v`, by design —
  correct now that it's shared). Run `docker compose down -v` on the ENGINE's
  own `docker-compose.yaml` instead, then bring every compose back up.
- **`mcp` must stay `<2`.** mcp 2.0 removed the low-level `Server.list_tools()` /
  `call_tool()` decorators `semanticmcp/server.py` is built on — every MCP container
  dies at startup with `AttributeError: 'Server' object has no attribute 'list_tools'`.
  The requirement was an unpinned `mcp>=1.0`, so ANY cache-invalidating rebuild
  floated it to 2.x. Now pinned `mcp>=1.24,<2` in `semantic-mcp-server/`,
  `semantic-layer/`, the root `requirements.txt`, and both demo Dockerfiles.
- **Any MCP SSE endpoint MUST return a Response.** Starlette >=1.0 does
  `await (await endpoint(request))(scope, receive, send)`, so a handler
  returning `None` — the shape every MCP example uses — dies with
  `TypeError: 'NoneType' object is not callable`. It fires at teardown, *after*
  the exchange completed, so the tool call appears to work while the client
  intermittently sees a dead connection / `ExceptionGroup` / `JSONDecodeError`.
  Return a bare `Response()` after `connect_sse` (never sent; the connection is
  already hijacked). Done in `semanticmcp/server.py` and
  `loanpro-demo/app_mcp_server.py`.
- **An `ExceptionGroup` at an MCP client is not evidence about the transport.**
  At least three unrelated faults surface identically (the one above, a sync
  `OpenAI` client awaited inside the client's task group, a real server error).
  Flatten the group to its leaves (`_describe()` in
  `loanpro-demo/ungoverned_server.py`) and read the MCP server's own log before
  concluding anything.

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
- **NaN is not JSON.** An aggregate over an EMPTY set — a `quantileExact`/`avg`
  where a time bucket holds no root span, or any range with no data — returns
  NaN, and FastAPI's encoder then 500s the whole endpoint. It only shows up on
  SHORT ranges (the UI's 15m button), which is why it survived the first round of
  testing on 24h. Every such aggregate goes through `ch.nan_to_zero()`; note
  ClickHouse has `isNaN(x)` but **no** `ifNaN(x, y)`. `ch.rows()` also coerces any
  non-finite float to 0 as a backstop. Test new endpoints against a range with no
  data, not just the default one.
- **ClickHouse alias trap:** `SELECT sum(x) AS x, avg(x)` fails with
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
- **The UI over this pipeline lives in `prefront-ui/CLAUDE.md`** — the
  Observability tab's views, Findings (which lives in `DecisionTraces.tsx`,
  not Observability), the family display names, the session flyout and
  `SessionDetail`'s layout. Kept out of this file because it is component
  behaviour, not pipeline mechanics.
- **Clearing trace data spans three services, and all three must fire.**
  `DELETE /oob/spans` (oob-ingest, `ch.truncate()`) clears only
  `spans`/`ingest_state`; eval-engine's `eval_verdicts`/
  `eval_conformance_tags`/`eval_evaluated_sessions` need `DELETE
  /eval/verdicts`; and Phoenix itself needs `DELETE /oob/phoenix`
  (`PhoenixPoller.purge`, deletes every project but `default` and resets the
  poller's watermarks/seen-set/held orphans/aliases under the sync lock).
  Clearing ClickHouse alone is only a pause — oob-ingest re-pulls every span
  from Phoenix on its next poll and eval-engine re-evaluates them, so findings
  come back within a minute. The UI's "Clear all trace data" button fires
  Phoenix first, then the two truncates.
- Quick checks: `curl :8110/oob/status` (both sources + counts),
  `curl -X POST :8110/oob/sync` (pull now), `docker exec prefront-clickhouse-1
  clickhouse-client -q "SELECT service, count() FROM prefront.spans FINAL GROUP BY service"`.

## Commands

### Run the bundled stack

The engine and every demo deployment are now SEPARATE Compose projects (see
"Active demo: LoanPro" above for why) — bring the engine up first, since a
demo compose attaches to its network/volume as `external: true`:

```bash
cp .env.example .env          # add an LLM key (e.g. NVIDIA_API_KEY=…; GROQ_API_KEY also supported)
docker compose up --build     # ui:5173  skill-builder:8000  semantic-layer-api:8010
                              # oob-ingest:8110  clickhouse:8123  phoenix:6006  eval-engine:8120
docker compose -f loanpro-demo/docker-compose.yml up --build -d
                              # LoanPro (the active demo): orchestrator:8098
                              #   agent:8097  app-mcp:8102  postgres:5435  verdict:5180
curl 'localhost:8098/api/run?only=F2-05'          # one LoanPro session (see loanpro-demo/README.md)
curl 'localhost:8110/oob/sessions?since=3600'     # what OOB ingested, per session
docker compose -f loanpro-demo/docker-compose.yml down   # tear the demo down
docker compose down           # then the engine — add -v on either to wipe that project's volumes

# NOT started by the two `up` lines above:
docker compose -f securebank-demo/docker-compose.yml up --build -d   # the SecureBank demo
docker compose --profile mcp up -d semantic-mcp-server               # the engine's OWN MCP
                                                                     #   instance (:8090), defaults
                                                                     #   to SecureBank's DSN/artifacts
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

Four Python test suites exist: `skill-builder/tests/`, `eval-engine/tests/`
(includes a domain-independence guard and, for `family1/temporal.py`'s
precondition automaton, a Hypothesis property-based suite against generated
step streams — `test_family1_temporal_properties.py`), `semantic-mcp-server/
tests/` (governance/inline_checks.py, both pure and wired against a real
`_call_governed`), and two pure (no-network) files in `loanpro-demo/`
(`test_grading_harness.py`, `test_preflight_import.py`). `semantic-layer`
and `oob-ingest` still have none — verify changes to those by running the
service (see the per-service verification recipes below and in the OOB
section). (`semantic-layer/tests` was deleted in 9cf773a; pytest is not even
in its requirements.) `make test` runs all four suites plus
`eval-engine/sync.sh --check`, using each service's already-created venv;
`.github/workflows/tests.yml` runs the same four suites in CI (fresh venvs
via `actions/setup-python`, plus a `compose-config` job) on every push/PR —
deliberately NOT `make grade-loanpro` (below), which needs the live stack +
a metered LLM key that a plain CI runner doesn't have.

```bash
# from skill-builder/ (same pattern for eval-engine/, semantic-mcp-server/)
VIRTUAL_ENV=.venv .venv/bin/python -m pytest -q
VIRTUAL_ENV=.venv .venv/bin/python -m pytest tests/test_validation.py -q   # one file
VIRTUAL_ENV=.venv .venv/bin/python -m pytest -q -k executability           # one pattern

make test          # every offline suite, from the repo root
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
Same pattern for `artifacts/verdict` (swap the `-w` path).

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

## UI front-end

See **`prefront-ui/CLAUDE.md`** (sub-CLAUDE.md) for tab architecture,
`Overview.tsx`'s data sources and truthfulness rules, and the
Observability/Findings/flyout behaviour. Workspace shape is under "UI layout"
above; build/typecheck recipes under "Commands › UI dev".

`skill-builder/CLAUDE.md` is likewise a sub-CLAUDE.md with that service's
architecture notes (pipeline vs API dual-path, domain-pack layering,
downstream contract). Read it when working on that service.

## Where to read more

Top-level design docs, each answering a different question:

| doc | what it covers |
|---|---|
| `design.md` | positioning + the LLM-at-design-time-only principle |
| `prefront_semantic_layer_design.md` | the semantic-contract artifact set |
| `prefront-check-families.md` | the three OOB check families (learnt rules / integrity invariants / intent conformance) — the WHAT |
| `autonomous_build.md` | the phased build order for the eval engine — the HOW |
| `intent_learning_design.md` | **PLANNED, not built** (`autonomous_build.md` §6 Phase E, steps 21-25): mining an intent catalog from observed traces, for customers with no policy document to compile |
| `loanpro-demo/docs/check-coverage.md` | generated contract: check → session → the span attributes that detect it |
| `loanpro-demo/docs/use-cases.md` | the policy-failure use cases in narrative form, each with its real `loan_underwriting_policy.md` citation (and a correction table for numbers from an older version of that document) |
| `loanpro-demo/docs/eval-coverage.md` | generated grading report for the scenario catalogue |

Sub-CLAUDE.mds: `eval-engine/CLAUDE.md`, `skill-builder/CLAUDE.md`,
`prefront-ui/CLAUDE.md`. Each service also has its own `README.md`; for a
concrete end-to-end domain with a governed-vs-ungoverned harness, see
`securebank-demo/`.
