# Prefront Evaluation Engine — Check Families

The engine core evaluates every agent action against three families of checks. All three consume the same canonical trace and value-provenance graph, and all emit the same verdict contract into a single combinator. Families 2 and 3 require no policy onboarding — they deliver findings from day one of trace ingestion.

---

## Family 1 — Learnt Rules (customer policy, extracted & approved)

**Source:** business policy documents + agent system prompt + tool schemas.
**Lifecycle:** LLM-assisted extraction at design time → human review → published as a versioned rule pack. No LLM at runtime; every rule compiles to a deterministic engine or is rejected at publish time.

| Type | Example | Evaluates as |
|---|---|---|
| `precondition` | "Verify KYC status before quoting a limit increase" | Before tool T fires, fact F must already be established in the session |
| `sequencing` | "Always fetch risk profile before discount check" | Tool-ordering constraint within a session |
| `prohibition` | "Never reveal internal risk scores" | Condition over tool args or outputs |
| `field_restriction` | "tax_id, bank_account_hint must never appear in responses" | Sensitivity scan over tool results + final answer |
| `approval_gate` | "Amounts over X require manager approval" | Condition that maps to approval-required |
| `substitution` | "A Loan Officer sees the tier band, not the raw score" | The positive half of a restriction: a declared substitute must appear when the restricted field was retrieved |

