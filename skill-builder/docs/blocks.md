# Skill-Builder — Blocks Explained

A walkthrough of every block in the [block diagram](block_diagram.md), with real
field names from the code and worked examples. The diagram is the map; this is the
legend.

Skill-Builder is a **policy compiler**: a messy business-policy document goes in,
versioned + human-approved runtime rules come out. The one hard rule: **LLMs are
used only at design time; everything the runtime consumes is deterministic,
validated, and approved.** Anything an LLM emits is a *candidate* until it passes
validation and a human approves it.

To keep the blocks connected, one example clause is carried the whole way down:

> *"Orders over USD 50,000 for watch-list customers require approval by the
> Regional Sales Manager."*

Pipeline order (see `pipeline.py`):

```
Extract → Normalize → Segment        (deterministic text pipeline)
  → Profile → Classify → Atoms       (understanding; LLM-assisted, heuristic fallback)
  → Extract Rules                    (the ONE required LLM step)
  → Validate (6 passes) + Ledger     (deterministic)
  → Human Review                     (FastAPI + React)
  → Publish                          (deterministic → artifacts)
  → semantic-layer binder            (downstream; expects zero rejections)
```

---

## 1. Deterministic text pipeline

No LLM anywhere in this band. Bytes become clean, citable, atomic clauses.

### 1a. Extract — `extract.py`

**Job:** turn a file into plain text. Dispatches on suffix.

- `extract_text(path) -> str`
- Supported: `.md`, `.markdown`, `.txt`, `.text`, `.docx`, `.pdf` (`SUPPORTED_SUFFIXES`).
- `.docx` preserves heading levels from style names; `.pdf` emits page markers
  `<<<PAGE N>>>` for the normalizer to consume.
- Missing optional dependency → an actionable error, never a silent empty string.
  Scanned-PDF OCR is deliberately out of scope (a non-goal).

```
DOCX paragraphs (styled)            ──▶  plain text
  [Heading 1] "Credit Policy"            # Credit Policy
  [Normal]    "Orders over ..."          Orders over ...
```

### 1b. Normalize — `normalize.py`

**Job:** raw text → **canonical markdown** + structured `Section`/`Paragraph`
objects with stable IDs, so every later artifact can cite a precise location.

- `normalize(raw_text, *, document_id, version, file_name, title=None) -> NormalizedDoc`
- Regex-driven: `_ATX_HEADING`, `_NUMBERED_HEADING`, `_PAGE_MARKER`.
- Assigns stable refs: `section_id = sec_001…`, `paragraph_ref = p001…`. These IDs
  are the spine of grounding and the clause ledger later.

```
raw                                NormalizedDoc
<<<PAGE 1>>>                        canonical_markdown:  "# Credit Policy\n...[page:1]..."
## 4.2 Approvals          ──▶       sections:   [Section(section_id="sec_002",
Orders over USD 50,000 for                       section_path="4.2 Approvals",
watch-list customers require                     page_start=1, markdown="Orders over ...")]
approval by the Regional                paragraphs: [Paragraph(paragraph_ref="p007",
Sales Manager.                                       page=1, section_path="4.2 Approvals",
                                                     text="Orders over USD 50,000 ...")]
```

### 1c. Segment — `segment.py`

**Job:** split sections into **atomic clauses** and tag each with a heuristic
`clause_type` (a routing hint; the LLM refines it later). **No clause is ever
dropped** — that is what lets the ledger prove completeness.

- `segment(doc) -> list[Clause]` — fine-grained (sentence-ish split).
- `segment_sections(doc) -> list[Clause]` — coarse (one clause per non-boilerplate
  section; keeps tables/lists intact). **This is the default path** in `pipeline.py`.
- `_classify(text) -> ClauseType` — keyword heuristic; defaults to `explanatory`.
- `is_boilerplate_section(...)` — drops Purpose/Scope/Definitions/Revision History/
  Glossary/etc. from rule extraction (still recorded).

`ClauseType` (11 values): `approval_threshold`, `restriction`, `exception`,
`regional_rule`, `data_access_rule`, `role_permission`, `audit_requirement`,
`eligibility_rule`, `fallback_or_escalation`, `definition`, `explanatory`.

**A `Clause` carries:** `clause_id`, `document_id`, `section_id`, `section_path`,
`page_number`, `paragraph_ref`, `clause_type`, `disposition` (set later),
`source_text`.

