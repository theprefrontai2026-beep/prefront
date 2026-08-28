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
and the `intent_catalog.yaml` schema/generator (semantic-layer).

## Step 15 (grading harness): run live, real bugs found and fixed

`loanpro-demo/grading_harness.py` has now actually run against the live
stack (8 scenarios: 4 baselines + F2-01/F2-05/F3-03/F3-11) - **7/8 PASS**,
one documented known gap (below), report at `loanpro-demo/docs/eval-coverage.md`.
Full 30-scenario catalogue run is still future work (this pass was
deliberately scoped: mostly `mode: "replay"` scenarios plus a couple of
`llm` ones, to keep the metered LLM cost small) - `make grade-loanpro` runs
everything once someone's ready to spend that.

Four real bugs the live run found and fixed, in order of discovery:

1. **`param_staleness` couldn't detect the exact staleness pattern the demo
   is built to test.** It only looked for a later RE-READ disagreeing with
   the origin value. LoanPro's app rolls every write back (seed data stays
   stable across runs - see `loanpro-demo/README.md`), so a re-read of the
   same tool always shows the pre-write value, forever - the re-read signal
   could never fire even though the agent unambiguously issued a change.
   Fixed: `family2/param_staleness.py` now ALSO treats an intervening WRITE
   step's own args (not just a later read) as a refresh signal.
2. **`result_fidelity` treated markdown ordered-list markers as numeric
   claims.** `"1. **Loan ID 7001**..."` extracted `1` as a "claim," which
   naturally matches no tool result - 7 spurious violations on one clean
   baseline answer. Fixed: strip a leading `N. ` at the start of a line
   before scanning (`family2/result_fidelity.py`).
3. **A content-engine detector scoped to `result` is a permanent false
   positive against an app that always returns the field raw.** LoanPro is
   "policy-blind" by design (see its own CLAUDE.md) - `get_risk_profile`
   always includes `internal_risk_score` in its raw SQL result, and the
   policy explicitly sanctions that for internal pricing use; only the
   FINAL ANSWER leaking it is the actual violation. `policy/rule_pack.yaml`'s
   three content-engine rules (`R-INTERNAL-RISK-SCORE`,
   `R-SSN-TAXID-BANK-RESTRICTION`, `R-CREDIT-SCORE-OFFICER-RESTRICTION`) now
   scope to `final_answer` only - `result` scope is right for a field the
   app should never fetch AT ALL, wrong for one it's allowed to use
   internally but never repeat back.
