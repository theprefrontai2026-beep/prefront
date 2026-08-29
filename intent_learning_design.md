# Learned intents: mining an intent catalog from observed behaviour

> **Status: PLANNED, not built.** Tracked as Phase E (steps 21-25) in
> `autonomous_build.md` §6. Nothing described here exists in the codebase yet.

Companion to `autonomous_build.md` (phased build order) and
`prefront-check-families.md` (what the checks are). This document plans the
**policy-less onboarding path**: what to do for a customer who has no business
policy document to compile.

## 1. The gap

Prefront's two artifact-backed check families both need a design-time document:

| family | artifact | produced by | needs |
|---|---|---|---|
| Family 1 (Policy) | `rule_pack.yaml` | `skill-builder/skillbuilder/rulepack.py` | a policy document |
| Family 3 (Conformance) | `intent_catalog.yaml` | `semantic-layer/semanticlayer/intent_catalog.py` | an approved intent catalog |
| Family 2 (Integrity) | — | built in | **nothing** |

Both degrade to zero verdicts when unconfigured (Hard Rule 9). So a customer
with no policy document today gets Family 2 only: real, useful, universal —
but blind to *their* rules, because nobody has told us what they are.

The observation behind this plan: **their traces already contain most of an
intent catalog.** Every session in ClickHouse carries the tool called, the
caller's role and channel, the arguments, the returned columns and row counts,
the side effect, and the order it all happened in. That is most of
`IntentEntry`'s field list, sitting in a table we already read.

## 2. The hard constraint: frequency is not legitimacy

This is the assumption that makes or breaks the whole idea, so it goes first.

Mining observed behaviour learns **what the agent did**, never **what it should
have done**. If an agent has been leaking SSNs for six months, a naive miner
learns "SSN access is normal for this role" and mints an intent that *blesses*
the leak. We would have encoded normalization of deviance as policy, and worse,
laundered it through a governance product so it looks approved.

Three structural defences, in order of importance:

1. **Family 2 is the ground truth that keeps the learner honest.** It is the
   one family that needs no policy, and it runs over exactly the corpus we
   would mine. So before proposing any candidate, cross-reference the sessions
   that support it against their Family 2 verdicts. A candidate whose
   supporting sessions carry `param_taint`, `param_provenance`,
   `entity_consistency` or `minimization` violations is **not** presented as
   clean observed practice; it is presented as *contested*, with the
   violations attached. This is the single most valuable idea in this design:
   the integrity checks that work without policy are what make policy
   learnable without a policy.
2. **Never auto-approve.** Same rule the whole repo already runs on
   (`review_status="pending"`, human gate, schema validation). A mined
   candidate is a hypothesis, exactly like `preflight.py`'s candidate
   scenarios — and step 19 already proved a structurally-valid candidate can
   be behaviourally wrong (PF-04 passed validation and was still false).
3. **Present the evidence, not just the conclusion.** Every candidate ships
   with support counts, the time window, example session ids, the observed
   field list, and the sensitive-looking columns it would bless. The reviewer
   approves a *narrowing*, not a rubber stamp.

A fourth, weaker defence: a baseline period during which mining is descriptive
only, so a reviewer sees the catalog stabilize before anything is published.

## 3. What can actually be learned, field by field