```
Section "4.2 Approvals"  ──▶  Clause(
                                clause_id="clause_0007",
                                section_path="4.2 Approvals", page_number=1, paragraph_ref="p007",
                                clause_type="approval_threshold",   # matched "approval"
                                source_text="Orders over USD 50,000 for watch-list customers
                                             require approval by the Regional Sales Manager.")
```

---

## 2. Understanding — LLM-assisted, heuristic fallback

Every block here **degrades gracefully**: with an LLM client it does the smart
thing; with none it falls back to deterministic heuristics and still returns
something. Nothing here is the rule source — that is block 3.

### 2a. Profile — `profiler.py`

**Job:** detect the document's shape and guess its domain, so the operator knows
what they're dealing with before extraction.

- `profile_document(canonical_markdown, *, domain=None, client=None) -> DocumentProfile`
- LLM path: `client.chat_json(...)` over the first ~12k chars. Fallback:
  `_heuristic_profile()` (regex feature flags, low confidence — 0.5 with a domain
  hint, 0.0 without).
- `structural_features`: `has_numbered_sections`, `has_definitions_table`,
  `has_approval_matrix`, `has_thresholds`, `has_exceptions_section`,
  `has_audit_section`, `has_related_documents`.

```
DocumentProfile {
  detected_source_type: "business_policy",
  detected_domain: "credit_collections",  domain_confidence: 0.82,
  structural_features: { has_approval_matrix: true, has_thresholds: true, ... },
  extraction_strategy: ["section_clause_extraction", "approval_matrix_extraction"],
}
```

### 2b. Classify — `classifier.py`

**Job:** give every clause a **`disposition`** — the routing decision of what this
clause should become — and optionally refine its `clause_type`. No clause goes
unprocessed; the disposition is what the ledger records.

- `classify_clauses(clauses, *, client=None) -> list[Clause]`
- `heuristic_disposition(clause_type) -> Disposition` via `_DISPOSITION_BY_TYPE`:
  `definition → definition_only`, `explanatory → non_enforceable_context`,
  `audit_requirement → atom_candidate_required`, **everything else →
  `rule_candidate_required`**; unknown → `needs_human_review`.

`Disposition` values: `rule_candidate_required`, `atom_candidate_required`,
`definition_only`, `related_policy_reference`, `unresolved`,
`non_enforceable_context`, `duplicate`, `needs_human_review`, plus the
post-extraction markers `rule_extracted`, `atom_extracted`.

```
Clause(clause_type="approval_threshold", disposition=None)
  ──▶ Clause(clause_type="approval_threshold", disposition="rule_candidate_required")
```

### 2c. Extract Atoms — `atoms.py`

**Job:** carve each clause into **domain-neutral policy atoms** — the explainable
middle layer (`clause → atom → rule`) the ledger uses to prove the chain. Additive
and optional: requires an LLM client; with none it returns `[]`. The direct
clause→rule extractor (block 3) remains the path of record.

- `extract_atoms(clauses, *, client=None) -> list[PolicyAtom]`
- A `PolicyAtom` has: `atom_id`, `clause_id`, `atom_type`, `actor`, `action: [str]`,
  `object`, `condition`, `effect`, `source_evidence`, `confidence`.
- 16 `atom_type`s: `prohibition`, `permission`, `obligation`,
  `approval_requirement`, `authority_assignment`, `threshold`, `exception`,
  `waiver`, `segregation_of_duties`, `audit_requirement`, `retention_requirement`,
  `data_access_permission`, `data_access_restriction`, `routing_requirement`,
  `definition`, `related_policy_reference`.
- **Hard constraint:** an atom must be *lowerable* to the flat rule IR — it carries
  no construct the IR forbids (no expression trees, no `reason_code`).

```
clause_0007 ──▶ PolicyAtom(
  atom_id="a_0003", atom_type="approval_requirement",
  actor="Regional Sales Manager", action=["approve"], object="order",
  condition={order_total: ">50000", credit_status: "watch"},
  effect={decision: "approval_required"},
  source_evidence="Orders over USD 50,000 ... require approval")
```

### 2d. Domain Pack — `domain_packs/loader.py` (sidecar to the whole band)

**Job:** a **design-time vocabulary + alias map** that mirrors the four runtime
binding namespaces (`column` / `request_param` / `metric` / `caller`), so the
validators can predict *before publish* whether a rule's symbols will bind. It is
**configuration, not engine code** — the binding authority is still the
semantic-layer catalog; the pack is only a pre-check.

- Built-ins ship under `domain_packs/`; uploads in `$SKILLBUILDER_DOMAIN_PACKS`
  override built-ins. `load_pack(domain)`, `list_pack_names()`.
