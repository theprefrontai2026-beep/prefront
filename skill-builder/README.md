# Prefront — Design-Time Skill Builder

A **policy compiler**. It converts messy business-policy documents into
reviewed, versioned runtime artifacts that the Prefront runtime can enforce
*deterministically* — no live LLM SQL.

```
Policy Document
  → Canonical Markdown
  → Clauses
  → Candidate Rules        (LLM — always "draft")
  → Human Review           (approve / edit / reject)
  → Approved Skill         (versioned, cited, testable)
  → Runtime Policy Engine
  → Decision Trace
```

The LLM only ever produces **candidate** rules. Nothing becomes runtime
configuration until it passes schema validation *and* human approval.

## Pipeline stages

| Stage | Module | LLM? | What it does |
|-------|--------|------|--------------|
| Extract | `extract.py` | no | `.md`/`.txt` native; `.docx`/`.pdf` optional |
| Normalize | `normalize.py` | no | canonical markdown, sections, paragraph IDs, page refs |
| Profile | `profiler.py` | opt | document shape + domain guess (heuristic fallback) |
| Segment | `segment.py` | no | atomic clauses + heuristic `clause_type` |
| Classify | `classifier.py` | opt | a `disposition` for every clause (heuristic fallback) |
| Atoms | `atoms.py` | **yes** | domain-neutral policy atoms (auditability) |
| Extract rules | `llm.py` | **yes** | strict-JSON candidate rules, schema-validated |
| Domain pack | `domain_packs/` | no | vocabulary/alias map mirroring the four binding namespaces |
| Validate | `validation/` | no | schema/grounding/semantic/**executability**/consistency/coverage/testability |
| Unresolved | `unresolved.py` | no | first-class items for anything that won't bind/resolve |
| Conflicts | `conflicts.py` | no | unknown roles/fields, threshold overlaps, contradictions |
| Tests | `tests_gen.py` | no | trigger + negative test case per rule |
| Ledger | `ledger.py` | no | clause → disposition → atoms → rules → unresolved (coverage proof) |
| Artifacts | `artifacts.py` | no | published + per-run output files below |

The **executability** validator mirrors the runtime binder
(`semantic-layer/.../policybind.py`): every condition symbol must resolve to one
of `column | request_param | metric | caller.*`, so a rule that would be rejected
at publish is caught at design time instead.

## Output artifacts

Published skill, under `skills/<skill_id>/v<version>/`:

```
source_policy.md       # clean, citable markdown
policy_skill.yaml      # high-level skill metadata
extracted_rules.yaml   # candidate rules (status: draft) + full provenance
test_cases.yaml        # generated policy tests
review_report.yaml     # confidence, ambiguities, conflicts (review aid)
validation_report.yaml # per-rule validator verdicts (when validated/published)
unresolved_items.yaml  # first-class unresolved items
```

Per extraction run, under `skills/<skill_id>/v<version>/runs/<run_id>/`:

```
document_profile.yaml  clauses.yaml  clause_ledger.yaml
policy_atoms.yaml      unresolved_items.yaml  validation_report.yaml
```

## Setup

```bash
pip install -r requirements.txt

# Pick an LLM provider (all OpenAI-compatible). Set the matching key:
export NVIDIA_API_KEY=...          # provider=nvidia   (default, meta/llama-3.3-70b-instruct)
export GROQ_API_KEY=...            # provider=groq     (llama-3.3-70b-versatile; used by the stack)
export DEEPSEEK_API_KEY=...        # provider=deepseek (deepseek-chat)
export XAI_API_KEY=...             # provider=grok|xai (grok-3; GROK_API_KEY also accepted)
# export OPENAI_API_KEY=...        # provider=openai   (gpt-4o-mini)

# optional overrides:
#   SKILLBUILDER_PROVIDER=groq             # default provider
#   SKILLBUILDER_MODEL=...                 # override the model
#   SKILLBUILDER_BASE_URL=...              # override the endpoint
#   SKILLBUILDER_DB=postgresql+psycopg://… # Postgres DSN; bare path ⇒ SQLite (dev)
```

The provider preset sets base_url + default model + which key env to read, so
base_url and key never mismatch. `deepseek-chat` supports JSON mode and
temperature; `deepseek-reasoner` (R1) does not, and the client omits both
automatically for any model whose name contains `reasoner`.

## CLI

```bash
# Inspect clauses without calling the LLM:
python -m skillbuilder build examples/discount_policy.md \
  --doc-id DISC-001 --version 2026.01 --dry-run

# Full build → writes the five artifacts:
python -m skillbuilder build examples/discount_policy.md \
  --doc-id DISC-001 --version 2026.01 --domain discount_approval \
  --name "Discount Approval Policy" \
  --roles "VP_SALES,CFO,sales_manager,Finance" \
  --fields "discount_percentage,region,caller_role,unit_cost,margin" \
  --intents "create_discount_approval_request" \
  --out ./skills
```

## REST service

```bash
uvicorn skillbuilder.api:app --reload
```

