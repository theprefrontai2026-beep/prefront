# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Skill Builder is one service of the Prefront engine. The parent `../CLAUDE.md`
covers the whole platform (domain independence, the design-time-LLM-only
principle, the other services); this file is **skill-builder-specific**. For the
narrative architecture see `docs/blocks.md` (per-block walkthrough) and
`prefront_skill_builder_detailed_design.md` (the formal spec, anchored on the
real code + binder contract).

## What this service is

A **policy compiler**, not a "doc → YAML" converter. A business-policy document
is compiled into reviewed, versioned, machine-enforceable rules:

```
extract → normalize → segment        (deterministic; no LLM)
  → profile → classify → atoms       (understanding; LLM-assisted, heuristic fallback)
  → extract-rules                    (the ONE required LLM step → flat candidate rules)
  → validate (6 passes) + ledger     (deterministic)
  → human review/approve             (FastAPI + React UI)
  → publish                          (deterministic → versioned artifacts)
```

## The contract that governs everything (read before changing rule shape)

1. **The candidate-rule IR is flat and fixed** by `skillbuilder/schema.py`
   (`CandidateRule`: `rule_key`, `rule_type` enum, `conditions[]` of
   `{field, operator, value}`, `effect{decision,…}`, `applies_to_intents`).
   There is **no expression tree, no `reason_code`, no `hard_block`**. Anything
   richer cannot publish.
2. **Every condition symbol must bind to one of four namespaces** — `column`,
   `request_param`, `metric`, or `caller.*` — or the rule is **rejected
   downstream** at `semantic-layer publish-policy` (the binder), never reaching
   runtime. `validation/executability.py` is a **design-time mirror of that
   binder**: keep the two in sync, or rules pass here and get rejected there.

Whatever an LLM emits is a *candidate* (`review_status="pending"`); it only
becomes an `ApprovedRule` after human approval, and only approved rules publish.

## Architecture notes that aren't obvious from one file

- **Two orchestration paths, intentionally separate.** `pipeline.py` wires the
  stages for the **CLI** (`python -m skillbuilder build`). The **FastAPI app
  (`api.py`) re-implements the orchestration itself**, one stage per endpoint
  (and `run-full-extraction` chains them). Logging/grounding changes usually
  need to be made in *both* paths.
- **A rule fires by the template supplying its fact, not by listing it.**
  `validation/engine.py:run_all` evaluates each validator; a rule whose symbol
  is missing from facts goes *indeterminate*, which fail-safes (never silently
  allows). This mirrors the runtime engine in `../semantic-mcp-server`.
- **Domain packs are layered.** `domain_packs/loader.py` loads a curated named
  pack (gated by `SKILLBUILDER_NAMED_PACKS`, default on); `schema_pack.py`
  derives a **column-only** pack from a datasource DDL captured at upload.
  `api.py:_pack_for` overlays named-over-schema. The schema pack supplies the
  `column` namespace the binder actually resolves; the named pack adds
  roles/intents/aliases/metrics the schema can't. A doc's DDL is stored on
  `source_documents.ddl` (see migration `0002`).
- **Coverage / "what didn't convert".** `engine.py` emits an
  `unconverted_clause` UnresolvedItem for **every** clause that produced no rule
  (low severity for prose, medium otherwise) — it deliberately does **not**
  trust the classifier's `clause_type`/`disposition` to hide a clause.
- **Persisted unresolved rows are reshaped.** `store.list_unresolved_items`
  returns rows keyed by `unresolved_type` (top-level) with the full
  `UnresolvedItem` nested under `item` — filter on `unresolved_type`, not `type`.
- **Persistence:** SQLAlchemy over Postgres (prod) or SQLite (dev/tests).
  Documents are immutable — identity is `(file_hash, version)`, so re-uploading
  identical bytes returns the existing row (a fresh run needs a new version).
  Sections/clauses use stable deterministic IDs (UPSERT). **New columns need an
  alembic migration** — `create_all` only covers fresh SQLite, not an existing
  Postgres table.