- Five sections: `fields` (term → canonical + `binds_to` + `allowed_values` +
  aliases), `roles`, `actions` (→ **intent**), `reason_codes` (→ `effect.message`).
- The only shipped pack today is **`credit_collections`** (the CommerceRisk demo
  vocabulary). Adding a domain = drop a YAML file; no code change.

```
fields:
  order_total:      {type: money,  binds_to: column,  aliases: [order value, net order value]}
  credit_status:    {type: enum,   binds_to: column,  allowed_values: [good, watch, hold]}
  available_credit: {type: money,  binds_to: metric}
roles:
  regional_sales_manager: {aliases: [Regional Sales Manager, RSM]}
```

---

## 3. LLM Rule Extraction — `llm.py`  *(the one required LLM step)*

**Job:** the only place where reasoning happens. Turn clauses into flat
`CandidateRule`s — verbatim values, no invented facts — every one emitted with
`review_status="pending"`.

- `RuleExtractor(provider, model, …)` with `extract_clause(clause, ctx)` /
  `extract_clauses(...)` returning `ClauseExtraction { clause, candidates, errors,
  skipped }`. (`skipped=True` for `definition`/`explanatory` clauses.)
- `ExtractionContext { domain, known_roles, known_fields, known_intents }` grounds
  the prompt in the pack's vocabulary.
- Providers (OpenAI-compatible): **`nvidia`** (default, `meta/llama-3.3-70b-instruct`),
  `groq`, `deepseek`, `grok`/`xai`, `openai`.
- The system prompt is strict: extract **machine-enforceable** rules only; **never
  invent** thresholds/roles/fields not in the text; never condition on reference
  metadata (`domain`, `known_roles`, …); each `condition.field` must be concrete
  per-request data; conditions are AND-combined; cite a verbatim `source_evidence`.

**The flat IR it emits** (the `CandidateRule` contract — see block 5):

```yaml
rule_key: approve_large_order_watch
rule_type: approval_threshold        # approval_threshold|data_access|regional_access|
                                     # restriction|exception|audit_requirement|mandatory_filter
conditions:                          # AND-combined, >= 1
  - {field: order_total,    operator: ">",  value: 50000}
  - {field: credit_status,  operator: "==", value: watch}
effect:
  decision: approval_required        # allow|approval_required|block|mask|escalate
  approver_role: regional_sales_manager
  message: "Watch account over limit requires RSM approval."
applies_to_intents: [create_order]
requires_trace: true
confidence: 0.0
ambiguities: []
source_evidence: "Orders over USD 50,000 ... require approval by the Regional Sales Manager"
# source_clause_id is stamped by the pipeline, not the LLM
```

---

## 4. Validation Engine — `validation/engine.py` (+ siblings)

**Job:** deterministically prove what each candidate is worth. `run_all(rules,
clauses, *, pack, declared_params, metrics) -> ValidationReport`. Six passes; the
keystone is **executability**, which mirrors the downstream binder so the
semantic-layer sees zero rejections. **No silent drops** — every problem becomes a
first-class `UnresolvedItem`.

| Pass | Module | Checks | On failure |
|---|---|---|---|
| Grounding | `grounding.py` | `source_evidence` appears verbatim (whitespace/case-normalized) in the cited clause | `NOT_SOURCE_GROUNDED` |
| Semantic | `semantic.py` | symbols exist in pack vocab: approver role resolves, fields known, enum values allowed | `SEMANTIC_INVALID` |
| **Executability** | `executability.py` | every condition field **and** every identifier in an arithmetic value resolves to `column`/`request_param`/`metric`/`caller`; `applies_to_intents` non-empty | `NOT_EXECUTABLE` |
| Consistency | `conflicts.py` | contradictory effects, threshold overlaps, duplicate keys, orphan exceptions, unknown role/field | `CONSISTENCY_CONFLICT` |
| Testability | `tests_gen.py` | a trigger case can be synthesized for the rule | `NOT_TESTABLE` |
| Coverage | (in `engine`) | enforceable clauses that produced neither rule nor unresolved item | → unresolved item |

`ValidationReport.summary` counts: `candidate_rules_total`, `source_grounded_rules`,
`semantic_valid_rules`, `executable_rules`, `testable_rules`, `publishable_rules`,
`unresolved_items_total`, `critical_unresolved_items`, `clauses_total`,
`clauses_with_candidate_rules`. Each `RuleValidation` carries the per-rule booleans
plus a `publish_blockers` list (the codes above + `REVIEW_NOT_APPROVED`); a rule is
`publishable` only when every blocker is empty.

