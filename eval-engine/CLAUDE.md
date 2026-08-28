# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

eval-engine is one service of the Prefront engine. The parent `../CLAUDE.md`
covers the whole platform; this file is **eval-engine-specific**. The design
doc is `../autonomous_build.md` (the HOW, phased build order) and
`../prefront-check-families.md` (the WHAT, the three check families). This
service implements **Phase A** (steps 1-8: Family 2 + combinator + store) and
**Phase B** (steps 9-14): Family 1 (`family1/temporal.py`, `predicate.py`,
`content.py`, loaded from a published `rule_pack.yaml` via
`EVAL_RULE_PACK_PATH`) and Family 3 (`family3/call.py`, `scope.py`,
`session.py`, loaded from a published `intent_catalog.yaml` via
`EVAL_INTENT_CATALOG_PATH`). Both degrade to zero verdicts when
unconfigured, never an error (Hard Rule 9). The rule-pack COMPILER (step 9,
`skill-builder/skillbuilder/rulepack.py` - `CandidateRule` + `Clause` →
`rule_pack.yaml`, written as a sixth artifact alongside `extracted_rules.yaml`
at publish time) and the intent-catalog SCHEMA/GENERATOR (step 11,
`semantic-layer/semanticlayer/intent_catalog.py`) live in their own
services, not here - eval-engine only ever reads the published YAML.
Population-level Family 3 checks (`outcome_consistency`, `invocation_drift`,
`verdict_trend`) are still Phase C (step 17).

## Hard rule: the engine names no demo

`grep -rin "loanpro\|securebank" evalengine/` must be zero hits, forever -
enforced by `tests/test_domain_independence.py`. This applies to `.py` AND to
the bundled `evalengine/profiles/*.yaml` - a docstring or a YAML comment
naming a demo fails the guard exactly like code would (caught once during
Phase A: a comment in `visibility_profile.default.yaml` referencing
`loanpro-demo/CLAUDE.md`).

## Pipeline

```
ch.session_spans(session_id)          read-only, from the shared `spans` table
  -> reconstruct.reconstruct()        raw span rows -> canonical Session (turns, Step[])
  -> provenance.build()                per-arg Origin: exact|normalized|transform|mutated|none, trust class
  -> family2.evaluate_all()            10 built-in checks -> list[Verdict]
  -> family1.evaluate_all()            temporal/predicate/content over rule_pack.yaml (empty if unconfigured)
  -> family3.evaluate_all()            call/scope/session checks over intent_catalog.yaml (empty if unconfigured)
  -> combinator.combine_oob()          version-stamp + resolve indeterminate reason
  -> store.persist()                    eval_verdicts (all) + eval_conformance_tags (satisfied)
```

`family3/population.py` (outcome_consistency/invocation_drift/verdict_trend)
is a separate, on-demand path - `evaluate.evaluate_population()`, called via
`POST /eval/population`, not part of the per-session pipeline above (there
is no single session these checks are "about").

`evaluate.py` is the ONLY place that wires these together - both `worker.py`
(the poll loop) and `api.py`'s `POST /eval/run` call `evaluate_and_persist`,
never their own copy of the pipeline (Hard Rule 12 needs one code path).

## Binding profile: column vs attr

