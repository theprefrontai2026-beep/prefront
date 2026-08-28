# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

eval-engine is one service of the Prefront engine. The parent `../CLAUDE.md`
covers the whole platform; this file is **eval-engine-specific**. The design
doc is `../autonomous_build.md` (the HOW, phased build order) and
`../prefront-check-families.md` (the WHAT, the three check families). This
service implements **Phase A only** (autonomous_build.md steps 1-8): Family 2
(built-in integrity invariants), the combinator, and the verdict/finding
store. Family 1 (`family1/`) and Family 3 (`family3/`) are stub packages
pending Phase B.

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
  -> family2.evaluate_all()            10 pure checks -> list[Verdict]
  -> combinator.combine_oob()          version-stamp + resolve indeterminate reason
  -> store.persist()                    eval_verdicts (all) + eval_conformance_tags (satisfied)
```

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

## Idempotent replay

`eval_evaluated_sessions` (session_id, version_key) is the dedup gate:
`version_key = f"{engine_version}:{binding.version}:{visibility.version}"`
(rule_pack/catalog versions join this once Phase B lands). The worker skips
any `(session_id, version_key)` pair it's already recorded; `POST
/eval/run?force=true` bypasses the gate for a manual re-check. Bumping
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

Phase B adds `family1/` (temporal/predicate/content engines over a published
`rule_pack.yaml`) and `family3/` (call/scope/session checks over an
`intent_catalog.yaml`), plus the skill-builder rule-pack compiler. Phase C is
the LoanPro grading harness (diff findings + conformance tags against
`expected_findings`) and a Findings UI. Phase D reuses `family1` +
`family2`(parameter-side) + `family3`(call/scope) + `combinator.combine_inline`
inside semantic-mcp-server's govern pipeline, and adds the Preflight
generator. None of that exists yet - `combine_inline` is implemented and
unit-tested but has no caller yet.
