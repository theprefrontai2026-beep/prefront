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

The engine names **no table, column, policy, or tenant** — it is pure mechanism (README §"domain independence"). All application vocabulary lives in the published artifacts/config, not in code: `grep -rin commercerisk` over the Python/JS finds hits only in `docker-compose.yaml` (the bundled example's build args / DSN) and generated `semantic-layer/out/…` artifacts, never in engine code. Keep it that way — do not hardcode a domain's tables, roles, or thresholds into any service.

The bundled `docker-compose.yaml` wires **CommerceRisk as the example deployment**: build args `--domain=commercerisk`, output path `/artifacts/commercerisk`, and a Postgres at host `:5433` whose schema/seed come from a **separate repo, `commercerisk-demo`** (`BuildSachin/commercerisk-demo`). That repo holds the example data + the before/after test harness; this repo is just the engine.

## Services (`docker-compose.yaml`)

| Service | Dir / package | Port | Role |
|---|---|---|---|
| skill-builder | `skill-builder/skillbuilder` | 8000 | **policy compiler**: policy doc → clauses → LLM candidate rules → human review → published skill (FastAPI) |
| semantic-layer-api | `semantic-layer/semanticlayer` | 8010 | design-time API: schema introspect/parse, build/publish templates, bind+publish policy |
| semantic-layer (build job) | `semantic-layer/semanticlayer` | — | **one-shot**: schema + approved rules → semantic model, query templates, bound policy bundle → `artifacts` volume. Shows `Exited (0)` — that's normal; `docker compose up semantic-layer` re-runs it |
| semantic-mcp-server | `semantic-mcp-server/semanticmcp` | 8090 | **runtime**: loads published templates as governed MCP tools (HTTP/SSE); runs the governance pipeline per call |
| ui | `prefront-ui` | 5173 | React front-end for skill-builder (nginx proxies `/design` → :8000, avoiding CORS) |

The `semantic-layer` LLM mapper is the **only** agentic step; everything it emits is candidate output gated by schema validation + human approval. The runtime loads only published YAML.

A customer can also bypass the mapper entirely by **importing a dbt semantic model** + a Prefront governance overlay (`semantic-layer/semanticlayer/dbt_import.py`, `pipeline.run_import_pipeline`, `POST /design/semantic/import/dbt`, CLI `import-dbt`). This path is **deterministic (no LLM)**: dbt supplies structure (entities/attributes/joins), the overlay supplies governance (intents, rules, sensitivity, caller scoping, metrics). It rejoins the *same* `build_bindings → build_query_templates → build_tools → validate` tail, so a customer model is held to the identical §19/§23 gate — and dbt's implicit joins are kept **only** when backed by a real FK (others are dropped + reported, never auto-approved).

In the UI, both the LLM-generate and dbt-import paths are unified in one **Semantic** tab (`prefront-ui/src/Semantic.jsx`): the dbt upload is **optional** — provide a dbt model + overlay for the deterministic import, or leave it empty to generate from the Policy Studio rules via the mapper. The publish-policy step is driven from the overlay (dbt mode) or the Policy Studio rules (LLM mode). Tab order reflects the dependency pipeline: **Data Connector → Policy Studio → Semantic**.

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
- **The artifacts volume is read-only in the MCP containers.** `docker exec <mcp-server> cp …` into `/artifacts` fails silently. Edit via a RW helper: `docker run --rm -v <artifacts-vol>:/artifacts -v $PWD/file:/in:ro alpine cp /in /artifacts/<path>`.
- **The MCP SSE transport can flake on slower calls** (a precheck + multi-rule eval), dying with `TypeError: 'NoneType' object is not callable` server-side → an `ExceptionGroup`/`JSONDecodeError` at the client. It's a transport issue, not a governance bug. Verify decisions deterministically by calling the pipeline **in-process** inside a server container: load `PolicyRegistry` + `resolve_caller` + a precheck row and call `govern(...)`.

## Commands

### Run the bundled stack
```bash
cp .env.example .env          # add an LLM key (e.g. NVIDIA_API_KEY=…; GROQ_API_KEY also supported)
docker compose up --build     # ui:5173  skill-builder:8000  semantic-layer-api:8010  mcp:8090
docker compose down           # add -v to wipe the artifacts/data volumes
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

`semanticmcp doctor` checks DB + template loading; `semanticmcp call …` runs one tool with no MCP client.

### Publish flow (design-time → runtime)
Artifacts reach the runtime by HTTP, then land in the shared `artifacts` volume the MCP server hot-reloads (on file mtime — no restart):
1. Approve candidates: `POST :8000/design/skills/candidate-rules/{id}/approve`
2. Publish skill: `POST :8000/design/skills/{skill_id}/publish`
3. Build the semantic model + templates from approved rules + schema: `POST :8010/design/semantic/build` then `/publish`
4. Bind + publish the enforceable bundle: `POST :8010/design/semantic/publish-policy` → `policy.yaml`. Rules whose symbols don't resolve are **rejected here**, not shipped.

## Where to read more
`design.md` (positioning + the LLM-at-design-time-only principle), `prefront_semantic_layer_design.md` (the semantic-contract artifact set), and each service's `README.md`. For a concrete end-to-end domain + a before/after governed-vs-ungoverned harness, see the separate `commercerisk-demo` repo.
