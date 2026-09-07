# Learned intents: mining an intent catalog from observed behaviour

> **Status: L1 and L2 BUILT** (branch `feature/intent-mining`); L3-L5 still
> planned. Tracked as Phase E (steps 21-25) in `autonomous_build.md` §6.
>
> | phase | what | where |
> |---|---|---|
> | L1 | behavioural aggregates, no LLM | `eval-engine/evalengine/behavior/`, `GET /eval/behavior/{tools,sequences,invariants,labels}` |
> | L2 | candidate synthesis + inferred policy | `semantic-layer/semanticlayer/intent_mining.py`, `POST /design/semantic/intents/mine` |
> | L3 | review surface (read-only) | `prefront-app`'s **Learned Intents** tab, `/learned` |
> | L4-L5 | impact preview, drift watch | not built |
>
> L3 is PARTIAL on purpose: candidates are shown with their evidence, but
> approve/publish is not wired, and the page says so rather than offering a
> control that does nothing.
>
> §6's holdout experiment has been RUN; its results are in §6.1 below.

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

A fourth defence, and it turned out not to be the weak one: **a baseline period
during which mining is descriptive only.** Built as `behavior/baseline.py` and
the `learning` / `monitoring` split in `intent_mining.py`.

Learning is not a shorter monitoring phase. Monitoring compares behaviour
against a known-good shape; learning is how that shape is obtained, and until
it exists there is nothing to compare against. "This operation is frequently
performed with no preconditions" is a FINDING only if you already know it ought
to have some — on day one it is simply the shape of the operation. A miner that
editorialises from the first minute manufactures issues out of the absence of a
baseline, and a reviewer shown those learns to distrust the surface before it
has told them anything true.

So during learning the job is only to answer **how are tools being called, in
what pattern** — which is counting, needs no model, and is reproducible. The
same counted facts are framed differently by mode:

| | learning | monitoring |
|---|---|---|
| warning | "81% of the time this operation was performed with nothing preceding it" | …"either no precondition is required, or one is being bypassed routinely" |
| inferred | "appears to require no preconditions in the majority of cases" | "appears to require certain preconditions … but is frequently performed without any" |

**The model does not run during learning at all** — a refusal, enforced in
semantic-layer (409) rather than only in the UI, because the UI is not the only
caller. Asking a model to read a rule out of traffic that is still surprising us
produces a confident statement about a pattern that may not be the pattern, and
that is exactly the output most likely to be believed. Summarising is what you
do once the set has settled and you are naming things to approve.

`learning_progress()` answers the one question the phase CAN answer by
counting: do we know what normal looks like yet? Two measures, neither a
judgement — how many patterns appeared for the first time in the most recent
period, and what share of that period was already explained by patterns learned
from EARLIER periods. Prior-coverage rather than whole-window coverage, because
the latter is circular: the shapes were derived from that traffic. Bucketing is by CLOCK, and getting that wrong was this section's own bug.
The first version split by POSITION over the whole window, so every new episode
re-partitioned all of history and the "most recent" chunk kept re-inheriting
older bursts. Running steady repeated traffic exposed it: five rounds of an
identical scenario mix added no new shapes at all — the distinct-shape count
sat at 49 — while the measure went on reporting 25% novelty and refusing to
settle. A convergence measure that cannot notice convergence is worse than
none. With real time buckets the same corpus reads:

    period 1   18 eps  10 new    0% explained by prior
    period 2  371 eps  33 new   50%
    period 3  272 eps   3 new   98%
    period 4    7 eps   0 new  100%
    period 5   54 eps   2 new   96%
    period 6  230 eps   1 new   99%      -> STABLE (99.6% / 2.8%)

Empty periods are dropped rather than scored as perfectly covered: a deployment
idle overnight has not thereby learned anything, and counting that as 100%
would let idleness declare readiness.

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

**An intent is not always one call, and this table assumed it was.** The
original §3 treats one tool as one intent and reduces sequence to a single
pairwise field. But "underwrite an application" is *fetch the record, pull the
report, score it, decide* — and mining tool-by-tool reports that as four
unrelated operations with nothing saying they belong together. `behavior/
workflows.py` mines the contiguous ordered runs and `intent_mining.
mine_workflows` proposes them as multi-call candidates, with the ORDER carried
into the prompt as evidence: a step that consistently precedes another is a
candidate **precondition**, one that follows is a candidate **obligation** —
both of which Family 3 already enforces. Three filters decide whether the
output is readable at all: consecutive repeats collapse (a retry is not a
step), support counts sessions rather than occurrences, and a run that is only
a fragment of a longer run with the same support is dropped. Without the last
one, every prefix and rotation of every real pattern is its own row.

**And one intent rarely has one shape.** The agent sometimes already held part
of the data and sometimes went further, so "assess an applicant" surfaces as
find→profile, profile→report, and the full four-step run. Reported separately
those are several candidates with near-identical policies, and a reviewer reads
the same operation repeatedly without seeing it is one.
`behavior/workflows.group_workflows` merges runs that share most of their steps
and `intent_mining.mine_intent_groups` summarises each group in ONE model call
— N shapes become one candidate rather than N. The CORE steps (in every
variant) and OPTIONAL ones (in some) are counted, and the prompt is told not to
contradict that split: the core is the backbone a reviewer would turn into a
precondition, so it must not depend on the model's reading.

Linkage matters more than the similarity metric here. Single linkage — join a
group if similar to ANY member — chained the whole corpus into one 31-variant
"intent" spanning applicant lookup, quoting and loan decisions, with no step
common to it. Complete linkage (similar to EVERY member) gives 18 coherent
groups with a non-empty core in each. Over-splitting is the better failure: two
candidates a reviewer merges by eye beats one they must take apart.
| `allowed_roles` | **observed ≠ allowed**, but see cohort contrasts below | the normalization-of-deviance hotspot. Emit as *observed callers with support counts*; the human must narrow. Never present the observed set as the permitted set |
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