`trace_binding.yaml` (`binding.py`) is the only place that knows whether a
canonical field lives in a top-level ClickHouse column (`intent_name`,
`user_role`, `channel` - already lifted by oob-ingest's `SpanRow.lift()`) or
inside the `attributes` map (`app.row_count`, `app.columns`,
`app.side_effect`, `app.trust` - not lifted, since they're Family-2-specific
and oob-ingest doesn't know about checks). `reconstruct.py` and every
`family2/*` check ask `binding.field(name, row)` and never touch `row[...]`
or `row["attributes"][...]` directly. If a new subject app's tracer uses
different attribute names, it ships its own `trace_binding.yaml` - no engine
code changes.

## Provenance path convention

`provenance.py` flattens `step.args` with an EMPTY prefix (`flatten(step.args)`
-> paths like `"amount"`, `"filter.status"`) but flattens candidate origins
with an explicit `"arg"` / `"result"` prefix (`flatten(s.args, "arg")`,
`flatten(s.result, "result")` -> paths like `"result.balance"`). A check that
needs to match a `Verdict`'s param path against a later result's path (only
`param_staleness.py` does this today) MUST flatten that result with the same
`"result"` prefix, or the path comparison silently never matches. Caught once
in Phase A (staleness always came back `satisfied` because the comparison
used bare `flatten(r.result)`).

## Applicability = absence, not a status value

A check emits **nothing** for a unit it doesn't consider applicable (Hard
Rule 16) - there is no `not_applicable` status. `param_discard` needs >=2
calls to the same tool to emit anything; `param_staleness` needs the source
tool to have been re-invoked between the source step and the use;
`result_fidelity`/`error_blindness`/`approval_evidence` need a non-empty
answer to judge against. Every `family2/*` module's docstring says what its
applicability gate is - read it before assuming a check "isn't firing" is a
bug rather than "this session never exercised it."

## OOB indeterminate resolution stays in the combinator

A check that cannot tell satisfied from violated (today: only
`approval_evidence`, when no approval-shaped tool call backs a claim) emits
`status="indeterminate"` with `missing_capture` naming the visibility-profile
key it hinges on, and stops there - it never consults `ctx.visibility_profile`
to decide the reason itself. `combinator.combine_oob` does that lookup (Hard
Rule 7): `visibility.captured(missing_capture) == False` -> `visibility_gap`,
otherwise `missing_precondition`. Keep new indeterminate-capable checks to
this same split of responsibility.

## Verdicts vs findings vs conformance tags

One physical table, `eval_verdicts`, holds every verdict regardless of status
(satisfied included - Hard Rule 15, never discarded). `GET /eval/findings`
is a read filtered to `status='violated'`; there is no separate `findings`
table - avoids two write paths that could drift out of sync on the same
source data. `eval_conformance_tags` IS a separate, denormalized table (its
own `policy_document`/`clause_id`/`section`/`page`/`clause_text` columns per
`prefront-check-families.md` §3.5) because Family 1/3 tags need a real policy
citation Family 2 never has (`source` stays empty for every Family 2 tag -
Hard Rule 17).

## Family 1 rule shapes: what the current compiler can and can't lower

skill-builder's flat `CandidateRule` IR (`rule_type` enum:
`approval_threshold | data_access | regional_access | restriction |
mandatory_filter | exception | audit_requirement`) has **no ordering
construct at all** - every rule_type is a fact/condition check or a field
scan. `skillbuilder/rulepack.py`'s lowering table therefore only ever emits
`engine: predicate` (approval_threshold/regional_access/restriction/
mandatory_filter) or `engine: content` (data_access); `exception` and
`audit_requirement` are REJECTED at compile time (recorded in the pack's
`rejected` list, not silently dropped - Hard Rule 10). `family1/temporal.py`
is real, working machinery (verified against synthetic sessions in
`tests/test_family1.py`), just with no current producer - it's there for a
future rule source that expresses genuine ordering, not dead code.

A `predicate` rule with `approver_roles` set is approval-gate shaped: when
its conditions fire, it looks for an approval-shaped tool call the same way
`family2.approval_evidence` does, and emits `indeterminate` +
`missing_capture="approval_events"` rather than guessing `violated` when none
is found - same combinator-resolves-the-reason contract as Family 2. A
`predicate` rule with no `approver_roles` is prohibition shaped: firing
conditions ARE the violation, `status="violated"` directly.

Verified live (Phase B smoke test): a hand-written `rule_pack.yaml` with one
`content` rule (`field_names: [ssn, tax_id, credit_score, ...]`) run against
the real ClickHouse volume from prior LoanPro runs correctly found SSN/
tax_id/credit_score surfacing on `get_applicant_profile` - the exact
leakage class `loanpro-demo/CLAUDE.md` documents that app as containing.

## Family 3: catalog structure and what's actually checkable

`intent_catalog.yaml` (`family3/catalog.py`) keys entries by `intent` - the
canonical field the trace binding resolves (`intent_name` column, LoanPro's
`app.intent`), NOT the tool name (`family3/catalog.py`'s `IntentEntry` carries
both; `tool_name` is informational only, checks match on `intent`). An empty
`intent` on a step (the binding found none) is exactly "off-catalog" -
`catalog_membership` needs no special case for it, a catalog lookup miss
already covers it.

`version_conformance` needs `entry.params` (the declared arg-key schema) to
mean something even when it's an empty list - a tool that genuinely takes no
params still has ANY arg flag as drift. There is no separate "not declared"
state (an absent `params:` key parses to the same empty tuple as a declared
empty one) - every on-catalog call always gets a version_conformance verdict.
Same reasoning for `entitlement`: `not entry.allowed_roles` means "no
restriction declared", which is itself a legitimate (trivially satisfied)
answer, not a reason to skip the check.

`filter_scope` only ever recognizes ONE mandatory_filter shape:
`"<field> = caller"` (exact regex match in `scope.py`). Anything richer
("X = caller OR Y = <subject>", prose with a placeholder) is deliberately
left unparsed rather than guessed at - never invent a partial match. In
practice this means filter_scope stays silent on most hand-authored
mandatory_filters; that's the intended, honest failure mode, not a bug.
**Gotcha (caught live, Phase B smoke test):** a tool's `output_value` is
usually `{"columns": [...], "rows": [...], "row_count": N}`, not a bare
row dict or a bare list - `scope.py:_result_rows` unwraps that shape.
`field_scope` never had this bug (it flattens `step.result` recursively via
`provenance.flatten`, which walks through the `rows` list on its own), but
any NEW family3 check that reads `step.result` directly must go through
`_result_rows` (or `flatten`), never assume a shape.

