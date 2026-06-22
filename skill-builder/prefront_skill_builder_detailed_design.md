# Prefront Design-Time Skill Builder — Detailed Engineering Design

## 0. Reconciliation note (read first)

This document supersedes the earlier greenfield draft. It is **anchored on the
code that already exists** in this repo (`skill-builder/skillbuilder/`, the React
Policy Studio UI, and the real published artifacts under
`skill-builder/skills/cr_fin_001/v3.2/`) and on the **real downstream contract**
the rest of Prefront enforces.

Two things make or break this design, so they are stated up front:

1. **The candidate-rule IR is fixed by `skillbuilder/schema.py` and the binder
   in `semantic-layer/semanticlayer/policybind.py`.** Anything the Skill Builder
   emits that the binder cannot resolve is *rejected at publish time* and never
   reaches the runtime. The IR is **flat conditions + effect + intents** — there
   is no expression tree, no `reason_code`, no `hard_block` rule type, no
   `applies_to.actions`. See §9 and §9b.
2. **Every condition symbol must bind to one of four namespaces** — `column`,
   `request_param`, `metric`, or `caller.*` — or the rule does not publish. This
   is the spine the new layers (domain packs, unresolved items, validation) hang
   off. See §9c.

The ambitious layers from the original draft are **kept** — policy atoms, domain
packs, an expanded validation engine, first-class unresolved items, and a move to
Postgres/SQLAlchemy — but each is redefined so it produces only IR the binder
accepts.

---

## 1. Goal

Build a **Design-Time Skill Builder** that converts arbitrary business policy
documents into reviewed, versioned, machine-enforceable Prefront policy
artifacts.

This is **not** a "document → YAML" converter. It is a **policy compiler**:

```text
Business policy document
  -> canonical markdown
  -> document profile
  -> clauses (segmented + classified)
  -> policy atoms            (domain-neutral intermediate IR)
  -> domain-mapped candidate rules   (the real flat IR — §9)
  -> validation results + unresolved items
  -> human review / approval
  -> published skill artifacts        (consumed by semantic-layer)
```

The system assumes: policies arrive in many formats; their language is often
ambiguous; generated rules can be wrong; missing/indeterminate rules are
acceptable *only if explicitly surfaced*; and runtime rules must be
deterministic, typed, traceable, testable, and review-approved.

Product promise:

> Prefront can ingest messy business policies, extract candidate rules, prove
> what it understood, reveal what it could not determine, and publish only
> validated, reviewed policy artifacts — in the exact shape the runtime enforces.

---

## 2. Non-Goals

- No direct runtime use of raw LLM output.
- No automatic publication of LLM-generated rules.
- No scanned-PDF OCR in MVP.
- No fine-tuning; no multi-document legal-reasoning engine; no BPM/workflow
  approval engine.
- No automatic query-template generation from policies — that is the
  **semantic-layer's** job (see §0 downstream contract). The Skill Builder stops
  at approved rules.
- No new rule constructs the binder cannot resolve (expression trees, free-form
  formula strings, `reason_code` enums, etc.).

The MVP delivers high-quality extraction, validation, review, and artifact
generation that round-trips cleanly into `semantic-layer publish-policy`.

---

## 3. Core Concepts

### 3.1 Source Document
The original uploaded file, stored immutably with hash, metadata, owner, version.
Re-uploading identical content (same `file_hash` + version) returns the existing
row — documents are never overwritten. MVP input types: `.md`, `.txt`, `.docx`,
text-based `.pdf` (`.html` optional). (`skillbuilder/extract.py`.)

### 3.2 Canonical Markdown
A normalized Markdown representation produced **deterministically** (no LLM) by
`skillbuilder/normalize.py`: preserves headings, numbering, tables, bullets,
section references; adds paragraph IDs (`p001`…) and page markers
(`<<<PAGE N>>>` → `[page:N]`). Source content is never summarized.

### 3.3 Clause
A small, traceable unit of policy text carved from a section
(`skillbuilder/segment.py`). A clause may be enforceable, definitional,
contextual, ambiguous, a reference to another policy, an approval-matrix row, or
an audit requirement. **Every clause receives a disposition — no silent drops.**

### 3.4 Policy Atom (intermediate IR — kept, but constrained)
A domain-neutral semantic unit extracted from a clause (prohibition, permission,
obligation, approval_requirement, threshold, exception, waiver,
segregation_of_duties, data_access_permission/restriction, audit_requirement,
retention_requirement, routing_requirement, definition,
related_policy_reference).

Atoms are an *explanatory* intermediate representation that makes extraction
auditable. **Hard constraint:** an atom must be *lowerable* to the flat candidate
rule IR in §9 — it may not carry any construct that IR forbids (no expression
trees, no `reason_code`). §6.5 shows the end-to-end lowering.

### 3.5 Domain Pack (design-time pre-check, NOT the binding authority)
A per-domain vocabulary + alias map (roles, fields, actions→intents, enums,
reason-code→message templates). Its purpose is to **catch unmapped symbols early
as unresolved items**, by mirroring the four binding namespaces (§9c). The
*authority* for binding remains the physical catalog + approved templates +
app-supplied metrics at `semantic-layer publish-policy` — the domain pack is a
design-time convenience, not a second source of truth.