**Engine mapping:** precondition + sequencing → temporal engine (per-rule automata over the session's step stream). Fact conditions (prohibition on args, approval_gate) → predicate engine (expression eval over the fact bag). Output prohibitions + field_restriction → content engine (detector sets over payloads).

---

## Family 2 — Integrity Invariants (universal, built-in)

**Source:** ships with the engine. Not customer-specific, not extracted, not configurable away. These check whether the agent handles *values* honestly.

**Mechanism:** a value provenance graph built during fact reconstruction. Every value in every tool call is resolved against candidate origins — user messages, prior tool results, config, system-prompt constants — via deterministic matching (exact → normalized → whitelisted transform → none), and tagged with a trust class: `trusted` (config, verified tool result), `semi` (user input), `untrusted` (retrieved content). All checks below are queries over this graph.

### Parameter-side (values flowing into tool calls)

| Check | Finding | Meaning |
|---|---|---|
| `param_provenance` | Fabricated parameter | Arg value has no legitimate origin — agent invented an account ID, amount, rate |
| `param_mutation` | Distorted parameter | Value has an origin but was altered en route (rounding, unit/currency change, truncation, sign flip) beyond the whitelisted-transform tolerance |
| `param_discard` | Dropped constraint | Constraint supplied upstream never reached the call (user said "savings only" → agent queried all accounts) — over-broad retrieval |
| `param_taint` | Tainted parameter | Value originates from untrusted content (retrieved doc, email body, web page) and flows into a privileged param — prompt injection actually executing |
| `param_staleness` | Stale parameter | Value sourced from a step later superseded in-session (used the pre-refresh balance) |
| `entity_consistency` | Entity confusion | Call's subject ≠ session's subject (customer A's session, customer B's tax_id in args) |

### Result-side (values flowing out to the user)

| Check | Finding | Meaning |
|---|---|---|
| `result_fidelity` | Fabricated answer | Claim in the final answer traces to no tool result; numeric claims must match results within declared tolerance |
| `error_blindness` | Ignored failure | Tool returned error/empty; agent proceeded as if success |
| `approval_evidence` | Phantom approval | Agent claims or implies an approval that has no corresponding event in the trace |
| `minimization` | Over-retrieval | Fetched far more than the intent needed — columns, rows, repeated calls |

**Fuzziness containment:** legitimately derived values are handled by a small whitelist of transforms (rounding within tolerance, sum, unit conversion). Anything beyond it flags as `param_mutation` with the near-miss shown, so a human whitelists a new transform rather than the engine guessing.

---

## Family 3 — Intent Conformance (actual behavior vs approved catalog)

**Source:** the generated-and-approved intent catalog. Each approved intent is a behavioral contract: allowed callers/channels, allowed fields, mandatory filters, expected volume, side-effect class, trigger descriptors, closing obligations.

### Call level — is this approved work at all?

| Check | Finding | Meaning |
|---|---|---|
| `catalog_membership` | Off-catalog action | Call binds to no approved intent — work nobody sanctioned |
| `entitlement` | Unentitled invocation | Intent exists, but not for this caller, channel, or agent |
| `version_conformance` | Schema drift | Call shape doesn't match the published intent version — unknown params, changed types; the approval no longer describes reality |
| `side_effect_class` | Effect escalation | Intent approved read-only; call performed a write |

### Scope level — approved work, in approved shape?

| Check | Finding | Meaning |
|---|---|---|
| `field_scope` | Scope creep | Columns fetched exceed columns approved |
| `filter_scope` | Unscoped retrieval | Mandatory predicate absent (missing customer_id=X → full-table read) |
| `volume_scope` | Bulk deviation | Rows returned far exceed the intent's declared magnitude |

This level operationalizes data-governance minimization: not a principle, a diff against a signed envelope.

### Session level — approved intents, in an approved combination?

| Check | Finding | Meaning |
|---|---|---|
| `toxic_combination` | Unsanctioned aggregation | Individually-allowed intents composed into an unapproved unit (contact + salary + export in one session) |
| `goal_alignment` | Task drift | Intents invoked bear no relation to the session's stated request. Fuzziness moved to design time: intents carry approved trigger descriptors; runtime is a match against those; "no descriptor matched" emits a low-severity signal, not a verdict |
| `workflow_integrity` | Abandoned obligation | Intent's declared closing obligation (log disclosure, send confirmation) never occurred |
| `redundancy` | Retry storm | Same intent, same args, repeated N times — agent confusion |

### Population level — is the agent predictable? (OOB-only; needs many sessions)

| Check | Finding | Meaning |
|---|---|---|
| `outcome_consistency` | Nondeterminism score | Same intent + equivalent fact pattern → different action shapes over time; variance per (intent, discretized fact bucket). Unpredictability made quantitative — re-measurable after every prompt or model change |
| `invocation_drift` | Behavioral change | Intent frequency/mix shifts vs baseline after a new model version or prompt — change detection per deploy |
| `verdict_trend` | Persistent violation | Violation rate per rule per intent, trending — evidence that prompt edits are not fixing an issue |

---

## Common contract

Every check, whatever its family or engine, emits the same verdict:

```
{ rule_id | check_id, effect, status: satisfied | violated | indeterminate, evidence }
```

The **combinator** is the only component that knows precedence (block > approval > allow) and the only place deployment mode changes behavior:

- **Inline:** verdict is enforced; indeterminate fail-safes to approval-required (drift can gate, never bypass).
- **Out of band:** verdict is a shadow; divergence against what the agent actually did produces the finding; indeterminate splits into *missing precondition* (agent never fetched the fact) vs *visibility gap* (log never captured it), via the session's visibility profile.

## Known gaps

### Substitution obligations: every content check is prohibitive

Every check in Family 1's content engine and Family 3's scope level answers
"did something forbidden appear?" **None can assert that something required
appeared in its place.** The families have no positive content obligation.

The worked example, from LoanPro: policy restricts the raw bureau credit
score to Underwriters and Branch Managers (§12.2), while stating that Loan
Officers see the **tier band** instead (§6.4). `field_restriction` correctly
catches the raw score leaking to a Loan Officer. But:

- if the agent returns **neither** the score nor the tier — refusing, or
  quietly omitting the answer — no check fires, even though the policy
  expects the tier to be supplied; and
- if the agent returns the tier **correctly**, that is recorded only as the
  absence of a violation, never as positive evidence that the substitution
  happened.

So the policy's actual shape — "not X, but Y in its place" — is only half
enforceable. This is distinct from `closing_obligation`/`workflow_integrity`,
which is a positive obligation about a **subsequent tool call**, not about
answer content.

**Status: BUILT** (step 26). Shipped as the `substitution` check —
`required_substitute` on a content rule. When the restricted field is present
in a tool RESULT (so the agent held the data and had to choose what to
surface) and the caller is in `restricted_from_roles`, it asserts that a
declared substitute token appears in that turn's answer. Conditioning on the
field having actually been retrieved is what keeps it from firing on every
session that never asked the question.

The substitute is **declared, never derived**: a rule lists the substitute
field's name and/or the literal values it can take, because an answer usually
names the value without naming the field ("She's Near-prime"). Deriving
"712 → Near-prime" would mean the engine reading §6.4's band table — a far
larger change than checking a declared token is present.

Effect is `flag`, not the rule's own (usually `block`): withholding a
restricted value while supplying nothing in its place is an incomplete
answer, not a disclosure breach. The breach, if any, is separately reported
by `field_restriction` on the same rule — the two are independent findings,
and a session can legitimately raise both.

**OOB only, by nature**: it needs a final answer, which a single governed MCP
call never produces. `family1/__init__.evaluate_all` calls it; the inline
path calls `content.evaluate` directly and so never reaches it.

---

**Division of labor, in one line each:**

- Family 1 — "your agent broke *your* rules."
- Family 2 — "your agent can't be trusted with values — it invented, distorted, or smuggled them."
- Family 3 — "your agent did work, at a shape, in combinations, or with a variance that nobody approved."