4. **`evaluate_and_persist` marked a session "evaluated" even when it read
   zero spans** - a real race: `wait_for_ingestion` (harness, via oob-ingest)
   and eval-engine's own `store.session_spans()` read (a separate
   ClickHouse query) aren't the same check, so eval-engine could run a
   moment before its own read caught up, get nothing, and then NEVER retry
   (the version-key gate treated "evaluated to zero findings" and "not
   ready yet" as the same state). Fixed: `evaluate_and_persist` no longer
   calls `mark_evaluated` when `session_spans()` comes back empty; the
   harness also retries `POST /eval/run` a few times on that specific
   "skipped: no spans ingested yet" response.

Plus fixture-only fixes (not engine bugs): several `intent_catalog.yaml`
entries under-declared `fields` relative to what their tools actually
return (`decide_loan`, `view_application`, `request_approval`, `send_notice`,
`view_risk_profile`, `quote_terms`) - `field_scope` was correctly flagging a
REAL gap between the catalog and reality, not a false positive; fixed by
completing the field lists (the catalog is deliberately not exhaustively
re-audited against the DB schema for every entry this pass - expect the
same class of gap on other intents once more scenarios run).

**Known, documented, NOT fixed this pass**: `param_provenance` flags
agent-synthesized categorical decisions (`decide_loan`'s `decision:
"approved"`, `send_decision_notice`'s `kind: "approval_letter"`,
`request_manager_approval`'s free-text `reason`) as "fabricated," since
their value never appears verbatim in a user message or prior tool result -
true of ANY classification the agent is supposed to produce as its own
judgment, not just literally-invented facts. `prefront-check-families.md`'s
own examples for this check ("agent invented an account ID, amount, rate")
are all traceable identifiers/quantities, not categorical choices - the
check's current form doesn't distinguish the two. This is why `BASE-02`
(the clean approval-with-notice baseline) still shows 1/8 FAIL: three
`param_provenance` false positives, nothing else. Fixing this properly
needs a real design decision (how does a generic engine recognize "this
field's value SET is small/enumerated, so absence-of-a-verbatim-source
isn't fabrication" without hardcoding field names?) - deliberately not
guessed at under time pressure in this pass.

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

Step 19 (Preflight) is DONE and live-verified end to end:
`semantic-layer/semanticlayer/preflight.py` (`CandidateScenario`,
`validate_candidate_scenario`, `render_prompt`, `generate_candidate_scenarios`)
is wired to `POST /design/semantic/preflight/generate` (`api.py`:
`PreflightBody` -> real `McpTool`/`IntentCatalog` objects -> a real
`LLMClient()`, never a stub in the endpoint path). Verified live against a
REAL LLM (gpt-4o-mini, one call, three of LoanPro's tools + a trimmed
intent_catalog.yaml): the first prompt draft got a real response back but
0/5 candidates survived validation (the model's JSON didn't match
`CandidateScenario`'s shape - `validate_candidate_scenario` correctly
rejected every one rather than coercing them); adding a full worked-example
JSON object to the system prompt fixed it - the re-run got 4/4 valid,
schema-conformant candidates (unauthorized credit-report access,
unauthorized loan decision, a restricted-field probe, an entity-consistency
probe), all `review_status="pending"`. The endpoint's own request-validation
path (bad tool shape -> 400, no tools -> 400, never a 500 or a stray LLM
call) is covered by a `TestClient`-based check that deliberately never
reaches the LLM. `KNOWN_CHECKS` in `preflight.py` is the one place outside
eval-engine that has to know the check-families vocabulary by name; keep it
in sync by hand if a check id ever changes.

## Phase D / step 18 (inline reuse): the safe subset is DONE and live-verified

`semantic-mcp-server/semanticmcp/server.py`'s `_call_governed` is a
**stateless per-call gateway** - it has `args` + a precheck row + caller
facts for ONE call, never a session history. Most of Family 2's
"parameter-side" checks (`param_provenance`, `param_mutation`, `param_taint`,
`param_staleness`, `param_discard`, `entity_consistency`) are inherently
cross-step: `provenance.build()` only finds an origin by looking at EARLIER
steps in `Session.steps`. Wiring THOSE in against a synthetic one-`Step`
`Session` would make every single governed call fail `param_provenance` -
there is never an earlier step to trace an origin to. That is not a wiring
bug to fix, it is a missing prerequisite (session-state accumulation across
calls) this pass does NOT build - **Family 2 stays un-wired here** for that
reason; do not add it without that prerequisite.

What WAS wired in, because it's genuinely single-call-safe (needs no session
history): `semantic-mcp-server/semanticmcp/governance/inline_checks.py` runs
`family3.call` (`catalog_membership`, `entitlement`, `version_conformance`,
`side_effect_class`) PRE-execution - folded into `decision.status` right
after `decision = ctx.decision`, before the `if decision.status != "allowed"`
gate, so a block/approval_required from either source wins (native rules are
never downgraded) - and `family1.content` (`field_restriction`) POST-execution,
in all three result-producing branches (guarded-read precheck, `mcp`, plain
read), unioned into the existing `masked_fields` set rather than trying to
retroactively block a read that already ran. Both artifacts default to
unconfigured (`PREFRONT_RULE_PACK_PATH` / `PREFRONT_INTENT_CATALOG_PATH`
empty -> every call is a no-op here, Hard Rule 9); LoanPro's `loanpro-mcp`
compose service (unused by default, behind the `mcp` profile) points them at
`loanpro-demo/policy/` via a direct bind mount - the same staleness-trap-free
pattern eval-engine uses, not the artifacts named volume's seed-once copy.

`family1/predicate.py`'s prohibition/approval_gate rules are deliberately
**NOT** wired in here even though they're single-call-safe in principle:
doing so would run in parallel with, not replace, the native
`governance/rules.py`/`decide.py` pipeline skill-builder's published
`policy.yaml` already drives. The two rule representations (`policy.yaml`
for native inline enforcement, `rule_pack.yaml` for eval-engine's OOB shadow
evaluation) stay separate lowerings of the same approved rules; unifying
them is a bigger design decision than this pass makes.

Reconciliation points resolved: the native engine's `decide.aggregate()`
precedence is `block > approval_required > allow` with **no `flag` concept**
- `inline_checks.py`'s `evaluate_pre_execution`/`evaluate_post_execution`
map a `combine_inline` `flag` down to `"allow"` before returning, so it never
touches `decision.status` but the verdict (status=violated, effect=flag) is
still recorded in `inline_checks_trace` for the span. `_annotate_decision`
(server.py) now sets `prefront.rule.satisfied` / `.violated` / `.clause`
from `result["governance"]["inline_checks"]`, following the exact same
`tracing.set_attributes(span, {...})` pattern as the pre-existing
`prefront.rules.fired`/`.indeterminate` attributes.

**Vendoring**: `contract.py`, `provenance.py`, `combinator.py`, `visibility.py`,
`family1/`, `family3/` are copied into `semantic-mcp-server/semanticmcp/evalengine/`
by `eval-engine/sync.sh` (same pattern as `tracing/sync.sh` - this service has
its own Docker build context and cannot import eval-engine directly).
`family2/`, `config.py`, `binding.py`, `reconstruct.py`, `ch.py`, `store.py`,
`api.py`, `worker.py`, `evaluate.py`, `profiles/` are NOT vendored (either
fastapi/clickhouse-connect-heavy, or Family 2, which isn't used here at all).
Run `sh eval-engine/sync.sh` after editing any vendored file;
`sh eval-engine/sync.sh --check` reports drift (not wired into CI - no CI
exists in this repo yet).

**A real bug this caught in review, before it ever ran live**: the first cut
passed `govern_kind` (the NATIVE engine's own "read vs mcp_write" masking
concept - "read" for every SQL precheck-write) as `side_effect_class`'s
signal, so it could never observe a real write and the check could never
fire. Fixed by deriving the true side effect from `kind`/`write_action`/
`mcp_destructive` directly (`inline_side_effect` in `_call_governed`) -
verified live: `decide_loan` now reports `side_effect (write) matches
approval (write)` instead of always `(read)`. Also live-verified: an
Applicant blocked from `view_applicant` **before the DB was ever queried**
(entitlement), a Loan Officer blocked from `decide_loan` by BOTH the native
`decide_loan_role_restricted` rule and the new `entitlement` check
independently (real defense-in-depth, not redundant code), and
`R-SSN-TAXID-BANK-RESTRICTION` (`rule_pack.yaml`) catching `ssn` in a real
`view_applicant` query result and getting masked in the actual response
alongside the native rule's `credit_score` mask.

`semantic-mcp-server/tests/` (new - this service had none before):
`test_inline_checks.py` (pure, `governance/inline_checks.py` in isolation,
temp-file fixtures, no DB) and `test_inline_checks_wiring.py` (mocks
`resolve_caller`/`govern`/`db.run_select` and calls the real `_call_governed`,
so it's exercising server.py's actual control flow, not just the module -
this is what caught the `govern_kind` bug once written).
`requirements-dev.txt` adds `pytest`/`pytest-asyncio`; `pytest.ini` sets
`asyncio_mode = auto`.
