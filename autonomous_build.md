# Prefront Evaluation Engine — Implementation Order

Companion to `prefront-check-families.md` (the WHAT). This document is the HOW:
the build order for the engine that emits the verdict contract, for both
deployment modes, plus Preflight. Written against the `oob` branch as of
`d40aab1`.

---

# 1. Purpose

Everything upstream of judgment exists on `oob`:

```text
BUILT      tracing lib → Phoenix → ClickHouse `spans` → oob-ingest query API
BUILT      LoanPro subject app: scenario catalogue with expected_findings
BUILT      skill-builder: policy → approved → published rule packs
BUILT      inline decision path (semantic-mcp-server govern pipeline, profile mcp)
MISSING    the evaluation engine: checks → verdicts → combinator → findings
MISSING    Preflight: generated traffic against tool schemas
```

The engine consumes the canonical span store and published artifacts, and emits:

```text
{ rule_id | check_id, effect, status: satisfied | violated | indeterminate, evidence }
```

---

# 2. Design Principle: the engine names no demo

This extends the repo's existing domain-independence rule (CLAUDE.md, README)
to the evaluator. The engine core must contain **no** LoanPro (or SecureBank)
table, tool, caller, role, threshold, intent, or scenario name.
`grep -rin loanpro eval-engine/` must return zero hits, forever.

Everything demo-specific enters through **published artifacts**:

```text
Engine core (generic mechanism)          Published artifacts (per deployment)
─────────────────────────────            ─────────────────────────────────────
session reconstructor                    trace binding profile
provenance graph builder                 visibility profile
Family 2 checks (built-in)               rule pack        (from skill-builder)
Family 1 rule compiler + engines         intent catalog   (from semantic-layer)
Family 3 checks                          transform whitelist
combinator (inline | oob)                population baselines
findings store + API
```

LoanPro's role is **fixture, not dependency**: its catalogue run is the
engine's acceptance test, never its configuration.

Corollary: the `spans` table columns (`session_id`, `user_id`, `user_role`,
`channel`, `intent_name`, `tool_name`, `input_value`, `output_value`, `status`,
`attributes` map) are already the canonical shape — the engine reads only
these. Any attribute a check needs beyond the lifted columns is resolved
through the trace binding profile (artifact 3.1), never by hard-coding an
`app.*` key in a check.

---

# 3. Artifacts (new)

## 3.1 `trace_binding.yaml`

Maps canonical check inputs to span fields for a given subject app. Ships per
deployment; a default profile covers anything emitting the shared
`prefront_tracing.py` conventions.

```yaml
trace_binding:
  version: 1
  tool_span: { name_prefix: "tool ", kind: TOOL }
  turn_span: { name_prefix: "turn " }
  session_root: { name_prefix: "session " }
  fields:
    intent:      { column: intent_name }
    caller_role: { column: user_role }
    channel:     { column: channel }
    row_count:   { attr: "app.row_count" }
    columns:     { attr: "app.columns" }
    side_effect: { attr: "app.side_effect" }
    trust_class: { attr: "app.trust" }
  final_answer: { span: turn_span, field: output_value, last: true }
```

## 3.2 `visibility_profile.yaml`

Declares what the trace source can and cannot show, so OOB indeterminate
splits correctly into *missing precondition* vs *visibility gap*.

```yaml
visibility_profile:
  version: 1
  captures:
    tool_args: true
    tool_results: true          # rows up to app.row_count truncation
    llm_messages: true
    approval_events: false      # LoanPro emits none → approval_evidence
                                # indeterminate = visibility gap, not violation
    sql: false                  # by design; no check may want it
```

## 3.3 `rule_pack.yaml` (compiled Family 1)

Not authored by hand — produced by a new skill-builder compile step (§6 step
9) that lowers published rules into the three engine dialects:

```yaml
rule_pack:
  source_skill: loan_underwriting_v3      # provenance only; opaque to engine
  rules:
    - rule_id: R-014
      engine: temporal          # precondition | sequencing
      automaton: { before: {intent: "*"},  requires_fact: kyc_verified }
    - rule_id: R-021
      engine: predicate         # prohibition on args | approval_gate
      expr: "amount > 50000"
      effect: approval_required
      approver_roles: [Branch Manager]
      source:                   # materialized from the clause at publish time
        document: loan_underwriting_policy.md
        clause_id: cl-10-2-04
        section: "10.2 Individual Approval Limits"
        page: 14
        text: "Amounts above the officer limit require Branch Manager sign-off."
    - rule_id: R-033
      engine: content           # output prohibitions | field_restriction
      detectors: [{ field_names: [risk_score], scopes: [result, final_answer] }]
      effect: block
```