Verified live (Phase B smoke test): the real `loanpro-demo/policy/intent_catalog.yaml`
run against the same ClickHouse volume found a genuine role-escalation case
(a Loan Officer's session invoking `view_risk_profile`, an
Underwriter/Branch-Manager-only intent - `entitlement`), an abandoned closing
obligation (`decide_loan` with no following `send_notice` - `workflow_integrity`),
an unsanctioned combination (`view_applicant` + `export_directory` in one
session - `toxic_combination`), and catalog drift (`decide_loan`'s result
carries `decided_by`/`version`, fields the hand-authored catalog didn't
declare - `field_scope`; a real gap in the catalog transcription, left as-is
since "the catalog under-declares" is exactly the kind of thing this check
exists to surface for a human to reconcile, not for the engine to paper over).

## Idempotent replay

`eval_evaluated_sessions` (session_id, version_key) is the dedup gate:
`version_key = f"{engine_version}:{binding.version}:{visibility.version}:{rule_pack.source_skill}@{rule_pack.source_skill_version}:catalog@{catalog.version}"`.
Republishing a skill (a new `source_skill_version`) makes every
already-evaluated session eligible for re-evaluation under the new rule pack
automatically - no manual cache bust. The intent catalog has no such
auto-bump: bump its `version:` field by hand when you edit it (same
convention as the binding/visibility profiles - none of these are content
hashes). The worker skips any `(session_id, version_key)` pair it's already recorded;
`POST /eval/run?force=true` bypasses the gate for a manual re-check. Bumping
`config.ENGINE_VERSION` (or either bundled profile's `version:` field) is
what makes a prior run re-evaluate - the version key is the only thing that
distinguishes "already checked" from "artifacts changed since."

## Testing

`eval-engine/tests/` is pure-Python, no Docker required (Hard Rule 3 exists
partly to make this possible):

```bash
cd eval-engine
python3 -m venv .venv && VIRTUAL_ENV=.venv .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
```

`tests/helpers.py` builds synthetic `Session`/`Step`/`Turn` objects directly
(bypassing `reconstruct.py`) for check unit tests; `tests/test_reconstruct.py`
is the one test that exercises the full raw-span-dict -> `Session` path, using
row shapes that mirror what `ch.session_spans()` actually returns (JSON
strings in `input_value`/`output_value`, a flat `attributes` dict) - keep new
reconstruct tests in that shape, not in the already-parsed `Step`/`Turn`
shape `helpers.py` uses.

Verifying the service itself (no pytest for this - same as oob-ingest, no
Docker test harness in this repo):

```bash
docker compose up -d clickhouse eval-engine
curl :8120/eval/status                              # worker + profile versions + table totals
curl -X POST ':8120/eval/run?session_id=<id>'        # evaluate one session on demand
curl ':8120/eval/findings?limit=10'
curl ':8120/eval/sessions/<id>/verdicts'
curl ':8120/eval/sessions/<id>/conformance'
```

## What's still missing (see `../autonomous_build.md`)

Phase B (steps 9-14) is DONE: `family1/` (temporal/predicate/content), the
skill-builder rule-pack compiler, `family3/` (call/scope/session checks),
LoanPro's hand-authored `policy/rule_pack.yaml` + `policy/intent_catalog.yaml`,
and the `intent_catalog.yaml` schema/generator (semantic-layer). Step 15's
grading harness (`loanpro-demo/grading_harness.py`) is BUILT and its own
diff/grading logic is unit-tested (`loanpro-demo/test_grading_harness.py`,
no network) - but it has **not yet been run live end-to-end** against the
real stack (needs `docker compose up --build` + a metered LLM key for the
`mode: "llm"` scenarios, so it wasn't run unprompted). Running it is the next
concrete step: `make grade-loanpro` from the repo root, or see
`loanpro-demo/README.md`'s "The grading harness" section. Expect the first
live run to surface real gaps to fix (engine bugs, citation mismatches,
scenarios needing a rule/catalog tweak) - that IS what an acceptance gate is
for; do not assume Phase B is fully validated until it has actually run
clean, or with documented, understood deltas.

Step 17 (population checks) is also DONE - `family3/population.py`
(`outcome_consistency`, `invocation_drift`, `verdict_trend`), invoked
on-demand via `POST /eval/population` (`scenario_id`/`variant` for the first
two, `rule_id` for the third), not the per-session worker loop - there is no
single session a population finding is "about", so its `Verdict.session_id`
is a synthetic key (`population:<scenario_id>[:variant]`,
`population:<scenario_id>:<a>-vs-<b>`, or `population:rule:<rule_id>`) that
`GET /eval/sessions/<key>/verdicts` still works against, same as any real
session. `grading_harness.py` drives this for POP-01/02/03 (`POP_VARIANT` /
`POP_DRIFT` / `POP_RULE_TREND` maps in that file - demo-specific knowledge
that belongs in the fixture-side harness, never in eval-engine).

Step 16 (Findings UI) is also DONE: `prefront-app`'s Observability tab
gained a "Findings" view over `/eval/findings` (family/check filters,
click-through to `SessionDetail`), and `SessionDetail` (both the main app's
and Verdict's copy) now shows real `/eval/sessions/<id>/verdicts` +
`/eval/sessions/<id>/conformance` chips alongside the existing static
"checks this scenario is built to trigger" ones - the former is what the
harness EXPECTS, the latter is what the engine ACTUALLY found. nginx (both
`nginx.conf` and `verdict-nginx.conf`) gained a `/eval/` proxy block
mirroring the existing `/oob/` one; `ui`/`verdict` compose services now
depend on `eval-engine`. Typechecked clean via the documented WSL docker-tsc
workaround; not yet exercised against a running eval-engine in a browser
(same "not run live" caveat as the grading harness).

Still open: Phase D inline reuse (step 18, see below). Step 19 (Preflight)'s
schema + structural validator + prompt +
LLM-call plumbing are DONE in `semantic-layer/semanticlayer/preflight.py`
(`CandidateScenario`, `validate_candidate_scenario`, `render_prompt`,
`generate_candidate_scenarios` - the LLM client is dependency-injected, so
this was smoke-tested with a stub completion function, never a real API
call) - not yet wired to an HTTP endpoint, and never run against a real LLM
in this repo. `KNOWN_CHECKS` there is the one place outside eval-engine that
has to know the check-families vocabulary by name; keep it in sync by hand
if a check id ever changes.

## Phase D / step 18 (inline reuse): a real blocker, not yet attempted

Investigated (not wired in): `semantic-mcp-server/semanticmcp/server.py`'s
`_call_governed` is a **stateless per-call gateway** - it has `args` + a
precheck row + caller facts for ONE call, never a session history.
Most of Family 2's "parameter-side" checks (`param_provenance`,
`param_mutation`, `param_taint`, `param_staleness`, `param_discard`,
`entity_consistency`) are inherently cross-step: `provenance.build()` only
finds an origin by looking at EARLIER steps in `Session.steps`. Wire these
in against a synthetic one-`Step` `Session` and every single governed call
fails `param_provenance` - there is never an earlier step to trace an origin
to. That is not a wiring bug to fix, it is a missing prerequisite: the
runtime has no session-state accumulation across calls at all. Building that
(a session-scoped store in semantic-mcp-server that grows a `Session` object
call by call, keyed by the caller's session id) is real, scoped work, not
part of this pass - **do not** wire Family 2 in against a single-call
`Session` without it; that would turn every governed call into a false
positive, in a live authorization path.

What IS safely single-call (no session-state prerequisite), for whenever
this is picked up: `family3/call.py` (`catalog_membership`, `entitlement`,
`version_conformance`, `side_effect_class` - args + caller only) and
`family1/content.py` (`field_restriction` - needs only the post-execution
result, available at `server.py`'s write/mcp/read branches). `family1/
predicate.py`'s prohibition/approval_gate rules are ALSO single-call-safe in
principle, but reusing them here would run in parallel with - not replace -
the native `governance/rules.py`/`decide.py` pipeline skill-builder's
published `policy.yaml` already drives; the two rule representations
(`policy.yaml` for native inline enforcement, `rule_pack.yaml` for
eval-engine's OOB shadow evaluation) are currently separate lowerings of the
same approved rules, and unifying them is a bigger design decision than
"call combine_inline somewhere," out of scope for a single pass.

Two more reconciliation points, confirmed but not resolved: the native
engine's `decide.aggregate()` precedence is `block > approval_required >
allow` **with no `flag` concept at all** - `combine_inline`'s `flag` effect
would need an explicit policy (most natural: `flag` never changes
`decision.status`, i.e. treated as `allowed`, but IS still recorded on the
span - never silently dropped). And `_annotate_decision` (server.py, the
`tracing.set_attributes(span, {...})` call keyed on `prefront.rules.fired`/
`.indeterminate`) is the right pattern to extend with
`prefront.rule.satisfied`/`prefront.rule.clause`, once there's something
real to annotate.