### 3.2 The unit a reviewer can approve: the EPISODE

Everything above aggregates across sessions. Nothing asked what a single
session revealed, and a session is not one intent — it is a sequence of them.
To hand a reviewer "this is a governed intent, approve it", something has to
say where one operation ends and the next begins, and an n-gram cannot: it is a
window slid over a stream with no notion of when the stream changed subject.

`behavior/episodes.py` cuts each session into EPISODES — one operation on one
subject — on two structural boundaries: the same identifier taking a different
value, and a side-effecting call closing the operation it was gathering
evidence for. Structural rather than statistical, which is what makes an
episode trustworthy where a frequent substring is not.

**A different identifier is NOT a boundary, and getting that wrong was the
first version's bug.** One operation legitimately walks between related
entities — fetch the record by its own id, then pull a report by the id of the
party it names — and treating that hop as a new subject severed every decision
from the evidence gathered for it. It reported 228 decisions as having no
preconditions, which was an artifact of the cut, not a finding. Each episode
now carries a MAP of identifier → value and only a contradiction ends it.

**Grouping episodes by their CLOSING act is the sharpest input for inferring a
rule that this corpus offers**, because it is a comparison rather than a rate.
Every observed way of reaching one write, side by side:

    decide_loan            241 times
      (nothing preceded it)                                 196x  81%
      find_applicant -> get_applicant_profile ->             15x   6%
      get_credit_report -> get_income_verification ->        15x   6%

An n-gram rate cannot express that. This can, and the model reads it correctly:
"appears to require certain preconditions … but is frequently performed without
any preceding steps", caveated as "may indicate a lack of control rather than a
legitimate absence of requirements". Which of the two it is, the traces cannot
settle and a reviewer can — which is exactly the right division of labour.

`explained_fraction` reports what share of all episodes the candidate shapes
account for (98% of 897 on the bundled corpus). A catalog covering a third of
the traffic leaves most of it ungoverned, and a reviewer should be told that
before approving rather than inferring it from a thin findings feed afterwards.

### 3.1 The signal §3 missed: what differs BETWEEN cohorts

The table above asks what can be learned about one intent from its own usage.
That framing misses where an access policy is actually visible. A policy is
precisely what makes one group of callers behave differently from another, so
the DIFFERENCES carry it — and they appear in no single intent's profile.
`behavior/cohorts.py` computes three, worth very different amounts:

1. **Field gaps** — the same tool returning fewer fields to one cohort than to
   another. The strongest signal available, because it cannot be explained by
   what a cohort happened to need: they called the same operation and got less
   back. On an ungoverned corpus there are none, and that absence is itself
   reported: nothing is being withheld from anyone.
2. **Exclusive operations** — performed by one cohort only.
3. **Absence, scored per tool** — and this is the one that is easy to get
   wrong. A cohort that never called an operation may be barred or may simply
   never have needed it. Which, depends on how often it WOULD have: if other
   cohorts reach a tool in a fraction p of their sessions, silence across n
   sessions happens by chance with probability (1-p)^n.

   A flat session threshold was tried first and was wrong in an instructive
   way. It told the model a 31-session cohort had "ample traffic", and the
   model returned HIGH confidence on a boundary spanning 13 unused tools —
   most of which its own traffic could say nothing about. Per-tool scoring cuts
   that cohort to the 4 tools where silence is genuinely unlikely. The prompt
   is handed the split rather than the raw list, because a long list of
   rarely-used tools is not evidence and its LENGTH is the thing most likely to
   be mistaken for some.

The ceiling from §3 still binds: absence of evidence is not evidence of
prohibition. These are candidate boundaries for a human, and a cohort's
observed reach is never its permitted reach.

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

## 6.1 What the holdout experiment actually measured

Run against the bundled loan-origination demo: 18 tools mined from ~30 days of
traces, scored against its 17-intent hand-authored catalog with
`semantic-layer/score_mined_catalog.py`. Alignment is via the `app.intent`
label — the answer key, used for scoring only, never as a mining input.

| field | precision | recall | reading |
|---|--:|--:|---|
| `side_effect` | **17/17 exact** | — | deterministic, as §3 claimed |
| `fields` | 0.92 | **1.00** | every authored field was observed; the 8 extra are fields the tool really returns that the catalog omits |
| `params` | 0.92 | 0.83 | good; misses are args never exercised in the window |
| `allowed_roles` | **0.76** | **0.66** | the weakest by a distance — exactly where §3 predicted |

Three things worth keeping:

1. **The `allowed_roles` gap is the design's central claim, measured.** Nine
   observed roles are absent from the authored permitted set. That is not miner
   error — it is *observed ≠ allowed*, the normalization-of-deviance hotspot,
   showing up as precision loss. A miner that reported observed roles as
   permitted would have blessed all nine.
2. **The miner found an operation the catalog deliberately omits.**
   `get_internal_metrics` aligned to nothing, because it is one of the demo's
   two off-catalog tools. Unaligned candidates are therefore a *signal* —
   operations running with nobody's approval — not scoring noise.
3. **The contested overlay was non-empty on every high-traffic tool.** This
   corpus is deliberately full of violations, so that is the correct answer, and
   it confirms defence #1 fires on exactly the corpus designed to fool it.

Not yet measured: §6 step 3, running the graded harness against the MINED
catalog to see how much governance survives. That needs L3's approve-and-publish
path, which does not exist yet.

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