`IntentEntry` (eval-engine's `family3/catalog.py`) is the target shape. Being
honest about which fields are inferable is the core of the design:

| field | learnable? | how |
|---|---|---|
| `tool_name` | **yes, deterministic** | observed directly |
| `params` | **yes, deterministic** | union of observed arg keys |
| `side_effect` | **yes, deterministic** | `app.side_effect` attribute |
| `fields` | **yes, deterministic** | union of observed result columns |
| `trust` | **yes, deterministic** | `app.trust` attribute |
| `expected_rows_p99` | **yes, statistical** | p99 of observed `row_count` — a genuinely free win: it turns on volume/minimization checking with zero policy input |
| `mandatory_filters` | **yes, as a hypothesis** | "this arg equalled the caller id in 100% of N sessions". Note the convergence: `scope.py`'s `filter_scope` only recognizes the exact shape `<field> = caller`, and that is precisely the shape this test produces |
| `closing_obligation` | **yes, as a hypothesis** | sequence mining: B follows A within the session in X% of cases |
| `allowed_roles` | **observed ≠ allowed** | the normalization-of-deviance hotspot. Emit as *observed callers with support counts*; the human must narrow. Never present the observed set as the permitted set |
| `allowed_channels` | same caveat | same |
| `intent` (the name) | **no — language, not counting** | LLM-drafted from tool name + observed usage; advisory only |
| `trigger_descriptors` | **no — language** | LLM-drafted, advisory |
| `restricted_fields` | **no, not positively** | can only be *suggested* from sensitivity heuristics + Family 2 signals; human decides |
| `toxic_with` | **no, not positively** | frequency learns what co-occurs = *normal*, the exact opposite of toxic. Only rarity/anomaly can hint |
| `policy` | **n/a** | there is no policy document by definition |

Two consequences worth stating plainly:

- **A learned catalog produces findings with no policy citation.** That is
  already legal (Hard Rule 17 — Family 2 tags carry no `source` either), and
  the Findings UI already renders a finding whose `source` has no quotable
  text. But a reviewer should know a learned finding cites *observed
  practice*, not a clause, and the UI should say so rather than leaving an
  empty citation block that reads like a bug.
- **The unlearnable half is the prohibitive half.** You cannot learn "must
  never" from observation, because absence of evidence is not evidence of
  prohibition. Learned catalogs therefore cover *conformance to normal
  practice* well and *prohibition* not at all. That is a real ceiling, not a
  gap to be engineered away, and it should be communicated as such — a learned
  catalog complements a policy document, it does not replace one.

## 4. Where the code lives

Respecting existing service charters (and domain independence — the engine
names no customer's tables, roles or thresholds; all vocabulary comes from the
traces):

```
ClickHouse `spans`
  └─ eval-engine  (already owns the read-only ClickHouse reader + reconstruct.py)
       └─ NEW  evalengine/behavior/   aggregation only, no synthesis
            GET /eval/behavior/tools       per-tool profile: roles, channels,
                                           params, fields, side_effect, row-count
                                           distribution, support counts
            GET /eval/behavior/sequences   frequent ordered pairs/n-grams per session
            GET /eval/behavior/invariants  args that always equal a caller attribute
            (every response carries the Family 2 verdict overlay for its
             supporting sessions — defence #1 above)
  └─ semantic-layer  (already owns IntentCatalog schema, generator, and
                      preflight.py's candidate/validate/approve pattern)
       └─ NEW  semanticlayer/intent_mining.py
            CandidateIntent (pydantic, mirrors CandidateScenario)
            validate_candidate_intent()   structural: real tool names, known fields
            POST /design/semantic/intents/mine     (mirrors /preflight/generate)
       └─ human approval gate → build_intent_catalog() → dump_intent_catalog()
            → artifacts volume → Family 3 consumes it EXACTLY as today
```

The deliberate property: **no new runtime path.** Mining is design-time; the
output is the same `intent_catalog.yaml` the runtime and eval-engine already
load. This keeps the repo's founding thesis intact — LLMs at design time only,
runtime deterministic — and means the entire existing Family 3 test surface
covers a learned catalog for free.

Why aggregation in eval-engine and synthesis in semantic-layer: eval-engine
already has the only ClickHouse reader and `reconstruct.py`; semantic-layer
already owns the catalog schema and the candidate→approve pattern. Splitting
this way adds no second ClickHouse client and no second candidate framework.
Aggregates cross the boundary, raw spans never do.

## 5. Phases

Each phase is independently useful and independently shippable.

- **L1 — Behavioural aggregates (no LLM).** `evalengine/behavior/` + the three
  read endpoints, with the Family 2 overlay. Deterministic, reproducible,
  auditable — counting, not guessing. Unit-testable with the existing pure
  synthetic-`Session` helpers.
- **L2 — Candidate synthesis.** Deterministic structure (everything in the
  "yes" rows of §3) + an LLM used *only* for naming and description. This
  split matters: structure is counting and must be reproducible; naming is
  language and stays advisory. `review_status="pending"` always.
- **L3 — Review and approve.** UI over candidates showing support, evidence
  sessions, the Family 2 overlay, and the sensitive fields being blessed.
  Approve / edit / reject per candidate — mirroring skill-builder's existing
  candidate-rule approval rather than inventing a second flow. Publishes
  through the existing `build_intent_catalog` path.
- **L4 — Impact preview.** Before publishing, shadow-run the proposed catalog
  against historical sessions and show *"if approved, this would have produced
  N findings on the last 30 days of traffic, here they are."* eval-engine can
  already re-evaluate history (`POST /eval/run?force=true`, and the version key
  already forces re-evaluation when the catalog version changes), so this is
  mostly wiring. This is the phase that makes approval a genuinely informed
  decision instead of a guess, and I would prioritize it over L5.
- **L5 — Drift watch.** Once a catalog exists, watch for behaviour diverging
  from it and propose amendments. `family3/population.py`'s `invocation_drift`
  is already this computation; it needs a proposal surface, not new maths.

## 6. Validating that the miner actually works

This repo has an unusually good test bed for it, and using it is the difference
between a plausible design and a verified one:

**LoanPro is a holdout.** It has a hand-authored `intent_catalog.yaml` (17
intents) *and* 37 graded scenarios currently at 37/37 PASS. So:

1. Hide the hand-authored catalog. Mine one from the same traces.
2. **Diff mined vs hand-authored, per field** — precision/recall per §3 row.
   This tells us which of the "learnable" claims above survive contact.
3. **Run the 37-scenario harness against the MINED catalog** and compare to the
   37/37 baseline. This is the end-to-end number: how much governance do you
   retain with a learned catalog instead of an authored one? I expect
   conformance checks to hold up and prohibition-shaped expectations to fail —
   which would be the §3 ceiling showing up as a measurement, exactly as
   predicted.
4. **Guardrail test.** The LoanPro corpus is *deliberately* full of violations
   (that is what the catalogue is for). A miner without the Family 2 overlay
   should visibly learn bad behaviour from it; one with the overlay should flag
   those candidates as contested. That makes the corpus a direct test of
   defence #1 — arguably the most important test in the whole plan, since it
   tests the assumption the design rests on rather than the code.

## 7. Open questions for a human

1. **How much traffic before a candidate is credible?** Minimum sessions,
   minimum distinct callers, minimum time span. Needs a real answer, not a
   default — too low and you learn noise, too high and nobody ever onboards.
2. **Whose behaviour counts?** Mining a corpus that includes a compromised or
   misbehaving agent teaches the miner that misbehaviour. Do we mine all
   traffic, or only sessions that are Family-2-clean? (Cleaner, but a much
   smaller corpus, and it presumes Family 2 catches everything.)
3. **Re-mining cadence and catalog churn.** A catalog that changes weekly is
   not a control. Amendments probably need to be batched and versioned
   deliberately.
4. **Does a learned catalog get labelled differently in the UI?** I think yes —
   a finding citing observed practice is epistemically weaker than one citing
   an approved clause, and the Findings surface should not present them
   identically.