State lives in `SKILLBUILDER_DB` via SQLAlchemy — a Postgres DSN in the bundled
stack (`postgresql+psycopg://…`), or a bare path treated as a SQLite file for
local/dev runs (default `./skillbuilder.db`). On boot the container runs
`alembic upgrade head`.

```
POST   /design/skills/documents/upload                       # multipart file or {text:...}
GET    /design/skills/documents
DELETE /design/skills/documents/{document_id}
POST   /design/skills/documents/{document_id}/extract        # → markdown / sections
POST   /design/skills/documents/{document_id}/segment        # → clauses
POST   /design/skills/documents/{document_id}/profile        # → document profile
POST   /design/skills/documents/{document_id}/classify-clauses
POST   /design/skills/documents/{document_id}/extract-policy-atoms
POST   /design/skills/documents/{document_id}/extract-rules  # → candidate rules (LLM)
POST   /design/skills/documents/{document_id}/run-full-extraction  # all stages + run artifacts
POST   /design/skills/documents/{document_id}/validate       # → validation report (persists unresolved)
GET    /design/skills/documents/{document_id}/validation-report
GET    /design/skills/documents/{document_id}/unresolved-items
GET    /design/skills/documents/{document_id}/clause-ledger
GET    /design/skills/documents/{document_id}/policy-atoms
GET    /design/skills/documents/{document_id}/clauses
GET    /design/skills/documents/{document_id}/profile
GET    /design/skills/candidate-rules?document_id=...
PATCH  /design/skills/candidate-rules/{id}                   # reviewer edit
POST   /design/skills/candidate-rules/{id}/approve           # → approved runtime rule
POST   /design/skills/candidate-rules/{id}/reject
POST   /design/skills/unresolved-items/{id}/resolve          # resolve | waive
POST   /design/skills/{skill_id}/publish                     # → versioned artifacts (blocks on open criticals)
GET    /design/skills/domain-packs
GET    /design/skills/versions
```

Sections and clauses are re-derived deterministically from the stored source
text, so the LLM is invoked *only* at the explicit extraction steps.

## Manual testing with curl

With the stack up (`docker compose up`), the service is on `:8000` and the
binder (`semantic-layer-api`) on `:8010`. No `jq` needed — pretty-print with
`python3 -m json.tool`.

```bash
SB=http://localhost:8000
DOCS=/home/sachi/prefront/commercerisk-demo/business-docs

# 1. upload a policy and capture its id
DID=$(curl -s -F "file=@$DOCS/CR-FIN-001-credit-and-collections-policy.md" \
        -F domain=credit_collections -F version=3.2 \
        $SB/design/skills/documents/upload | sed -E 's/.*"document_id":"([^"]+)".*/\1/')

# 2. run the whole pipeline (profile → classify → atoms → rules → validate)
curl -s -X POST $SB/design/skills/documents/$DID/run-full-extraction \
  -H 'content-type: application/json' \
  -d '{"pack":"credit_collections","skill_id":"cr_fin_001"}' | python3 -m json.tool

# 3. inspect
curl -s "$SB/design/skills/candidate-rules?document_id=$DID" | python3 -m json.tool
curl -s  $SB/design/skills/documents/$DID/validation-report  | python3 -m json.tool
curl -s  $SB/design/skills/documents/$DID/unresolved-items   | python3 -m json.tool
curl -s  $SB/design/skills/documents/$DID/clause-ledger      | python3 -m json.tool

# 4. approve all candidate rules, then publish
for CRID in $(curl -s "$SB/design/skills/candidate-rules?document_id=$DID" \
              | grep -oE '"candidate_rule_id":"[^"]+"' | cut -d'"' -f4); do
  curl -s -X POST $SB/design/skills/candidate-rules/$CRID/approve \
    -H 'content-type: application/json' -d '{"approved_by":"me","version":"3.2"}' >/dev/null
done
curl -s -X POST $SB/design/skills/cr_fin_001/publish -H 'content-type: application/json' \
  -d "{\"document_id\":\"$DID\",\"domain\":\"credit_collections\",\"approved_only\":true}" \
  | python3 -m json.tool
```

### One-shot script

`scripts/run_policy.sh` runs that whole flow (health → upload →
run-full-extraction → validation/unresolved/ledger → approve → publish → binder
round-trip) for one document:

```bash
# scripts/run_policy.sh <document> [domain] [skill_id] [version]
scripts/run_policy.sh \
  ../../commercerisk-demo/business-docs/CR-FIN-001-credit-and-collections-policy.md \
  credit_collections cr_fin_001 3.2
```

Env overrides: `SB` / `SL` (service URLs), `SCHEMA` (datasource `.sql` for the
binder step; defaults to the bundled CommerceRisk schema), `APPROVE=0` (stop
after inspection — no approve/publish/bind).

## Hard rules (enforced)

1. LLM output is always candidate output (`review_status: pending`).
2. Runtime artifacts require human approval.
3. Every rule cites its source document, section, and clause.
4. Every approved rule is versioned and timestamped.
5. Approval / block rules are testable (`test_cases.yaml`).
6. Source clauses are preserved, not just summaries.

## Not in MVP

OCR for scanned PDFs, multi-document conflict resolution, full enterprise
approval workflows, policy simulation, automatic runtime deployment.
