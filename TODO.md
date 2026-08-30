# TODO

Open work that is **not** already carried by a design doc's own status marker.

This file is an **index, not a second plan**. Where a phased plan already
exists (`autonomous_build.md`, `intent_learning_design.md`,
`prefront-check-families.md` § "Known gaps"), the entry here is a pointer and
the detail stays there — duplicating it is how the two drift apart. Anything
recorded only in a conversation belongs here in full, because there is nowhere
else for it to live.

Every entry states the evidence (file:line, or a measured number) so it can be
re-checked rather than re-argued. **Delete an entry when it's done** — a stale
todo is worse than a missing one. Closing one usually also means flipping a
status marker in the doc it points at.

---

## 1. Phase E — learned intents (steps 21–25)

**Pointer only.** Plan in `intent_learning_design.md`; steps and the validation
gate in `autonomous_build.md` (§6, "Phase E — learned intents (TODO, not
started)"); service-side notes in `eval-engine/CLAUDE.md` § Status.

Closes the policy-less onboarding gap: Families 1 and 3 both need an artifact
compiled from a business policy document, so a customer without one gets
Family 2 alone — while their traces already carry most of an intent catalog.

**Done when** the LoanPro holdout gate passes: hide its hand-authored
`intent_catalog.yaml`, mine one from its traces, diff per field, re-run the
39-scenario harness against the MINED catalog and compare to the baseline.

---

## 2. LoanPro's declared fields disagree with what its tools return

Two separate problems, same area. Both are **fixture** work — no engine change.

### 2a. Two intents under-declare against their own SQL

| intent | tool's actual `RETURNING` | declared `fields` | missing |
|---|---|---|---|
| `apply_discount` | `loan_id, apr, version` | `loan_id, apr` | `version` |
| `amend_application` | `loan_id, requested_amount, term_months, product, version, updated_at` | `loan_id, version` | 4 |

`apply_discount` is the live one: it is the **only** `field_scope` violation in
the store (event 38029). `amend_application` is latent — no graded session has
exercised it successfully yet.

**Done when** both files below declare what the SQL actually returns, and a
full harness run shows no `field_scope` finding. Note the fix must land in
**both** `app_tools.INTENTS` and `policy/intent_catalog.yaml` — see 2b.

### 2b. `INTENTS` and `intent_catalog.yaml` have silently diverged

`policy/intent_catalog.yaml` is documented as hand-transcribed from
`app_tools.py`'s `INTENTS`, but **six intents' `fields` now disagree**, and in
every case the catalog is the more complete one — it was reconciled against
reality as `field_scope` findings surfaced, and `INTENTS` was left behind:

```
get_application           catalog adds  apr, assigned_officer, decided_by
get_risk_profile          catalog adds  internal_risk_score, model_version
quote_terms               catalog adds  applicant_id, tier
request_manager_approval  catalog adds  approver_role, created_at
decide_loan               catalog adds  decided_by, version
send_decision_notice      catalog adds  channel, sent_at
```

This matters beyond tidiness: `docs/gen_coverage.py` reads `INTENTS`, so
`docs/check-coverage.md` — the check → session → evidence contract — documents
the **stale** field list. Nothing detects the divergence.

**Done when** the two agree and something enforces it (the natural home is
`gen_coverage.py`, which already exits non-zero on an unresolved policy `§`).

---

## 3. Nothing validates `rule_pack.yaml`'s policy citations

`docs/gen_coverage.py` builds a policy index and exits non-zero when a cited
section has no heading in `loan_underwriting_policy.md` — but it reads only
`INTENTS` and `scenarios.py`. It **never opens `policy/rule_pack.yaml`**, so a
citation that drifted there would pass every gate in the repo silently.

All nine rules' citations were checked by hand and currently resolve, including
the four that cite two or three sections in one `A / B` string. That check was
ad hoc and is not repeatable.

**Done when** `gen_coverage.py` folds the rule pack into its policy index and
fails on an unresolved section there too.

---

## 4. `near_miss_limit` is calibrated on one demo

`eval-engine/evalengine/provenance.py:218`

```python
near_miss_limit = max(rel_tol * 40, 0.05)
```

Domain-neutral in form, but the `* 40` was tuned against LoanPro's magnitudes —
12.9% off reads as a mutation, ~30% as an unrelated identifier. One dataset has
ever voted on it. Nothing is known to be broken; no test can catch a tuned
number, and only a second demo exercising `param_mutation` would prove it out.

**Done when** a second subject app exercises the provenance checks and the
constant is either confirmed or replaced with something derived.

---

## 5. The domain-noun guard covers `evalengine/` only

`eval-engine/tests/test_domain_independence.py` guard 2 scans executable tokens
for a demo's business vocabulary. Its scope is deliberately one package.

Extending it needs exemptions **designed first**, not a wider `rglob`:
`semanticlayer/mapper.py`, `semanticlayer/api.py`, `semanticlayer/preflight.py`
and `skillbuilder/llm.py` carry domain nouns *on purpose*, as few-shot examples
inside LLM prompt templates, and `skillbuilder/domain_packs/*.yaml` names a demo
by design (its line 1 says so). Library API names collide too (`branch_labels`
in an alembic migration, `begin_transaction` in SQLAlchemy).

**Done when** the other engine packages are covered with a justified exemption
list, or a deliberate decision is recorded that they stay out of scope.

---

## 6. Stale `demo='loanpro'` rows in the decision store

Measured in `api-db` (`prefront_audit`):

```
decision_trace   16
decision_agent    5
decision_intent   5
decision_policy   0   (cleared)
decision_stat     0   (cleared)
```

Genuine LoanPro governed-MCP decisions from 2026-07-21, when `loanpro-mcp` was
still driven. They are not wrong, just stale: `loanpro-mcp` is behind a compose
profile and unused, so nothing will ever add to or refresh them, while the
Dashboard's cumulative counters (`decision_agent`, `decision_intent`) are never
pruned and never reset by "Clear".

**Done when** they're either cleared (as `decision_policy`/`decision_stat`
already were) or a decision is recorded to keep them as historical record.