Per the repo's domain-independence rule (`prefront/CLAUDE.md`): domain packs are
**config/artifacts, never engine code**. The Skill Builder engine names no
table, role, or threshold.

### 3.6 Candidate Rule
A proposed runtime rule (the flat IR in §9), `review_status: pending`. Untrusted
until validated and human-approved.

### 3.7 Approved Rule
A reviewed, versioned candidate (`ApprovedRule` in `schema.py`) eligible to be
published into a skill version.

### 3.8 Unresolved Item
A first-class artifact representing ambiguity, missing info, an unmappable
symbol, an unknown role/field/action, a missing related policy, or
non-executable text. Unresolved items are required for trust, not failures.

---

## 4. High-Level Architecture

```text
Policy Document Upload
        │
        ▼
Source Document Store        (immutable file + metadata + hash)
        │
        ▼
Document Extractor           extract.py   (md/txt/docx/pdf)
        │
        ▼
Markdown Normalizer          normalize.py (headings/tables/page refs/para IDs)
        │
        ▼
Document Profiler            profiler.py  (policy shape + domain guess)   [new]
        │
        ▼
Clause Segmenter             segment.py   (sections/rows/bullets)
        │
        ▼
Clause Classifier            classifier.py (rule/definition/audit/…)      [new]
        │
        ▼
Policy Atom Extractor        atoms.py     (LLM structured output)         [new]
        │
        ▼
Domain Mapper                domain_packs/ (vocabulary pre-check)         [new]
        │
        ▼
Candidate Rule Generator     llm.py / RuleExtractor → flat IR (§9)
        │
        ▼
Validation Engine            validation/  (schema/grounding/semantic/
        │                                  executability/consistency/
        │                                  coverage/testability)          [expanded]
        ▼
Human Review (UI)            approve / edit / reject / resolve
        │
        ▼
Skill Publisher              artifacts.py (approved YAML + tests + ledger)
        │
        ▼  (published skills consumed by ↓)
semantic-layer publish-policy  → binds symbols → policy.yaml → runtime
```

`pipeline.py` orchestrates extract → normalize → segment → (profile/classify) →
atoms → candidate rules → conflicts/validation → tests → artifacts.

---

## 5. Implementation Stack (as built + chosen target)

### Backend (existing + target)
```text
Python 3.11+ , FastAPI , Pydantic v2          (in use today)
SQLAlchemy 2.x + Alembic + PostgreSQL         (TARGET — migrate from sqlite3; see §14)
ThreadPoolExecutor for concurrent LLM calls   (in use today, llm.py)
PyYAML / ruamel.yaml , jsonschema             (artifacts + validation)
python-docx / mammoth (DOCX) , PyMuPDF / pdfplumber (PDF) , BeautifulSoup (HTML)
```

### Frontend (existing)
The existing single-page React app in `prefront-ui/` (Vite). New review surfaces
are added to it — not a new frontend. See §17.

### LLM integration (existing abstraction — `skillbuilder/llm.py`)
`RuleExtractor` over an OpenAI-compatible `PROVIDERS` table:

```text
nvidia   (default)  meta/llama-3.3-70b-instruct
groq                 llama-3.3-70b-versatile      (used in docker-compose)
deepseek / grok / openai
```
Env: `SKILLBUILDER_PROVIDER`, `SKILLBUILDER_MODEL`, `SKILLBUILDER_BASE_URL`,
`<PROVIDER>_API_KEY`. JSON mode on; lenient JSON parse (tolerates fences);
retry-on-invalid then fall through to an unresolved item. Do not couple the app
to one provider.

---

## 6. Repository Structure (anchored on the real package)

```text
skill-builder/
  skillbuilder/
    __init__.py
    cli.py                 # python -m skillbuilder build …
    api.py                 # FastAPI: /design/skills/*
    pipeline.py            # orchestrator
    extract.py             # file -> raw text
    normalize.py           # raw text -> canonical markdown + sections/paras
    segment.py             # sections -> clauses (+ heuristic clause_type)
    classifier.py          # [new] explicit clause classification (LLM-assisted)
    profiler.py            # [new] document profile (shape + domain guess)
    atoms.py               # [new] policy-atom extraction (intermediate IR)
    llm.py                 # RuleExtractor + provider abstraction
    domain_packs/          # [new] per-domain vocabulary YAML + loader.py
    validation/            # [new] validators (see §12); replaces ad-hoc conflicts
      __init__.py
      schema.py            #   structural
      grounding.py         #   source grounding
      semantic.py          #   field/role/action vs domain pack + namespaces
      executability.py     #   every symbol binds (the four namespaces)
      consistency.py       #   (today's conflicts.py logic)
      coverage.py          #   clause-ledger completeness
      testability.py       #   tests_gen coverage
    conflicts.py           # retained; called by validation/consistency.py
    unresolved.py          # [new] unresolved-item model + builders
    tests_gen.py           # deterministic test-case generation
    artifacts.py           # render published + per-run artifacts
    schema.py              # Pydantic IR (Clause/CandidateRule/ApprovedRule/…)
    store.py               # persistence (sqlite3 today -> SQLAlchemy, §14)
  domain_packs/            # (alternatively ship packs alongside the package)
  examples/                # discount_policy.md, sample inputs
  skills/<skill_id>/v<version>/   # published registry (SKILLBUILDER_REGISTRY)
    source_policy.md  policy_skill.yaml  extracted_rules.yaml
    test_cases.yaml   review_report.yaml
    runs/<run_id>/     # [new] per-run intermediates (profile/clauses/atoms/…)
  README.md  design.md  prefront_skill_builder_detailed_design.md
```

