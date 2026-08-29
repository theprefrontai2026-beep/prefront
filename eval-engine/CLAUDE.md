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

**Latency from session end to finding** is the sum of the OOB pipeline's
polls: Phoenix export batch (~1-5 s) + oob-ingest's `PHOENIX_POLL_SECONDS`
(5) + the worker's `EVAL_POLL_SECONDS` (10) + `EVAL_QUIET_SECONDS` - the
session must have had no new span for that long before it's a candidate
(the debounce against evaluating a half-ingested trace, see "Two staleness
traps" below). The quiet window defaults to **10 s** (was 30 s; lowered per
request once the debounce had proven itself - findings now land ~20-30 s
after a session ends instead of ~45-60 s). The grading harness's readiness
polling (`GET /oob/sessions/<id>` once a second until the session exists)
is what the 404s in oob-ingest's log during a run are - expected, not an
error.

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

## Family display names (Policy / Integrity / Conformance)

`contract.FAMILY_LABELS` + `family_label()` map the stored family to a
human-readable name for any surface that shows a verdict to a person:

| stored | label | what it is |
|---|---|---|
| `family1` | **Policy** | the customer's own extracted, approved rules |
| `family2` | **Integrity** | built-in invariants (provenance, taint, fidelity, …) |
| `family3` | **Conformance** | behaviour vs the approved intent catalog |

Two rules this mapping must keep:

1. **The stored `family` value never changes.** It is a persisted column in
   `eval_verdicts`; renaming it would orphan every existing row under the
   ReplacingMergeTree rather than replacing it - the same trap documented for
   `check_id` renames in "Two staleness traps" below. The label is derived at
   READ time, in `ch.rows()` (the single funnel every verdict/tag read goes
   through), so all of `/eval/findings`, `/eval/verdicts`,
   `/eval/sessions/{id}/verdicts` and `/eval/conformance` carry
   `family_label` without each endpoint remembering to add it. A row with no
   `family` column is left alone (`eval_conformance_tags` genuinely has no
   such column - it is denormalized around `check_id` + policy citation), and
   an unknown family passes through unchanged rather than rendering blank.
2. **The label is a CATEGORY noun, never an outcome word.** "Policy", not
   "Policy Violations": the same label rides on `satisfied` verdicts (a clean
   session shows `Policy · satisfied`, `Integrity · satisfied`) and an outcome
   word would contradict the status beside it. Verified live on a baseline
   session: 10 Policy / 28 Integrity / 24 Conformance, all satisfied.

Population checks (`outcome_consistency`, `invocation_drift`,
`verdict_trend`) are stored as `family3` and therefore label as
**Conformance** - deliberately, so one label maps to one stored value; their
`check_id` is what distinguishes them. NB this is a different vocabulary from
the demo's SCENARIO groups (`F1/F2/F3/POP/BASE`, `loanpro-demo/scenarios.py`'s
`FAMILIES`, rendered by Verdict's `SessionRunner`) - do not conflate the two.

## Verdicts vs findings vs conformance tags

One physical table, `eval_verdicts`, holds every verdict regardless of status
(satisfied included - Hard Rule 15, never discarded). `GET /eval/findings`
is a read filtered to `status='violated'`; there is no separate `findings`
table - avoids two write paths that could drift out of sync on the same
source data. `eval_conformance_tags` IS a separate, denormalized table (its
own `policy_document`/`clause_id`/`section`/`page`/`clause_text` columns per
`prefront-check-families.md` §3.5) because Family 1/3 tags need a real policy
citation Family 2 never has (`source` stays empty for every Family 2 tag -
Hard Rule 17). Read two ways: per session (`GET /eval/sessions/{id}/conformance`,
what `SessionDetail` shows) and cross-session newest-first
(`GET /eval/conformance?limit=&offset=`, `ch.list_conformance`, same shape/cap
as `/eval/findings`) - added for the UI's Overview page, which shows the
newest policy-cited tags as positive evidence without fanning out one call
per session. Callers wanting only cited tags filter on `section`/`clause_text`
client-side; the newest rows are usually Family 2 (no citation).

