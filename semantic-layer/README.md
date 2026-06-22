# Prefront Semantic Layer Builder

A design-time **agentic program** that turns two reviewed inputs into a governed
*semantic contract* + runnable MCP tool interfaces:

| Input | Source | Role |
|-------|--------|------|
| Governed DB schema (DDL) | `commercerisk-demo/db/schema.sql` | the physical data source Prefront governs |
| Approved policy rules | `skill-builder/skills/<id>/v<ver>/` (skill-builder output) | intents, sensitivity, approval thresholds |

It is the third design-time component alongside `skill-builder/` (policy →
rules). It does **not** use or depend on `template-store/`.

```
   schema.sql ─┐
               ├─► LLM semantic mapper ─► promote ─┐
 policy rules ─┘   (the only agentic step)         │
                                                   ▼
            intent bindings ─► MCP tool contracts ─► validate ─► publish
```

The LLM is used **only at design time** to *suggest* the entity/attribute
mapping. Everything it returns is candidate output that must pass schema +
publish validation; the runtime loads only the published YAML artifacts
(design §2, §23).

## What it produces (the Core 6 artifacts)

Written to the `--out` directory:

- `physical_catalog.yaml` — what physically exists (tables, columns, PK/FK, enums, `[SENSITIVE]` markers)
- `semantic_model.yaml` — business entities/attributes mapped to **real** tables/columns
- `relationships.yaml` — approved join paths, derived from real foreign keys (the agent never invents joins)
- `sensitivity.yaml` — field-level access; sensitive fields **default to deny**
- `intent_bindings.yaml` — each intent → entities, allowed/restricted attributes, mandatory filters, policies
- `mcp_tools.yaml` — one typed MCP tool per approved intent (never raw SQL)

## Install

```bash
# repo venv is uv-managed
VIRTUAL_ENV=.venv uv pip install -r requirements.txt   # openai pydantic pyyaml mcp
```

Set a provider API key (mapper is LLM-only): `GROQ_API_KEY`, `NVIDIA_API_KEY`,
`OPENAI_API_KEY`, … (same presets as skill-builder).

## Use

```bash
# 1. Build the semantic layer
python -m semanticlayer build \
    --schema ../commercerisk-demo/db/schema.sql \
    --rules  skill-builder/skills/cr_fin_001/v3.2 \
    --model-id commercerisk_semantic_model --domain commercerisk \
    --out semantic-layer/out/commercerisk --provider groq

# 2. Re-run the publish-time validator (design §19) over the generated set
python -m semanticlayer validate --in semantic-layer/out/commercerisk

# 3. Serve the generated tool contracts as an MCP server (stdio)
python -m semanticlayer serve --in semantic-layer/out/commercerisk
```

### Ingesting a customer-supplied dbt semantic model

Instead of letting the LLM mapper *guess* a model from the bare schema, a customer
can bring an existing **dbt semantic model** and pair it with a small Prefront
**governance overlay**. This path is fully deterministic — **no LLM** — and is held
to the *same* publish-time gate (design §19/§23) as the generated path.

```bash
python -m semanticlayer import-dbt \
    --dbt     tests/fixtures/commercerisk_dbt_semantic_models.yaml \
    --overlay tests/fixtures/commercerisk_overlay.yaml \
    --schema  ../commercerisk-demo/db/schema.sql \
    --model-id commercerisk_semantic_model --domain commercerisk \
    --out semantic-layer/out/commercerisk
```

The split follows what each format can express:

* **dbt** supplies *structure* — entities (→ `SemanticEntity`), dimensions/measures
  (→ `SemanticAttribute`), and joins. dbt joins are **implicit** (a `foreign` entity
  matches another model's `primary` entity); the importer derives candidates from
  that and keeps **only the ones a real foreign key backs** — design requires
  FK-backed relationships (§7/§19.4), so any dbt join without an FK is **dropped and
  reported**, never auto-approved.
* **the overlay** supplies *governance* dbt has no concept of — the intents to expose,
  the rules that gate them (skill-builder rule shape), per-attribute sensitivity,
  caller scoping, and derived metrics.

From the translated model the flow rejoins the **same** deterministic tail
(`build_bindings → build_query_templates → build_tools → validate`). Over HTTP the
equivalent is `POST /design/semantic/import/dbt`; the generated templates land as
`pending` for the usual review/approve/publish, and the response carries a
`translation_report` (entities mapped, joins approved vs. dropped, sensitivity,
warnings) plus the overlay's `policy` governance (rules/metrics/domain). That
`policy` block feeds the existing `POST /design/semantic/publish-policy`, which
binds the overlay's rules against the datasource vocabulary and writes the
enforceable `policy.yaml` — so a single overlay drives **both** the templates and
the runtime policy bundle.

In the UI both paths live in one **Semantic** tab (after connecting the datasource
in **Data Connector**): **upload a dbt model + overlay** to take this deterministic
import path, or **leave the dbt upload empty** to fall back to the LLM mapper
(which generates the model from the Policy Studio rules instead). Either way the
tab drives the same flow end to end — build → review/**Approve all** templates →
publish templates → publish policy bundle. In dbt mode the policy bundle is bound
from the overlay; in LLM mode it's bound from the Policy Studio rules.

The MCP server exposes each tool with its typed input schema. A call validates
arguments, maps tool → intent → binding, and returns a **decision-trace stub**
(matched intent, semantic model version, policies that would be enforced,
approval behavior, parameters). It does **not** execute SQL — query execution is
a Prefront-runtime concern out of scope for this builder.

## Layout

```
semanticlayer/
  catalog.py     DDL -> physical_catalog (deterministic)
  policy.py      skill-builder rules -> intents/sensitivity/approval hints (deterministic)
  mapper.py      LLM semantic mapper + promotion to published shapes (agentic core)
  dbt_import.py  customer dbt model + overlay -> published shapes (deterministic, no LLM)
  bindings.py    intent bindings (deterministic projection)
  mcptools.py    MCP tool contract generation
  validate.py    publish-time hard-rule checks (design §19)
  pipeline.py    orchestration (run_pipeline | run_import_pipeline)
  artifacts.py   the Core 6 YAML renderers
  mcp_server.py  runtime MCP stdio server
  logutil.py     timestamped debug logging
  cli.py         build | import-dbt | validate | serve
```

## Hard rules enforced (design §23)

No LLM at runtime · runtime uses only published versions · every entity → real
table · every attribute → real column · every join approved & FK-backed ·
sensitive fields default to deny · every MCP tool maps to an approved intent · no
raw-SQL tool · every tool has a typed input schema and a trace requirement.