Frontend lives in `prefront-ui/src/` (separate package; see §17).

### 6.5 Worked example: clause → atom → candidate rule → bound rule
Using the real published rule `hold_order_block` (`skills/cr_fin_001/v3.2`):

```text
CLAUSE (segment.py, clause_0001, §4.1):
  "No new order, quotation conversion, or shipment may be accepted for a
   customer on hold … until Credit & Collections removes the hold."

ATOM (atoms.py):
  atom_type: prohibition
  object:    customer_account
  condition: customer.credit_status == hold
  effect:    decision=block, override_allowed=false

CANDIDATE RULE (llm.py / RuleExtractor → §9 IR):
  rule_key: hold_order_block
  rule_type: restriction
  conditions: [{field: credit_status, operator: "==", value: hold}]
  effect: {decision: block, message: "No order accepted for accounts on hold"}
  applies_to_intents: [create_order]
  requires_trace: true
  source_clause_id: clause_0001
  review_status: pending

BOUND RULE (semantic-layer publish-policy):
  conditions: [{field: credit_status, operator: "==", value: hold,
                value_kind: literal, value_refs: []}]
  bindings: {credit_status: {source: column, column: <table>.credit_status}}
  # publishes because credit_status resolves to a column.
```

---

## 7. Processing Pipeline (endpoints under `/design/skills`)

The nginx proxy maps `/design` → skill-builder:8000. Sections/clauses are
re-derived deterministically from stored `raw_text` on each call; the LLM is
invoked only at explicit steps (atoms, candidate rules).

### Step 1 — Upload
`POST /design/skills/documents/upload` (multipart file, or JSON
`{text, file_name, domain, owner, version}`). Store file, compute SHA-256, create
`source_documents` row, status `uploaded`. Idempotent on `(file_hash, version)`.

### Step 2 — Extract + Normalize → canonical markdown
`POST /design/skills/documents/{id}/extract` → persist `document_sections`,
status `markdown_generated`. Output artifact `source_policy.md`. Tables stay
tables; headings/para IDs/page refs preserved; never summarized.

### Step 3 — Profile  *(new)*
`POST /design/skills/documents/{id}/profile` → `document_profile.yaml`
(`schema_version: prefront.document_profile.v1`): detected source type, domain
guess + confidence, structural features (numbered sections, definitions table,
approval matrix, thresholds, exceptions, audit, related docs), extraction
strategy, and warnings (e.g. `RELATED_POLICIES_REFERENCED`).

### Step 4 — Segment clauses
`POST /design/skills/documents/{id}/segment-clauses` → `policy_clauses` +
`clauses.yaml`. Units from paragraphs, bullets, numbered subclauses, table rows,
matrix rows, definitions, exception/audit statements. Skips boilerplate
(Purpose, Definitions, Revision History, Glossary, …).

### Step 5 — Classify clauses  *(new, explicit)*
`POST /design/skills/documents/{id}/classify-clauses`. Today `clause_type` is set
heuristically in `segment.py`; this step makes classification an explicit
(LLM-assisted) stage and assigns a **disposition** to every clause. Allowed
`clause_type` values match `schema.py.ClauseType`:
`definition, eligibility_rule, approval_threshold, restriction, exception,
role_permission, data_access_rule, regional_rule, audit_requirement,
fallback_or_escalation, explanatory`.
Allowed dispositions: `rule_candidate_required, atom_candidate_required,
definition_only, related_policy_reference, unresolved, non_enforceable_context,
duplicate, needs_human_review`.

### Step 6 — Extract policy atoms  *(new)*
`POST /design/skills/documents/{id}/extract-policy-atoms` → `policy_atoms.yaml`
(`prefront.policy_atoms.v1`). Domain-neutral atoms with `actor/action/object/
condition/effect/source/confidence`. Extract only what the clause supports; use
null when absent; emit unresolved items for required-but-missing info.

### Step 7 — Domain mapping  *(new pre-check)*
`POST /design/skills/documents/{id}/map-domain`. Map raw concepts to the domain
pack vocabulary (mirroring the four namespaces). Outcomes:
`mapped | unmapped | ambiguous | inferred | requires_review`. Anything unmapped
becomes an unresolved item. Do not invent mappings.

### Step 8 — Generate candidate rules
`POST /design/skills/documents/{id}/extract-rules`
(body: `{domain, known_roles, known_fields, known_intents}`). `RuleExtractor`
emits the **flat IR of §9** (`review_status: pending`). Exact-duplicate rules
(same `rule_key` + identical condition/effect) are deduped, highest confidence
kept. Returns `{candidate_rules_created, errors[], requires_review}`.

### Step 9 — Validate candidate rules
`POST /design/skills/documents/{id}/validate` → `validation_report.yaml`. Runs
the §12 validators in order. A rule is publishable only when all validators pass
**and** `review_status: approved`.