**`Finding.event_id`**: a monotonic serial number (decimal string, e.g.
`"42"`) assigned per Finding at PERSIST time - `ch.py`'s `insert_verdicts`
(`_next_event_ids`), never by `combine_oob` or the check that emitted the
underlying `Verdict` (checks and the combinator stay pure, Hard Rule 3 -
assigning a serial number needs a live counter against the store, which only
the storage layer has; `combine_oob` leaves `event_id=""` on purpose). It is
NOT part of `eval_verdicts`' dedup identity (`ORDER BY (session_id, check_id,
rule_id, evidence_excerpt)`) - two persisted rows for the "same" logical
finding still collapse to one via ReplacingMergeTree, and `event_id` just
names whichever row won, not a lifetime identity for the finding concept
itself. Exists so a caller (the UI's Findings flyout/table, an API consumer)
has one opaque stable field to key/reference/deep-link a specific finding by,
instead of composing one from several columns. `ConformanceTag` deliberately
does NOT get one - the user's request was specifically about findings, and
extending it to conformance tags is a separate decision nobody has asked
for yet.

Originally a `uuid4` (assigned by `combine_oob`); switched to a serial
number per explicit request ("can we use a serial number instead of a
uuid"). **Not a ClickHouse-native auto-increment** - this ClickHouse version
(24.8) predates `generateSerialID()`, and a MergeTree has no sequence type -
so it's a process-local `threading.Lock`-protected counter
(`ch._event_seq`/`_next_event_ids`), seeded lazily from `SELECT
max(toUInt64OrZero(event_id))` on first use (an old uuid4-shaped or empty
`event_id` parses to 0 via `toUInt64OrZero`, so it never collides with or
influences the new sequence - counting just starts at 1). Safe against this
service's actual concurrency profile: eval-engine is a SINGLE uvicorn
process (no `--workers`), with exactly two callers that can race each
other - the background `Worker`'s asyncio task and a manual `POST
/eval/run`, both landing in `insert_verdicts` via `anyio`'s threadpool - so
a plain lock is sufficient; there is no second process or pod to coordinate
a real sequence with. The ClickHouse column self-heals via `ADD COLUMN IF
NOT EXISTS` (`ch.py`'s `_ADDED_VERDICT_COLUMNS`, same convention as
oob-ingest's `_ADDED_COLUMNS`) - a row persisted before this shipped reads
back as `event_id=""`, and one persisted during the brief uuid4 window
keeps its uuid4 forever unless the session is re-evaluated - both self-heal
forward, never an error either way.

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

## Two staleness traps in the new ClickHouse tables (step 20)

Root `CLAUDE.md`'s "Engine mechanics that bite" section already documents the
artifacts-volume seed-once trap for the PRE-EXISTING tables; these two are
specific to eval-engine's OWN new tables (`eval_verdicts`,
`eval_conformance_tags`, `eval_evaluated_sessions`) and were both found live,
not by inspection:

1. **A ReplacingMergeTree only dedupes within its OWN sort key - changing a
   `check_id` orphans the old rows instead of replacing them.**
   `eval_verdicts`/`eval_conformance_tags` are `ORDER BY (session_id, check_id,
   rule_id, evidence_excerpt)` (`ch.py`); a rule's `check_id` is part of that
   key by design (Family 1 rules pick their own via `Rule.check_id()`'s
   override). Changing one - `R-INTERNAL-RISK-SCORE` went `prohibition` ->
   `field_restriction` mid-session during step 15's live run (see that
   section) - means the OLD `check_id`'s rows are never touched by a
   re-evaluation under the new one; they linger forever as stale "violated"
   findings unless something explicitly clears them. There is no
   auto-detection for this (a check_id rename looks identical to a check_id
   being added, from the table's point of view) - the convention is to bump
   the rule pack's `source_skill_version` (forces every session back through
   `POST /eval/run?force=true` per "Idempotent replay" above) AND use
   `DELETE /eval/verdicts` (dev-only truncate) for a clean slate when a
   check_id itself changes, not just when a rule's condition changes.
2. **The version-key dedup gate cannot tell "evaluated to zero findings" from
   "not evaluated yet" unless the write path guards for it explicitly.**
   `eval_evaluated_sessions`'s whole purpose (idempotent replay, above) is to
   make a `(session_id, version_key)` pair permanently skip re-evaluation -
   which is exactly wrong for a session that was evaluated a moment too
   early, against a still-partial span read, and legitimately found nothing
   only because the data wasn't all there yet. Caught live in step 15's run:
   `evaluate_and_persist` used to call `mark_evaluated` unconditionally,
   which raced oob-ingest's own separate ClickHouse write and permanently
   stranded a session at "0 findings, done" the moment that race lost. Fixed
   by refusing to mark a session evaluated when `session_spans()` reads back
   empty (`evaluate.py`) - a narrower version of the SAME trap resurfaced one
   layer up, in the grading harness's OWN readiness check, later in this
   document (`wait_for_ingestion`'s span-count debounce, under step 15) -
   "spans exist" and "ALL of this session's spans have arrived" are two
   different conditions, and conflating them is the recurring shape of this
   whole trap.

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

## Status: what's done and what's open (see `../autonomous_build.md` §6)

**Phases A-D (steps 1-20) are DONE** — Family 2 built in, Family 1 + Family 3
wired over their published artifacts, population checks, the grading harness
(37/37 on the full catalogue), the Findings UI, Preflight, and inline reuse in
`semantic-mcp-server`.

**Phase E (steps 21-25) — learned intents — is TODO, not started.** Plan in
`../intent_learning_design.md`. It closes the policy-less onboarding gap: both
artifact-backed families need a document the customer may not have, leaving
them with Family 2 alone, while their traces already carry most of an intent
catalog. Mining is design-time and emits candidates only; the output is the
same `intent_catalog.yaml` Family 3 already loads, so there is no new runtime
path and this service needs no new check.

Two constraints from that plan bear directly on code here, if you pick it up:

- **Frequency is not legitimacy.** Mining learns what the agent DID, not what
  it should do. Family 2 is the only family that needs no policy and runs over
  exactly the corpus being mined, so it is the honesty check: a candidate whose
  supporting sessions carry integrity violations must be surfaced as contested,
  never as clean observed practice.
- **Most of `IntentEntry` is not learnable.** `expected_rows_p99` and the
  `<field> = caller` mandatory_filter shape are genuine wins; `allowed_roles`
  is observed-not-permitted; `toxic_with` and `restricted_fields` cannot be
  learned positively at all, since frequency learns what co-occurs as normal.
  The design doc has the field-by-field table — do not widen it by guessing.

## Live-run status and the bugs those runs found

`loanpro-demo/grading_harness.py` (`make grade-loanpro`) has run the **full
37-scenario catalogue** against the live stack: **37/37 PASS**, report at
`loanpro-demo/docs/eval-coverage.md`. Steps 15-19 are done; Phase D / step 18
(inline reuse in `semantic-mcp-server`) is summarized below.

**Full narrative — every live run, each bug's discovery story, the worked
Preflight example, and the inline-reuse verification — is in
`docs/build-log.md`.** What follows is the lookup table: if a check is
misbehaving, read this first.

### Check bugs found by live runs (symptom → cause → rule)

| # | check | symptom | cause / rule now in force |
|---|---|---|---|
| 1 | `param_staleness` | never fired on the demo's own staleness case | only a later re-READ counted as a refresh; the app rolls writes back so a re-read always shows the pre-write value. An intervening WRITE's args now count too |
| 2 | `result_fidelity` | 7 spurious violations on a clean answer | markdown ordered-list markers (`1. `) parsed as numeric claims; a leading `N. ` is stripped before scanning |
| 3 | content detectors | permanent false positive | a detector scoped to `result` is wrong for a field the app may fetch but never repeat; the three restriction rules scope to `final_answer` only. `result` scope is for a field that must never be fetched at all |
| 4 | `evaluate_and_persist` | session stuck at "0 findings, done" | it marked a session evaluated even when `session_spans()` read back empty, racing oob-ingest. Never mark evaluated on an empty read |
| 5 | `param_provenance` | flagged the agent's own judgments | an untraceable NON-numeric value is a categorical judgment, not a fabrication → `indeterminate`; untraceable NUMERIC stays `violated` (`is_numeric_like`) |
| 6 | `_find_transform` | a fabrication masked as a "mutation" | the near-miss window was a hardcoded 50%, so any two same-magnitude ids "explained" each other. Now `max(rel_tol * 40, 0.05)` |
| 7 | `_candidates_before` | a distorted amount could never be a near-miss | only the FIRST number in a user message was a candidate origin; every numeric token is now its own `user_number` candidate |
| 8 | `approval_gate`, `approval_evidence` | could never be `violated` | the bundled profile declared `approval_events: false` for a subject whose approvals ARE tool calls. Profile v2 says true; both checks read it, falling back to `indeterminate` only when approvals genuinely aren't captured |
| 9 | `entity_consistency` | "looked up A, decided B's loan" invisible | it compared id slots across ARGS only; now also reads a subject from a SINGLE-ROW result (multi-row listings ignored) |
| 10 | `param_discard` | needed two calls to the same tool | added a single-call shape: a user-named value absent from the args whose result rows mix values. Gated to calls that carried ≥1 arg — a parameterless identity-scoped call has no filter to drop |
| 11 | `ch.session_shapes` | population checks always "consistent" | it filtered every span by `scenario_id`, which only the session ROOT span carries, so shapes came back empty. Resolves session ids first. `invocation_drift` also gained a call-volume term |
| 12 | `content._field_in_text` | never matched prose | `re.escape` leaves `_` unescaped, so `credit_score` never matched "credit score". Field is split on `[\s_-]` and rejoined with `[\s_-]?` |

Two non-engine causes that repeatedly *look* like check bugs, and cost real
debugging time before being identified:

- **A partially-ingested trace.** "Spans exist" and "all of this session's
  spans have arrived" are different conditions; conflating them makes a check
  look broken when it merely ran early. `wait_for_ingestion` debounces on a
  span count that is non-zero AND unchanged across two polls.
- **Something clearing the stores mid-run.** A browser tab left open on the
  Observability tab can fire "Clear all trace data" (`DELETE /oob/phoenix` +
  `/oob/spans` + `/eval/verdicts`), wiping sessions during a grading run and
  producing "never showed up in OOB ingestion" failures that are not
  reproducible. Check oob-ingest's log for `phoenix purge` before believing a
  scenario regressed.

### Fixture bugs are not engine bugs

Several `intent_catalog.yaml` entries under-declared `fields` relative to what
their tools actually return. `field_scope` was correctly flagging a REAL gap
between catalog and reality — "the catalog under-declares" is exactly what
that check exists to surface for a human, not something the engine should
paper over. Expect the same class of gap on other intents as more scenarios
run.

## Step 19: Preflight (LLM-proposed test scenarios)

`semantic-layer/semanticlayer/preflight.py` has an LLM propose candidate
adversarial scenarios in `loanpro-demo/scenarios.py`'s shape from a tool list
+ intent catalog (`POST /design/semantic/preflight/generate`). Always
`review_status="pending"`, structurally validated against real tool and check
names, never auto-approved.

`loanpro-demo/preflight_import.py` closes the loop: `approve <id...>` is the
human gate that converts only the named candidates into `scenarios.py`'s shape
and appends them to `policy/preflight_approved.json`, which `get_scenarios()`
merges alongside the static catalogue — so an approved candidate grades
exactly like a hand-authored one.

- **`KNOWN_CHECKS` in `preflight.py` is the one place outside eval-engine that
  hard-codes the check-families vocabulary by name. Keep it in sync by hand if
  a check id ever changes.**
- **Structural validity is not behavioural correctness.** A candidate can pass
  schema validation, name real tools and a known check, and still assert
  something false about the catalog — which is exactly what running it through
  the real harness catches. `docs/build-log.md` has the worked example of both
  a correct candidate (PASS) and a wrong one (FAIL, then rejected).

## Phase D / step 18: inline reuse in `semantic-mcp-server`

eval-engine's single-call-safe checks also run inline on the governed path.
What runs where, and why the exclusions are what they are:

| runs inline | when | over |
|---|---|---|
| `family3.call` (`catalog_membership`, `entitlement`, `version_conformance`, `side_effect_class`) | pre-execution | `intent_catalog.yaml` |
| `family1.content` (`field_restriction`) | post-execution, unioned into `masked_fields` | `rule_pack.yaml` |
| five of Family 2's six parameter-side checks (`param_mutation`, `param_discard`, `param_taint`, `param_staleness`, `entity_consistency`) | pre-execution | built in, over `governance/session_state.py`'s per-connection history |

- **`param_provenance` is excluded, unconditionally.** This gateway has no
  turn/user-message concept — only tool args and results — and the check
  treats "no candidate origin at all" as `violated`/`block`. Inline, EVERY
  first call's bare user-supplied argument would be flagged as fabricated and
  blocked forever. Caught by an existing test the moment it was wired in.
- **Family 2's four result-side checks** (`result_fidelity`,
  `error_blindness`, `approval_evidence`, `minimization`) need a final
  answer, which a single tool call never produces. Out of scope by nature.
- **`family1/predicate.py`'s prohibition/approval-gate rules are deliberately
  NOT wired in**, though single-call-safe in principle: they would run in
  parallel with the native `governance/rules.py` pipeline that `policy.yaml`
  already drives. The two rule representations stay separate lowerings of the
  same approved rules; unifying them is a larger design decision.
- **Session identity is per-CONNECTION, not per-caller.** `session_state`
  mirrors `identity.py`'s `act_as_var` pattern with a deliberately *different*
  contextvar — two connections authenticating as the same caller must never
  share tool-call history. A blocked call is never appended to history.
- The native engine has **no `flag` concept** (`decide.aggregate()` is
  `block > approval_required > allow`), so `inline_checks` maps a `flag` down
  to `allow` before returning; the verdict is still recorded in
  `inline_checks_trace` for the span.
- All artifact-backed inputs default to unconfigured (Hard Rule 9); Family 2
  is built in and always runs.

**Vendoring**: `contract.py`, `provenance.py`, `combinator.py`,
`visibility.py`, `family1/`, `family2/`, `family3/` are copied into
`semantic-mcp-server/semanticmcp/evalengine/` by `eval-engine/sync.sh` (that
service has its own Docker build context and cannot import this one). Run
`sh eval-engine/sync.sh` after editing any vendored file; `--check` reports
drift and is wired into CI.
