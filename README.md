What was built

docker-compose.yaml (repo root) orchestrating 5 containers — the 4 services you asked for + the Postgres datasource the runtime needs:

┌─────────────────────┬───────────────────────┬───────────────────────────────────────────────────────────────┬──────┐
│       Service       │     Build context     │                             Role                              │ Port │
├─────────────────────┼───────────────────────┼───────────────────────────────────────────────────────────────┼──────┤
│ skill-builder       │ ./skill-builder       │ docs → rules (FastAPI)                                        │ 8000 │
├─────────────────────┼───────────────────────┼───────────────────────────────────────────────────────────────┼──────┤
│ semantic-layer      │ ./semantic-layer      │ rules+schema → templates (one-shot build job → shared volume) │ —    │
├─────────────────────┼───────────────────────┼───────────────────────────────────────────────────────────────┼──────┤
│ semantic-mcp-server │ ./semantic-mcp-server │ templates → live MCP query tools (HTTP/SSE)                   │ 8090 │
├─────────────────────┼───────────────────────┼───────────────────────────────────────────────────────────────┼──────┤
│ ui                  │ ./prefront-ui         │ skill-builder front-end (nginx)                               │ 5173 │
├─────────────────────┼───────────────────────┼───────────────────────────────────────────────────────────────┼──────┤
│ db                  │ postgres:16           │ CommerceRisk datasource (schema+seed auto-loaded)             │ 5432 │
└─────────────────────┴───────────────────────┴───────────────────────────────────────────────────────────────┴──────┘

Each got a Dockerfile + .dockerignore; added requirements.txt to skill-builder and semantic-layer. The UI is a multi-stage build (Node builds, nginx serves + proxies /design → skill-builder:8000, so no CORS).

One code change was required: the MCP server only spoke stdio, which can't be a network service. I added an HTTP/SSE transport (serve --http) — serve_http() in server.py + flags in cli.py. stdio still works.

Wiring: semantic-layer writes artifacts to a shared artifacts volume; semantic-mcp-server reads them (depends_on: service_completed_successfully) and connects to db (depends_on: service_healthy). LLM keys come from .env (gitignored; .env.example provided).

Verified

- All 4 images build (the UI image building = the React app compiles cleanly).
- docker compose up: db healthy → semantic-layer job generated all 7 artifacts → semantic-mcp-server came up serving 3 tools → skill-builder /healthz ok → ui returns 200.
- MCP SSE client call against localhost:8090 returned real rows from the DB container.

Run it

cp .env.example .env          # add GROQ_API_KEY=...
docker compose up --build
# UI            → http://localhost:5173
# skill-builder → http://localhost:8000
# MCP server    → http://localhost:8090/sse   (HTTP/SSE)
docker compose down           # stop  (down -v to wipe volumes)

The stack is currently up in your environment. A couple of notes:
- semantic-layer intentionally shows as Exited (0) — it's a build job, not a server. Re-run it anytime with docker compose up semantic-layer to regenerate templates (e.g. after editing the schema/rules).
- semantic-layer and skill-builder need an LLM key in .env; the runtime (semantic-mcp-server, db, ui) doesn't.

Domain independence (multi-tenant)

The same Prefront runs across any customer or domain with zero code edits. The
engine code (semantic-layer, skill-builder, semantic-mcp-server, prefront-ui) is
pure mechanism — it names no table, column, policy, or tenant. All
tenant-specific content lives in three planes OUTSIDE the code:

  1. Database + schema — an external datasource. The demo's Postgres lives on the
     host at :5433 and its schema.sql is mounted from the sibling
     commercerisk-demo/ repo; nothing tenant-specific is baked into these images.
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
    .env.example) — the demo wires CommerceRisk there, not in the packages.