### Step 10 — Generate tests
`POST /design/skills/documents/{id}/generate-tests` → `test_cases.yaml`
(`prefront.policy_tests.v1`), via `tests_gen.py` (trigger + negative per rule).
A rule with no derivable test is flagged `untestable` and is not publishable.

### Step 11 — Human review
Reviewer actions: approve / reject / edit candidate; mark/resolve/​waive
unresolved; map unknown role/field; merge duplicates; split; add test; approve &
publish skill. UI shows clause ↔ atom ↔ candidate rule ↔ validation ↔ unresolved
↔ tests side by side (§17).
`POST /design/skills/candidate-rules/{id}/approve` (→ `ApprovedRule`),
`POST /design/skills/candidate-rules/{id}/reject`.

### Step 12 — Publish skill
`POST /design/skills/{skill_id}/publish`
(body `{document_id, name?, domain?, owner?, approved_only=true}`) writes the
published artifacts (§18) into `skills/<skill_id>/v<version>/`. Only approved
rules enter `extracted_rules.yaml` as `status: active`; the rest stay `draft`.

---

## 8. Domain Pack Design

A domain pack gives the system enough vocabulary to map business language onto
the four binding namespaces *before* publish, so mismatches surface as unresolved
items early instead of as publish rejections later.

```yaml
schema_version: prefront.domain_pack.v1
domain: credit_collections
version: 1.0
status: active

# fields -> hint which namespace the symbol is expected to bind to.
fields:
  credit_status:
    type: enum
    allowed_values: [good, watch, hold]
    binds_to: column            # column | request_param | metric | caller
    aliases: [credit status, account standing]
  order_value:
    type: money
    binds_to: request_param
    aliases: [order value, net order value, order total]
  available_credit:
    type: money
    binds_to: metric            # app supplies the expression at publish time
    aliases: [available credit]

roles:                          # normalized + aliases (fold into caller.role)
  rep:           {aliases: [sales rep, sales representative]}
  manager:       {aliases: [Manager]}
  credit_analyst:{aliases: [Credit Analyst]}

actions:                        # actions map to INTENTS (not a separate axis)
  create_order:   {intent: create_order, aliases: [new order, accept order]}
  read_credit:    {intent: get_customer_credit, aliases: [read credit standing]}

# reason codes are a DESIGN-TIME catalog that render into effect.message.
reason_codes:
  CUSTOMER_ON_CREDIT_HOLD: {message: "Customer is on credit hold."}
  ORDER_EXCEEDS_AVAILABLE_CREDIT: {message: "Order exceeds available credit."}
```

Domain-pack rules:
1. Every executable field should declare a `binds_to` namespace; the binder is
   still the authority, the pack is the pre-check.
2. Actions map to **intents** (`applies_to_intents`), never to a separate
   `actions` axis.
3. Approver roles normalize to `caller.role` values / known roles.
4. Unknown concepts → unresolved items.
5. Aliases the LLM proposes must be human-approved before joining a pack.

---

## 9. Candidate / Approved Rule IR (the real shape)

This is the **strict contract** from `skillbuilder/schema.py`. It is what the
LLM must emit and what every downstream consumer accepts.

```yaml
rule_key: order_blocked_when_customer_on_hold   # lower_snake_case (validated)
rule_type: restriction         # see enum below
conditions:                    # AND-combined; minimum 1
  - field: credit_status
    operator: "=="
    value: hold
effect:
  decision: block              # see enum below
  approval_required: false     # optional
  approver_role: null          # optional
  restricted_fields: []        # optional; columns to mask/deny
  message: "No order accepted for accounts on hold"
applies_to_intents: [create_order]    # flat list — NOT applies_to.{actions,intents}
requires_trace: true
confidence: 0.0                # single float in [0,1]
ambiguities: []                # list[str]
source_evidence: "…verbatim quote from the clause…"
source_clause_id: clause_0001  # set by the pipeline, not the LLM
review_status: pending         # pending | approved | rejected | needs_clarification
```

### Enums (exact)
```yaml
operator:   ["==", "!=", ">", "<", ">=", "<=", "in", "not_in"]
rule_type:  [approval_threshold, data_access, regional_access,
             restriction, exception, audit_requirement, mandatory_filter]
decision:   [allow, approval_required, block, mask, escalate]
review_status: [pending, approved, rejected, needs_clarification]
```

### Deliberately removed (and why)
| Removed from old draft | Use instead |
|---|---|
| `rule_type: hard_block / allow / route_for_review / …` | `rule_type` is one of the 7 above; the *decision* lives in `effect.decision` (e.g. block ⇒ `rule_type: restriction` + `decision: block`) |
| `then.reason_code` (enum) | `effect.message` (domain-pack reason codes render into it) |
| `then.route_to`, `then.override_allowed` | not modeled; routing/override are runtime/approval concerns |
| `applies_to: {actions, intents}` | `applies_to_intents: [...]` |
| operators `eq/neq/gt/exists/contains` | the symbolic operators above (no `exists`/`contains`) |
| nested per-axis `confidence` block, `priority` | single `confidence` float; precedence is fixed by the runtime (`decide.py`: block > approval_required > allow) |

`ApprovedRule` is the same shape plus `version`, `status` (`active|draft|
retired|superseded`), a full `Source` block, `approved_by/at`,
`effective_from/to`, `trace_required`.

### 9b. Arithmetic — metrics, not expression trees