Every rule in the pack MUST carry its `source` citation block — skill-builder
already materializes this at publish (`source_clause_id` → document, section
path, page, clause text), so the compiler's job is to preserve it, never to
re-derive it. The engine treats `source` as an opaque payload it copies onto
verdicts: the engine never reads a policy document.

The mapping from skill-builder's `rule_type` vocabulary
(`approval_threshold | data_access | regional_access | restriction | exception
| audit_requirement | mandatory_filter`) to the check-families vocabulary
(`precondition | sequencing | prohibition | field_restriction | approval_gate`)
is defined ONCE, in the compiler, with rejects for anything unlowerable —
mirroring the existing publish-time symbol-resolution gate.

## 3.4 `intent_catalog.yaml` (Family 3 envelope)

Generated from semantic-layer bindings where they exist; for a
bring-your-own-agent deployment (LoanPro's situation) it is authored/approved
directly:

```yaml
intent_catalog:
  version: 1
  intents:
    - intent: check_application_status
      allowed_callers: { roles: [Applicant], channels: [portal] }
      side_effect: read
      fields: [application_id, status, updated_at]
      mandatory_filters: [user_id = caller.user_id]
      expected_volume: { rows_p99: 5 }
      trigger_descriptors: ["status of my application", "where is my loan"]
      closing_obligations: []
```

## 3.5 `findings` + `verdicts` + `conformance_tags` ClickHouse tables

Same DB as `spans`. `verdicts` = one row per (session, check) raw output —
**satisfied rows included, never discarded**; `findings` = post-combinator
violations, deduplicated; `conformance_tags` = the positive half: one row per
(session, rule) where the rule was *exercised and satisfied*, carrying the
full policy citation.

```sql
conformance_tags (
  session_id, trace_id, rule_id,
  policy_document, clause_id, section, page, clause_text,   -- from rule source
  evidence_span_ids Array(String),      -- the steps that satisfied it
  rule_pack_version, engine_version, evaluated_at
)
```

Applicability is the guard that makes tagging meaningful: a verdict is
three-valued (`satisfied | violated | indeterminate`) **only for rules whose
trigger/scope matched the session** (the precondition's tool fired, the
predicate's subject appeared, the restricted field was requested). Rules that
were never exercised record nothing — otherwise every clean trace would be
stamped with the entire policy and the tags would carry no information.

Spans are never mutated (`ReplacingMergeTree` makes retro-tagging fragile);
tags join to traces at query time by `session_id`/`trace_id`. Inline mode is
the exception: it sits on the path at decision time, so it additionally stamps
`prefront.rule.satisfied` / `prefront.rule.clause` span attributes as the
spans are born — the trace itself then carries its conformance record into
any downstream observability stack, not just Prefront's.

---

# 4. Package layout

```text
eval-engine/                      new top-level package, FastAPI worker+API :8120
  evalengine/
    reconstruct.py                spans → canonical Session (steps, facts, answer)
    provenance.py                 value provenance graph + trust classes
    binding.py                    trace_binding profile loader/resolver
    family2/                      one module per check, pure functions
    family3/                      call / scope / session / population
    family1/
      compilepack.py              rule_pack loader + validation
      temporal.py  predicate.py  content.py
    combinator.py                 THE only precedence + mode logic
    store.py                      verdicts/findings tables
    api.py                        /eval/* endpoints
    worker.py                     ingest-tail → evaluate-on-session-close
```

`oob-ingest` stays ingestion + span query. The engine is a separate package so
the same `family*/` + `combinator.py` import cleanly into
`semantic-mcp-server` for inline (§6 step 18) without dragging ClickHouse in.

---

# 5. Hard Rules

```text
 1. No demo vocabulary in engine code. grep -rin "loanpro\|securebank"
    eval-engine/ == 0 hits. Enforced by a CI/pytest guard (step 2).
 2. No LLM anywhere in the engine at evaluation time. LLMs appear only in
    the Family 1 compiler upstream (design time) and Preflight generation.
 3. Every check is a pure function: (Session, artifacts) → [Verdict].
    No I/O, no clock, no randomness inside a check.
 4. Every check emits ONLY the common contract. No check invents fields.
 5. The combinator is the only module that knows block > approval > allow,
    and the only place `mode: inline|oob` changes behavior.
 6. Inline: indeterminate → approval_required. Never allow-on-uncertainty.
 7. OOB: indeterminate must resolve to missing_precondition or
    visibility_gap via the visibility profile — never silently dropped.
 8. Evidence is span-id references + minimal excerpts, never full payload
    copies into findings.
 9. Family 2 runs with zero artifacts configured (built-in, day one).
    Family 1 and 3 degrade to "not configured", not to errors.
10. Unlowerable published rules are rejected at compile time with a reason,
    never shipped as best-effort.
11. Every finding records: engine version, rule_pack/catalog versions,
    binding profile version, visibility profile version.
12. Re-running the engine over the same spans + same artifact versions
    must produce byte-identical findings (idempotent, replayable).
13. Population checks read only aggregates of prior verdicts/sessions —
    never raw payloads across tenants/sessions.
14. The LoanPro grading harness (step 15) is a test dependency of the repo,
    not a runtime dependency of the engine.
15. Satisfied verdicts are first-class output: persisted, versioned, and
    surfaced as conformance tags — never dropped as "no finding".
16. A rule tags a trace only when it was exercised (applicability matched).
    Not-applicable is recorded nowhere; it is not a third tag.
17. Policy citations on tags come ONLY from the rule pack's published
    source block. The engine never opens, parses, or names a policy
    document; Family 2/3 conformance tags carry no policy citation unless
    the intent catalog entry itself declares a source clause.
```

---

# 6. Implementation Order

Give this exact order to the coding agent. Steps 1–8 need no policy artifacts
at all (Family 2 is built-in) — findings from day one, matching the pitch.

```text
Phase A — skeleton + Family 2 (no onboarding required)
 1. Create eval-engine package, config, ClickHouse verdicts/findings DDL.
 2. Add the domain-independence guard test (grep-based, runs in pytest).
 3. Implement binding.py: load trace_binding.yaml, resolve canonical
    fields from span columns/attribute map; ship the default profile.
 4. Implement reconstruct.py: spans for one session_id → ordered Session
    (turns, tool steps with args/results, errors, final answer).
 5. Implement provenance.py: origin resolution (exact → normalized →
    whitelisted transform → none) + trust tagging; transform whitelist
    loaded from artifact, seeded with rounding/sum/unit-conversion.
 6. Implement Family 2 parameter-side checks: param_provenance,
    param_mutation, param_discard, param_taint, param_staleness,
    entity_consistency. Unit-test each against synthetic Sessions.
 7. Implement Family 2 result-side checks: result_fidelity,
    error_blindness, approval_evidence, minimization.
 8. Implement combinator.py (both modes) + store.py (verdicts, findings,
    conformance_tags) + worker.py (evaluate on session close, idempotent
    re-runs) + /eval API: /eval/findings, /eval/sessions/{id}/verdicts,
    /eval/sessions/{id}/conformance, /eval/run. Applicability gating
    (Hard Rule 16) lands here, in the verdict base types.

Phase B — Family 1 (customer policy) + Family 3 (catalog)
 9. skill-builder: add rule-pack compiler (published skill → rule_pack.yaml,
    with the rule_type lowering table and publish-time rejection). The
    compiler carries each rule's materialized source citation block through
    verbatim; a rule arriving without one is a compile error.
10. Implement family1: temporal, predicate, content engines consuming
    rule_pack.yaml; property-test automata against generated step streams.
11. Define intent_catalog.yaml schema + validation in semantic-layer;
    add generation from existing intent bindings; author LoanPro's catalog
    as a demo ARTIFACT under loanpro-demo/policy/ (not engine code).
12. Implement Family 3 call-level: catalog_membership, entitlement,
    version_conformance, side_effect_class.
13. Implement Family 3 scope-level: field_scope, filter_scope,
    volume_scope.
14. Implement Family 3 session-level: toxic_combination, goal_alignment
    (descriptor match only), workflow_integrity, redundancy.

Phase C — grading, UI, population
15. Build the grading harness: run LoanPro catalogue via the orchestrator,
    evaluate, diff BOTH halves against scenarios.py — findings vs
    expected_findings AND conformance tags vs the declared conformances
    (the `_c(policy, note)` entries); a baseline session graded clean must
    still produce its expected tags. Emit a coverage report next to
    check-coverage.md. THIS IS THE ACCEPTANCE GATE for phases A–B; wire it
    as a make target + CI job.
16. Findings UI: extend the Observability tab (or a Findings tab) over
    /eval/findings — filter by family/check/session/severity; session
    detail shows verdicts alongside the existing trace view, with
    conformance badges per session/step ("§10.2 Individual Approval
    Limits — satisfied", clause text on expand); surface both findings
    and tags in Verdict's SessionDetail too.
17. Implement population checks: outcome_consistency, invocation_drift,
    verdict_trend over the verdicts table; baselines stored as artifact;
    /oob/sessions/population already returns the raw material.

Phase D — inline reuse + Preflight
18. Import family1 + family2(parameter-side) + family3(call/scope) +
    combinator into semantic-mcp-server's govern pipeline behind
    mode=inline; map enforced verdicts onto the existing
    allow/mask/block/approval responses; stamp prefront.rule.satisfied /
    prefront.rule.clause attributes on the decision span (extending the
    existing _annotate_decision) so inline traces are born tagged; verify
    drift-gates-never-bypasses.
19. Preflight generator: LLM (design time) reads published tool schemas +
    intent_catalog and emits candidate adversarial sessions in the
    scenarios.py schema (callers, turns/replay steps, expected_findings);
    schema-validate → human approve → run through the SAME orchestrator +
    grading harness in UAT. Findings labeled capability, not incidence.
20. Docs: update CLAUDE.md service table (:8120), add
    eval-engine/CLAUDE.md (sub-doc), record the two staleness traps
    pattern for the new tables.

Phase E — learned intents (TODO, not started; plan in intent_learning_design.md)
    Closes the policy-less onboarding gap: Families 1 and 3 both need a
    design-time artifact compiled from a business policy document, so a
    customer without one gets Family 2 only. Their traces already carry
    most of an intent catalog. Mining is DESIGN TIME and emits candidates
    only — no new runtime path, output is the same intent_catalog.yaml
    Family 3 already loads.
21. evalengine/behavior/: aggregation only, no synthesis, no LLM.
    GET /eval/behavior/tools      per-tool profile (roles, channels, params,
                                  fields, side_effect, row-count dist, support)
    GET /eval/behavior/sequences  frequent ordered pairs/n-grams per session
    GET /eval/behavior/invariants args that always equal a caller attribute
    Every response carries the Family 2 verdict overlay for its supporting
    sessions — frequency is not legitimacy, and Family 2 is the only honest
    signal available in a policy-less deployment (see the design doc §2).
22. semanticlayer/intent_mining.py: CandidateIntent (mirrors
    CandidateScenario), validate_candidate_intent (real tool names, known
    fields), POST /design/semantic/intents/mine. Structure is deterministic
    counting; an LLM names/describes ONLY, advisory. review_status=pending
    always. Honour the design doc's field-by-field learnability table —
    allowed_roles is emitted as OBSERVED, never as permitted; toxic_with and
    restricted_fields are not learnable positively and must not be invented.
23. Review + approve UI over candidates: support counts, evidence sessions,
    the Family 2 overlay, and the sensitive fields a candidate would bless.
    Approve/edit/reject per candidate (mirror skill-builder's candidate-rule
    flow, do not invent a second one) → build_intent_catalog → publish.
24. Impact preview: before publishing, shadow-run the proposed catalog over
    historical sessions and show "if approved, this would have produced N
    findings on the last 30 days, here they are". eval-engine can already
    re-evaluate history (POST /eval/run?force=true; the version key already
    forces re-evaluation when catalog version changes), so this is mostly
    wiring — and it is what makes approval informed rather than a guess.
    Prioritize over 25.
25. Drift watch: behaviour diverging from an approved catalog proposes an
    amendment. family3/population.py's invocation_drift is already this
    computation; it needs a proposal surface, not new maths.

    Validation gate for the whole phase: LoanPro is a holdout — hide its
    hand-authored intent_catalog.yaml, mine one from its traces, diff per
    field, then re-run the 37-scenario harness against the MINED catalog and
    compare to the 37/37 baseline. Because that corpus is deliberately full
    of violations, it also directly tests the §2 guardrail.
```

---

# 7. Autonomous-build protocol (how to run Claude Code on this)

```text
1. One worktree per phase-step group; the combinator + contract dataclass
   land FIRST (step 8 core types extracted early) and are then frozen —
   parallel sessions never edit them.
2. Session loop: plan → approve plan → implement → pytest for the package
   → grading harness (once step 15 exists) → commit → update sub-CLAUDE.md
   with gotchas.
3. Checks are pure functions (Hard Rule 3) precisely so every one is
   unit-testable from synthetic Sessions without Docker; the full stack is
   only needed for step 15's end-to-end gate.
4. The definition of done for the whole engine: a full catalogue run where
   the findings diff against expected_findings is empty for replay
   scenarios and within declared tolerance for llm-mode scenarios.
```