### Executability — the keystone, by example

`resolve_symbol(name, pack, declared_params, metrics)` resolution order:
`caller.*` → pack field → metric → request_param → **`None`** (unmappable).

```
PASSES                                         FAILS
conditions:                                    conditions:
  - {field: order_total,  op: ">",  value: 50000}   - {field: discount_pct, op: "<", value: "base_rate * 2"}
  - {field: caller.role,  op: "==", value: rep}  applies_to_intents: []
applies_to_intents: [create_order]
                                               → NOT_EXECUTABLE, two problems:
order_total → column (pack)                      • non_executable_language: "no applies_to_intents;
caller.role → caller namespace                       the binder would skip it"
50000 / "rep" are literals                       • missing_metric: value references unresolvable
                                                     symbol(s) ['base_rate']
```

The unmappable-symbol type is sharpened by `_kind`: a bad `role`/`caller_role`
field → `unknown_role`; otherwise → `unmappable_symbol`.

### The other passes, briefly

- **Grounding** (`grounding.check`): fails with `source_evidence` empty, the cited
  clause missing, or the phrase not found in the clause → catches an LLM that
  *invented* a control.
- **Semantic** (`semantic.check`): no pack ⇒ vacuously valid. Otherwise unknown
  `approver_role` → `unknown_role`; field not in vocab or enum value out of
  `allowed_values` → `vague_condition`. Handles comma-containing roles like
  `"Director, Credit & Collections"`.
- **Conflicts** (`detect_conflicts`): `contradictory_effect` (same condition,
  different decision), `threshold_overlap` (overlapping numeric ranges, different
  decisions), `duplicate_rule_key`, `orphan_exception`, `unknown_role/field`. Each
  is a `Conflict { conflict_id, severity, type, rules[], message, recommended_action }`.
  Conservative — it flags for a human, never auto-fixes.
- **Testability** (`tests_gen`): `generate_test_cases(rules)` synthesizes a
  **trigger** case (inputs satisfying all conditions → expected effect) and, where
  the operator allows, a **negative** case (→ expected allow). `untestable_rules()`
  returns keys for which no trigger could be built (e.g. an `in` operator given a
  non-list value).

```
rule (block & >=, in)                test_cases.yaml
conditions:                          - test_id: block_high_value__triggers
  - {field: order_total, op: ">=", value: 50000}   input: {order_total: 50000, region_id: EMEA}
  - {field: region_id,   op: "in", value: [EMEA, APAC]}   expected: {decision: block, approval_required: false}
effect: {decision: block}            - test_id: block_high_value__does_not_trigger
                                         input: {order_total: 49999, region_id: EMEA}
                                         expected: {decision: allow, approval_required: false}
```

### Clause Ledger — `ledger.py` (the no-silent-drops guarantee)

Every clause lands here with a disposition and its downstream links:

```
ClauseLedgerEntry {
  clause_id: "clause_0007", section: "4.2 Approvals",
  disposition: "rule_extracted",        # _infer(): rules? → rule_extracted; else unresolved? →
  generated_atoms: ["a_0003"],          #   unresolved; else atoms? → atom_extracted; else needs_human_review
  generated_rules: ["approve_large_order_watch"],
  unresolved_items: [] }
```

### Unresolved items — `unresolved.py`

First-class records of everything that could not be resolved.
`UnresolvedItem { unresolved_id, type, severity, status(open|resolved|waived),
source{document_id,clause_id,section,evidence}, issue, impact, recommended_action,
blocks_publication, rule_key }`. `type` ∈ `unmappable_symbol`, `unknown_role`,
`unknown_action`, `missing_metric`, `vague_condition`, `missing_threshold`,
`conflicting_policy_statement`, `ambiguous_approver`, `non_executable_language`,
`llm_output_invalid`, … `has_open_critical(store, document_id)` is the gate publish
checks.

---

## 5. Human Review & Approval — `api.py` + React UI (:5173)

**Job:** nothing publishes without a human. FastAPI under `/design/skills/…`
(nginx proxies `/design` → :8000); the React Policy Studio renders it.

**Inspect (GET):** `documents`, `candidate-rules`, `…/clauses`, `…/policy-atoms`,
`…/profile`, `…/clause-ledger`, `…/validation-report`, `…/unresolved-items`,
`versions`, `domain-packs`.