**There is no expression tree.** The runtime looks up the **left** side of a
condition as a simple symbol; it does not evaluate arithmetic there. Express
"current_balance + order_value > credit_limit" in one of two real ways
(both seen in `skills/cr_fin_001/v3.2/extracted_rules.yaml`):

```yaml
# (a) metric on the left — preferred. App defines available_credit at publish.
- field: available_credit          # metric: credit_limit - current_balance
  operator: "<"
  value: order_value               # right side may be a bound symbol

# (b) arithmetic string on the RIGHT — binder tags it value_kind: expression
- field: credit_limit
  operator: "<"
  value: "(current_balance + order_value)"
```

At `publish-policy` the binder classifies each `value` as `literal` or
`expression` (safe AST: `+ - * /` and identifiers only) and records
`value_kind` + `value_refs`. The Skill Builder itself emits flat conditions and,
where arithmetic is needed, **names a metric the deployment must define** — it
never ships a formula the binder can't resolve.

### 9c. Symbol binding — the four namespaces

Every `field` (and every identifier inside an expression `value`) must resolve,
at `semantic-layer publish-policy` (`policybind.py`), to exactly one of:

| Namespace | Meaning | Example |
|---|---|---|
| `column` | a real column in the physical catalog (intent's root table wins collisions) | `credit_status` → `customer_risk_profiles.credit_status` |
| `request_param` | a value the agent supplies, **declared** by the intent's approved template | `order_value` |
| `metric` | an app-supplied derived value (never hardcoded in the engine) | `available_credit = credit_limit - current_balance` |
| `caller.*` | trusted caller context injected by identity | `caller.role` |

A symbol resolving to none of these ⇒ the rule is **rejected at publish** with a
reason; the runtime never sees unresolvable policy. The Skill Builder's job is to
make sure this rejection is *impossible* by the time of publish — by mapping
symbols (§7 step 7) and raising unresolved items (§10) for anything that won't
bind.

**Authorization special case:** an `allow` rule keyed on `caller.role` / `role`
is folded by the binder into `intents_map[intent].allowed_roles` (who may invoke
the intent), not a per-call predicate. The Skill Builder should emit
role-permission clauses as such `allow` rules (see `sales_staff_read_credit`,
`place_account_hold` in the real artifact).

---

## 10. Unresolved Items Format

Produced whenever the system cannot safely generate a publishable rule.

```yaml
schema_version: prefront.unresolved_items.v1
document_id: doc_001
unresolved_items:
  - unresolved_id: u_001
    type: unmappable_symbol
    severity: high
    status: open
    source: {document_id: doc_001, clause_id: c_004_003, section: "4.3",
             evidence: "discount above the rep's base authority"}
    issue: "field 'base_authority' maps to no column / request_param / metric / caller attr"
    impact: "rule would be rejected at publish-policy"
    recommended_action: "declare a metric or template param, or map to a column"
    blocks_publication: false
    blocks_related_rules: [high_risk_discount_approval]
```

Allowed types (each tied to a concrete cause):
```yaml
unresolved_type:
  - unmappable_symbol          # fails the §9c binding pre-check
  - unknown_role               # role not in domain pack / not a caller.role value
  - unknown_action             # action with no intent mapping
  - missing_metric             # arithmetic needs a metric the deployment lacks
  - vague_condition
  - missing_threshold
  - missing_related_policy
  - conflicting_policy_statement
  - ambiguous_approver
  - non_executable_language
  - missing_exception_expiry
  - missing_audit_detail
  - llm_output_invalid         # extractor produced unparseable / invalid output
```
Severity `low|medium|high|critical`. Use `critical` when the item could make a
block/allow decision wrong. (Types from the old draft that *cannot occur* given
§9 — e.g. "unsupported_rule_pattern" for expression trees — are dropped.)

---

## 11. Clause Ledger

Proves every clause was processed (`prefront.clause_ledger.v1`).

```yaml
document_id: doc_001
clauses:
  - clause_id: clause_0001
    section: "4.1"
    disposition: rule_extracted
    generated_atoms: [a_004_001]
    generated_rules: [hold_order_block]
    unresolved_items: []
  - clause_id: clause_0003
    section: "4.3"
    disposition: unresolved
    reason: "discount authority symbol won't bind"
    generated_atoms: [a_004_003]
    generated_rules: []
    unresolved_items: [u_001]
```
Dispositions: `rule_extracted, atom_extracted, definition_only,
non_enforceable_context, unresolved, duplicate, related_policy_reference,
needs_human_review`. No clause may remain `unprocessed` after extraction.

---

## 12. Validation Engine

The trust layer. Today's `conflicts.py` becomes the consistency/coverage
validators; the rest are added under `validation/`.

### 12.1 Schema validator (`validation/schema.py`)
Required fields present; enums valid (operators/rule_type/decision/​review_status
per §9); ≥1 condition; `rule_key` is lower_snake_case; source evidence present;
confidence present.

### 12.2 Source-grounding validator (`validation/grounding.py`)
`source_clause_id` exists; `source_evidence` substring appears in the clause/
section; the rule introduces no facts absent from the source (or a reviewer
edit).

### 12.3 Semantic validator (`validation/semantic.py`)
Fields/roles/actions exist in the domain pack; enum values are allowed;
money/number/date types are valid; actions resolve to **intents**; reason-code
references resolve to messages. (Reframed around §9 vocabulary, not the old
`reason_code` enum.)

### 12.4 Executability validator (`validation/executability.py`)
**Defined in terms of §9c:** every condition `field` and every identifier in an
expression `value` resolves to one of the four namespaces (column /
request_param / metric / caller); left sides are simple symbols (no left
arithmetic); `effect.decision` is one of the enum; the rule has ≥1
`applies_to_intents` (else the binder skips it). A symbol that won't resolve ⇒
unresolved item, not a silent pass.

### 12.5 Consistency validator (`validation/consistency.py` → `conflicts.py`)
Unknown roles/fields, contradictory effects (same condition → different
decision), duplicate `rule_key` with different body, numeric threshold overlaps
with differing decisions, orphan exceptions (no base rule).

### 12.6 Coverage validator (`validation/coverage.py`)
Every clause has a final disposition; every enforceable clause produced a rule
or an unresolved item; every related-policy reference recorded; every
approval-matrix row processed.

### 12.7 Testability validator (`validation/testability.py`)
Each block/approval rule has ≥1 positive and (where relevant) a boundary/negative
test from `tests_gen.py`; expected decision deterministic; input facts cover all
condition fields.

---

## 13. Validation Report Format

```yaml
schema_version: prefront.validation_report.v1
document_id: doc_001
extraction_run_id: run_001
summary:
  source_clauses_total: 42
  clauses_processed: 42
  candidate_rules_total: 21
  schema_valid_rules: 21
  source_grounded_rules: 20
  semantic_valid_rules: 18
  executable_rules: 15            # i.e. all symbols bind (§9c)
  testable_rules: 15
  publishable_rules: 0           # 0 until human approval
  unresolved_items_total: 11
  critical_unresolved_items: 1
quality_metrics:
  silent_drop_count: 0
  unmappable_symbol_count: 2
  missing_metric_count: 1
  executable_rule_rate: 0.71
blocking_issues:
  - {code: CRITICAL_UNRESOLVED_ITEM, message: "One critical unresolved item blocks publication."}
rule_results:
  - rule_key: hold_order_block
    schema_valid: true
    source_grounded: true
    semantic_valid: true
    executable: true
    testable: true
    publishable: false
    publish_blockers: [REVIEW_NOT_APPROVED]
```

---

## 14. Data Model — migrate sqlite3 → SQLAlchemy/Postgres

Today `store.py` uses `sqlite3` (no ORM) with: `source_documents`,
`document_sections`, `policy_clauses`, `candidate_rules`,
`approved_policy_rules`, `skill_versions`. The target is **SQLAlchemy 2.x +
Alembic on PostgreSQL**.

### 14.1 Migration approach
- Replace `store.py` internals with SQLAlchemy models + a session factory; keep
  the public functions (`persist_structure`, candidate/approved CRUD, etc.) so
  callers don't change.
- Add Alembic; the **initial migration reproduces the existing columns** (incl.
  `candidate_rules.rule_json`, `confidence`, `review_status` enum **with
  `needs_clarification`**, `skill_versions.artifact_json`) so existing SQLite
  data can be backfilled if needed.
- Then add net-new tables: `document_profiles`, `policy_atoms`,
  `unresolved_items`, `validation_runs`, `review_events`, `domain_packs`.

### 14.2 Net-new tables (illustrative)
```sql
CREATE TABLE policy_atoms (
  atom_id UUID PRIMARY KEY,
  document_id UUID NOT NULL REFERENCES source_documents(document_id),
  clause_id  UUID NOT NULL REFERENCES policy_clauses(clause_id),
  extraction_run_id UUID REFERENCES extraction_runs(extraction_run_id),
  atom_type TEXT NOT NULL, atom_json JSONB NOT NULL, confidence JSONB,
  created_at TIMESTAMP NOT NULL DEFAULT now()
);
CREATE TABLE unresolved_items (
  unresolved_id UUID PRIMARY KEY,
  document_id UUID NOT NULL REFERENCES source_documents(document_id),
  clause_id UUID REFERENCES policy_clauses(clause_id),
  candidate_rule_id UUID REFERENCES candidate_rules(candidate_rule_id),
  unresolved_type TEXT NOT NULL, severity TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'open', item_json JSONB NOT NULL,
  resolved_by TEXT, resolved_at TIMESTAMP, resolution_notes TEXT,
  created_at TIMESTAMP NOT NULL DEFAULT now()
);
CREATE TABLE review_events (
  review_event_id UUID PRIMARY KEY,
  document_id UUID REFERENCES source_documents(document_id),
  candidate_rule_id UUID REFERENCES candidate_rules(candidate_rule_id),
  unresolved_id UUID REFERENCES unresolved_items(unresolved_id),
  event_type TEXT NOT NULL, actor TEXT NOT NULL,
  before_json JSONB, after_json JSONB, comment TEXT,
  created_at TIMESTAMP NOT NULL DEFAULT now()
);
```
(`extraction_runs`, `document_profiles`, `validation_runs`, `domain_packs`
follow the same pattern; existing tables keep their current columns.)

### 14.3 Infra blast radius (must change together)
`docker-compose.yaml` currently sets `SKILLBUILDER_DB=/data/skillbuilder.db` and a
`skillbuilder_data` volume. The migration replaces these with a **Postgres DSN**
(e.g. `SKILLBUILDER_DB=postgresql+psycopg://…`) and a **dedicated design-time
Postgres service**. Do **not** point the Skill Builder at the example tenant's
runtime Postgres (`:5433`) — design-time state is separate from tenant data, per
the domain-independence principle. `SKILLBUILDER_REGISTRY=/data/skills` (the
published-artifact path) is unaffected.

---

## 15. API Design (real prefix `/design/skills`)

### Existing (keep)
```http
POST   /design/skills/documents/upload
GET    /design/skills/documents
DELETE /design/skills/documents/{document_id}
POST   /design/skills/documents/{document_id}/extract
POST   /design/skills/documents/{document_id}/segment
POST   /design/skills/documents/{document_id}/extract-rules
GET    /design/skills/candidate-rules            (?document_id=…)
POST   /design/skills/candidate-rules/{id}/approve
POST   /design/skills/candidate-rules/{id}/reject
POST   /design/skills/{skill_id}/publish
GET    /design/skills/versions
GET    /healthz
```

### Net-new (additive stages)
```http
POST   /design/skills/documents/{id}/profile
POST   /design/skills/documents/{id}/classify-clauses
POST   /design/skills/documents/{id}/extract-policy-atoms
POST   /design/skills/documents/{id}/map-domain
POST   /design/skills/documents/{id}/validate
GET    /design/skills/documents/{id}/validation-report
POST   /design/skills/documents/{id}/generate-tests
GET    /design/skills/documents/{id}/unresolved-items
PATCH  /design/skills/unresolved-items/{unresolved_id}
POST   /design/skills/unresolved-items/{unresolved_id}/resolve
GET    /design/skills/documents/{id}/clauses
PATCH  /design/skills/candidate-rules/{id}          (edit)
```

---

## 16. LLM Prompting Strategy

Multiple narrow prompts via `RuleExtractor` (`llm.py`) — not one mega prompt.
Passes: (1) document profile, (2) clause classification, (3) policy-atom
extraction, (4) domain mapping, (5) candidate-rule generation. Each pass returns
**only JSON** matching its schema; the system prompt forbids inventing
thresholds/roles/fields and requires unresolved items for required-but-missing
info. Candidate-rule generation must emit the **flat §9 IR** (no expression
trees, no reason_code) and cite source evidence per rule.

Retry/repair (already in `llm.py`): on invalid JSON/schema, retry once with the
validation error; on persistent failure store a failed-extraction record and
emit an `llm_output_invalid` unresolved item — never silently drop the clause.
Definition/explanatory clauses are skipped by `_SKIP_TYPES`.

---

## 17. Frontend (extend the existing React app)

The UI is the single-page app in `prefront-ui/src/` with tabs **Policy Studio**
(`PolicyStudio.jsx`), **Data Connector** (`DataConnector.jsx`), **Interfaces**
(`Interfaces.jsx`); rule cards via `RuleCard.jsx`; calls go through `api.js`;
nginx proxies `/design/*` → :8000 and `/design/semantic/*` → :8010.

Add to Policy Studio (no new frontend package):
- **Clause Ledger view** — clause → type/disposition → atoms → rules →
  unresolved, proving full coverage.
- **Unresolved Items view** — filter by severity/type/status/section/blocking;
  actions: resolve, map role, map field/metric, upload related policy, waive with
  reason.
- **Validation Report panel** — per-rule badges (schema/grounded/semantic/
  executable/testable/publishable) and blocking issues.
- **Candidate Rule review** (extend `RuleCard.jsx`) — left: source clause +
  evidence; middle: the §9 rule; right: validation + tests. Actions: approve /
  reject / edit / mark unresolved / add test.
- **Publish panel** — approved vs blocked rules, open unresolved items, test
  coverage, artifact preview; publish disabled while critical blockers exist.

---

## 18. Artifact Output Layout

Published skill (registry path, unchanged):
```text
skills/<skill_id>/v<version>/
  source_policy.md
  policy_skill.yaml
  extracted_rules.yaml      # only approved rules become status: active
  test_cases.yaml
  review_report.yaml
```
Per extraction run (new intermediates):
```text
skills/<skill_id>/v<version>/runs/<run_id>/
  document_profile.yaml
  clauses.yaml
  clause_ledger.yaml
  policy_atoms.yaml
  unresolved_items.yaml
  validation_report.yaml
```

`extracted_rules.yaml` is exactly the shape `semantic-layer` reads (see the real
`skills/cr_fin_001/v3.2/extracted_rules.yaml`): top-level `skill_id`,
`source_document`, `document_version`, `domain`, `generated_by`, then `rules:`
in the §9 IR with a full `source` block + `status` + `review_status`.

---

## 19. Publication Rules

A skill version publishes only when every published rule is approved,
schema-valid, source-grounded, semantic-valid, **executable (all symbols bind,
§9c)**, and testable; all *critical* unresolved items are resolved or explicitly
waived; a review log exists; and the source document hash is recorded. Medium/low
unresolved items may remain if a reviewer waives them with a reason:

```yaml
waiver:
  waived_by: policy_admin
  waived_at: "2026-06-14T10:30:00Z"
  reason: "Related policy affects discount rules only; credit-hold rules publish independently."
  expires_at: "2026-09-14T00:00:00Z"
```

---

## 20. Example Candidate Rule (real, round-trips through the binder)

The over-limit decline, expressed with a right-hand expression value (matches
`decline_over_limit_orders` in the real artifact):

```yaml
rule_key: decline_over_limit_orders
rule_type: restriction
conditions:
  - field: current_balance
    operator: ">"
    value: 0
  - field: order_value
    operator: ">"
    value: 0
  - field: credit_limit
    operator: "<"
    value: "(current_balance + order_value)"   # binder → value_kind: expression
effect:
  decision: block
  message: "Order declined because current_balance plus order_value exceeds credit_limit"
applies_to_intents: [create_order]
requires_trace: true
confidence: 0.9
source_evidence: "current_balance + order_value > credit_limit"
source_clause_id: clause_0002
review_status: pending
```
At publish, `current_balance`/`credit_limit` bind to columns and `order_value`
to a request param, so the rule publishes.

---

## 21. Acceptance Criteria

### MVP
1. Upload .md/.txt/.docx/text-PDF.
2. Canonical markdown produced.
3. Document segmented into clauses; every clause gets a classification +
   disposition.
4. Policy atoms extracted from enforceable clauses.
5. Atoms mapped via a domain pack; unmapped symbols → unresolved items.
6. Candidate rules emitted in the **exact §9 IR** (no expression trees /
   reason_code / hard_block).
7. Unresolved items produced for missing/ambiguous/unmappable content.
8. Validators run (schema, grounding, semantic, executability, consistency,
   coverage, testability).
9. Reviewer can approve/reject/edit rules and resolve/waive unresolved items.
10. Only approved rules publish; published `extracted_rules.yaml` round-trips
    through `semantic-layer publish-policy` with **zero rejections**.
11. Published skill includes source hash, per-rule evidence, validation report,
    tests, review log.
12. No clause silently dropped.

### Quality (sample-policy benchmark)
```text
silent_drop_count = 0
every published rule's symbols bind to one of the four namespaces
all hard-block clauses extracted or unresolved
all approval requirements extracted or unresolved
all related-policy references become unresolved_items
all executable rules have tests
no rule contains a left-side arithmetic expression
```

---

## 22. Implementation Plan (mapped to real modules)

1. **Persistence migration** — `store.py` → SQLAlchemy + Alembic on Postgres;
   initial migration reproduces existing tables; update `docker-compose.yaml`
   (§14.3). *(largest single change)*
2. **Document profiler** — `profiler.py` + `/profile` + `document_profile.yaml`.
3. **Explicit clause classifier** — `classifier.py` + `/classify-clauses`;
   disposition on every clause.
4. **Policy atoms** — `atoms.py` + `/extract-policy-atoms` + `policy_atoms.yaml`;
   atom → §9 lowering (§6.5).
5. **Domain packs** — `domain_packs/` + loader + `/map-domain`; alias matching;
   `binds_to` namespace hints.
6. **Unresolved items** — `unresolved.py` + table + endpoints + artifact; wire
   into atoms/mapping/validation.
7. **Validation engine** — `validation/` package; fold `conflicts.py` into
   `consistency.py`; add executability (the §9c binding pre-check) + the rest;
   `validation_report.yaml`.
8. **Clause ledger** — emit `clause_ledger.yaml`; coverage validator consumes it.
9. **Frontend** — extend Policy Studio with Clause Ledger, Unresolved Items,
   Validation Report, richer rule review + publish panel (§17).
10. **Round-trip test** — publish a sample skill and assert
    `semantic-layer publish-policy` returns `rejected: []`.

---

## 23. Hard Engineering Rules

1. LLM output is always candidate output (`review_status: pending`).
2. LLM output is parsed into Pydantic models; invalid output is never silently
   ignored (→ `llm_output_invalid` unresolved item).
3. Every source clause has a disposition.
4. Every candidate rule cites source evidence + clause.
5. Every executable rule uses only symbols that bind to the four namespaces
   (§9c).
6. Every unmappable symbol / unknown role / unknown action / missing metric
   creates an unresolved item.
7. **No expression trees, no free-form formula on the left, no `reason_code`
   enum, no `hard_block` rule type** — only the §9 IR.
8. No candidate rule publishes without human approval.
9. Published artifacts are immutable; approved rules are versioned; review edits
   are logged.
10. Domain vocabulary lives only in domain packs / config / artifacts — never in
    engine code (`prefront/CLAUDE.md`).
11. The Skill Builder stops at approved rules; the **semantic-layer** binds them
    and the **semantic-mcp-server** enforces them. The Skill Builder is never the
    runtime engine.

---

## 24. Definition of Done

A reviewer can: upload a policy; see canonical markdown; see every clause and its
disposition; see candidate rules in the real §9 IR; see unresolved items for
unclear/unmappable content; see validation results before approval; approve/edit/
reject rules; resolve/waive unresolved items; publish a versioned skill; and
trace every approved rule to source evidence.

The decisive test: the published `extracted_rules.yaml`, once approved, passes
`semantic-layer publish-policy` with **no rejected rules** — i.e. every symbol
binds. A plausible-looking YAML that the binder would reject is *not* done.