## Downstream contract (why "it published but the runtime ignores it")

Publish writes `skills/<skill_id>/v<version>/extracted_rules.yaml` (active rules
only) to the registry (`SKILLBUILDER_REGISTRY`, default `/data/skills`,
bind-mounted to `skill-builder-data/` on the host). The **`semantic-layer build`
CLI** can consume those published rules, but there is no longer a bundled
compose build-job wiring a specific skill — point the build at whichever
`skills/<skill_id>/v<version>/extracted_rules.yaml` you published. If the build
exits 1 with `FileNotFoundError: …/extracted_rules.yaml`, the skill inputs
weren't published (or the path/version is wrong).

## Commands

Per-package dev uses **uv**, not pip (see `../prefront/CLAUDE.md`):
```bash
VIRTUAL_ENV=.venv uv venv && VIRTUAL_ENV=.venv uv pip install -r requirements.txt
```

```bash
# Tests (all / one file / one test)
VIRTUAL_ENV=.venv .venv/bin/python -m pytest -q
VIRTUAL_ENV=.venv .venv/bin/python -m pytest tests/test_validation.py -q
VIRTUAL_ENV=.venv .venv/bin/python -m pytest -q -k executability

# CLI: compile a doc to artifacts. --dry-run = segment only (no LLM); -v/-vv = debug logs
python -m skillbuilder build examples/discount_policy.md --doc-id D --version 1 --dry-run
python -m skillbuilder -vv build examples/discount_policy.md --doc-id DISC-001 \
  --version 1 --domain credit_collections --provider openai --out ./skills

# Service (whole stack runs from the PARENT dir, not here)
cd .. && docker compose up --build skill-builder   # FastAPI on :8000; alembic upgrade runs on boot

# End-to-end harness against a running service (upload → extract → validate → approve → publish → bind)
scripts/run_policy.sh <document> [domain] [skill_id] [version] [ddl_file]
scripts/reset.sh            # wipe all docs/rules/artifacts to a clean slate (prompts; -y to skip)
```

## Logging (project convention)

Library code logs via `logging.getLogger(__name__)` routed through
`logconfig.setup_logging()` — **never `print`** in library code. Controlled by
`-v`/`-vv` (CLI) or `SKILLBUILDER_LOG_LEVEL` (env; docker-compose defaults it to
DEBUG). INFO for stage transitions/counts, DEBUG for per-item detail and file
destinations; timestamps are on by default. Add generous debug logging to new
code (the runtime is debugged from container/CLI logs).

## Key env vars

| Var | Effect |
|---|---|
| `SKILLBUILDER_PROVIDER` / `SKILLBUILDER_MODEL` / `SKILLBUILDER_BASE_URL` | LLM provider preset (nvidia/groq/deepseek/grok/openai), model, endpoint |
| `SKILLBUILDER_DB` | SQLAlchemy DSN; a bare path ⇒ SQLite (dev) |
| `SKILLBUILDER_REGISTRY` | Published-artifact root (default `/data/skills`) |
| `SKILLBUILDER_NAMED_PACKS` | `1` (default) overlays curated packs on the DDL pack; `0` = DDL-only |
| `SKILLBUILDER_DOMAIN_PACKS` | dir of uploaded packs (override built-ins) |
| `SKILLBUILDER_LOG_LEVEL` | DEBUG/INFO/… for the `skillbuilder` logger |

## Gotchas

- **`run_policy.sh` step 7 (binder round-trip)** publishes to the bundled
  semantic-layer, which writes ONE deployment's bundle per datasource id at
  `/artifacts/<datasource_id>/policy.yaml`. It's gated by `BIND`
  (`auto`/`1`/`0`) + `BIND_DOMAINS`; `DATASOURCE_ID`/`METRICS` are env-config.
  Running it without gating could overwrite another datasource's bundle.
- **The artifacts volume is shared** with the semantic-layer/MCP services; a
  publish/bind for one domain can clobber another's bundle there.