**Pipeline as sequential actions (POST):** `…/extract` → `…/segment` →
`…/extract-rules` (+ optional `…/profile`, `…/classify-clauses`,
`…/extract-policy-atoms`), or `…/run-full-extraction` to drive
profile→classify→atoms→rules→validate in one call and write run artifacts.

**Decide:** `candidate-rules/{id}/approve` (→ `review_status="approved"`, creates an
`ApprovedRule`), `…/reject`, `PATCH …/{id}` to edit (re-validates shape),
`unresolved-items/{id}/resolve` (open|resolved|waived).

**Publish is BLOCKED if** there is any **open critical unresolved item**
(`has_open_critical`); the contract also expects only approved rules in the bundle.

### The flat IR contract — `schema.py`

This is the spine the whole system serves. `CandidateRule` (the LLM/untrusted
shape): `rule_key`, `rule_type` (7 enum values), `conditions: [Condition{field,
operator, value}]` (operators: `== != > < >= <= in not_in`), `effect: Effect{decision
(allow|approval_required|block|mask|escalate), approval_required?, approver_role?,
restricted_fields?, message?}`, `applies_to_intents`, `requires_trace`, `confidence`,
`ambiguities`, `source_evidence`, `source_clause_id`, `review_status` (pending|
approved|rejected|needs_clarification). `ApprovedRule` adds runtime provenance:
`version`, `status` (active|draft|retired|superseded), full `source`, `approved_by/at`,
`effective_from/to`. `BindsTo` = `column|request_param|metric|caller` ties the IR to
the four namespaces. **There is no expression tree, no `reason_code`, no
`hard_block`** — anything richer cannot publish.

### Persistence — `store.py` + `db.py`

SQLAlchemy over Postgres (prod) or SQLite (dev/tests). Documents are **immutable**
(`file_hash + version` = identity; re-uploading identical bytes is idempotent);
sections/clauses are UPSERTed with **stable deterministic IDs** so re-extraction
never orphans a referenced clause. Tables: `source_documents`,
`document_sections`, `policy_clauses`, `candidate_rules`, `approved_policy_rules`,
`skill_versions`, `extraction_runs`, `document_profiles`, `policy_atoms`,
`unresolved_items`, `review_events`.

---

## 6. Publish — `artifacts.py` → `skills/<skill_id>/v<version>/`

**Job:** deterministically materialize approved rules into the artifact set the
semantic-layer consumes. The split below matters:

**Published (the contract surface):**
- `source_policy.md` — immutable, citable markdown
- `policy_skill.yaml` — skill metadata (id, name, version, domain, intents, rule_count)
- `extracted_rules.yaml` — **active (approved) rules only**, each with its full
  `source` provenance block (document_id, clause_id, section, page, paragraph_ref,
  evidence). *This is the file the binder reads.*
- `test_cases.yaml` — generated trigger/negative tests
- `review_report.yaml` — per-rule confidence/ambiguities/review_status + conflicts
- (`validation_report.yaml`, `unresolved_items.yaml` added when supplied)

**Per-run intermediates** (`runs/<run_id>/`, optional, for audit): `document_profile.yaml`,
`clauses.yaml`, `clause_ledger.yaml`, `policy_atoms.yaml`, `unresolved_items.yaml`,
`validation_report.yaml`.

CLI equivalent (`cli.py`): `python -m skillbuilder build <doc> --doc-id … --version …
[--domain …] [--provider nvidia] [--granularity section|clause] [--dry-run]`
(`--dry-run` segments only, no LLM).

---

## 7. Downstream — semantic-layer binder

Not part of this repo's runtime, but it's *why* every constraint above exists.
`extracted_rules.yaml` flows into `semantic-layer publish-policy`, which binds each
rule's symbols against the real catalog/templates/metrics/`caller.*` and produces
the enforceable `policy.yaml`. **Rules whose symbols don't resolve are rejected
here.** Because block 4's executability pass mirrors that binder, a correctly
validated skill produces **zero rejections** downstream — design-time validation is
the guarantee, not a hope.

---

## The two structural guarantees (why the blocks are shaped this way)

1. **No silent drops.** Every clause lands in the ledger with a disposition;
   anything that can't become a clean rule becomes a first-class `UnresolvedItem`.
   You can always answer "what did it do with clause N?"
2. **Nothing publishes without human approval.** The LLM only ever produces
   `pending` candidates; approval creates the `ApprovedRule`; publish is gated on
   zero open-critical unresolved items and approved-only rules.

Everything else — domain packs, atoms, the six validators, the executability
mirror — exists to make those two guarantees *provable* rather than aspirational.
