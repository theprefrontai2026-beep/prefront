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

---

## 7. Finding suppression / whitelisting (three scopes)

Not started. Motivation: misfiring cases — but read the triage caution below
before treating any specific case as one.

Three scopes, widest to narrowest:

1. **Family** — suppress an entire family (e.g. all Family 2 / Integrity).
2. **Tool + family** — a family's findings for one tool (e.g. Integrity on
   `get_my_applications`).
3. **Check + tool** within a family — the narrowest (e.g. `param_discard` on
   `get_my_applications`, or `field_scope` on `apply_discount`).

### Design constraints

- **It must be an artifact, not engine code.** Same rule as `rule_pack.yaml` /
  `intent_catalog.yaml`: a whitelist naming a tool is domain vocabulary, so it
  belongs in a published YAML read at design time.
  *Do not rely on the domain-noun guard to enforce this.* Measured against the
  real LoanPro tool names, it catches `get_applicant_profile` and `decide_loan`
  but **passes** `apply_discount`, `get_my_applications`, `quote_terms` and
  `export_applicants` — including both names used as examples above. Hard
  Rule 1 is the binding constraint here; the guard is a partial backstop.
- **Suppress at read time; never drop the verdict.** Hard Rule 15 persists
  verdicts regardless of status. A whitelist that stops the *write* makes the
  engine quietly blind and destroys the audit trail — the thing a governance
  product least wants. Persist the verdict with a `suppressed` marker naming
  the rule that suppressed it, filter in `GET /eval/findings` (already just a
  read filtered to `status='violated'`), and surface a visible suppressed
  **count** rather than a silent zero.
- **The artifact needs a `version`, joined into the version key.**
  `evaluate.py:27` composes
  `{ENGINE_VERSION}:{binding}:{visibility}:{skill}@{ver}:catalog@{ver}` — a
  whitelist version belongs in that string, so editing it forces
  already-evaluated sessions back through evaluation rather than stranding
  them at their old verdicts.
- **The new column follows the self-healing convention.** `ch.py:60`'s
  `_ADDED_VERDICT_COLUMNS`, applied at `ch.py:128` as
  `ALTER TABLE … ADD COLUMN IF NOT EXISTS`, so an older volume self-heals.
- **Validate check ids** against the same vocabulary as `KNOWN_CHECKS`
  (`semantic-layer/semanticlayer/preflight.py:42`). A typo'd id would otherwise
  silently suppress nothing — or, depending on how matching is written, read as
  suppressing everything.
- **Require a `reason`; consider an expiry.** Suppression is the mechanism by
  which this system goes blind over time, so it should be at least as auditable
  as a finding.

### Triage caution — carry this into the design

Not every "misfire" seen so far was one, and the distinction is not cosmetic:

| case | what it actually was | right fix |
|---|---|---|
| `param_discard` on a parameterless call | check bug | fixed in the engine |
| `result_fidelity` on a derived count | check bug | fixed in the engine |
| `field_scope` on `apply_discount` (event 38029) | **correct finding** | fix the fixture (entry 2a) |

That third row is the warning: a whitelist there would have hidden a true
finding about a real catalog under-declaration — one that `amend_application`
shares — instead of fixing it. Make **"check bug, fixture gap, or genuine
finding?"** an explicit triage step that has to be answered before anything can
be whitelisted, and record the answer in the entry's `reason`.

**Done when** a versioned whitelist artifact suppresses at all three scopes at
read time, suppressed findings remain queryable and counted rather than
vanishing, editing the artifact re-evaluates affected sessions, and an unknown
check id is rejected at load rather than silently matching nothing.

---

## 8. Per-application settings: turn individual checks on/off within a family

Not started. Related to entry 7 but a different mechanism — that one waives
*findings* reactively, per tool, with a reason; this one configures *which
checks run at all* for a given subject application. Build them so they share
the check-id vocabulary and the version-key discipline, not so one is a special
case of the other.

### What exists today

Neither half of this is expressible:

- **No per-check granularity, at any level.** `evaluate.py:47-49` runs
  `evaluate_family2` → `evaluate_family1` → `evaluate_family3` unconditionally.
  Families 1 and 3 can only be turned off wholesale, and only by *removing
  their artifact* (Hard Rule 9's degrade-to-zero). **Family 2 cannot be turned
  off at all** — it is built in and always runs. There is no switch for one
  check.
- **No per-application concept in eval-engine.** Configuration is entirely
  env-var driven and single-tenant: one `EVAL_RULE_PACK_PATH`, one
  `EVAL_INTENT_CATALOG_PATH` per deployment (`config.py:32-39`). Nothing in
  `eval_verdicts` identifies an application.

Surface to cover: **27 check ids** in `KNOWN_CHECKS`
(`preflight.py:42`) — 6 Family 1, 10 Family 2, 11 Family 3 — plus the three
population checks, which are a separate on-demand path
(`family3/population.py`) and need their own answer.

### Design questions to settle first

- **What identifies an "application"?** There is no app id anywhere in the
  pipeline today. The nearest existing per-subject-app artifact is
  `trace_binding.yaml` (a new subject app ships its own), so that is the
  natural anchor — but sessions would still need to resolve to one, and
  `eval_verdicts` would need the column.
- **Does "off" mean "don't run", or "run and mark"?** Same tension as entry 7,
  and it may deserve the opposite answer: a deliberate per-app setting is not a
  per-finding waiver, and not running is genuinely cheaper. But then a session
  with no findings is indistinguishable from one that was never checked. The
  engine already has a vocabulary for exactly this distinction —
  `indeterminate` + `missing_capture`, resolved to `visibility_gap` by the
  combinator — so the cheap honest option is to **record the disabled set on
  the session** (version stamp or its own column) so "clean" and "not checked"
  never read the same, even if the checks genuinely don't run.
- **Does it apply inline?** `semantic-mcp-server` re-runs a subset of these
  checks on the governed path (Phase D / step 18). A per-app setting either
  applies there too or explicitly does not; silently differing between OOB and
  inline is the worst of the three.

### Constraints carried from entry 7

- **Artifact, not engine code** — an application id is domain vocabulary
  (Hard Rule 1). And per that entry: do not rely on the domain-noun guard to
  catch a violation here.
- **Version it into the version key** (`evaluate.py:27`), so toggling a check
  re-evaluates affected sessions instead of leaving them stamped with results
  from a different configuration.
- **Validate ids against `KNOWN_CHECKS`** at load; an unknown check id must be
  rejected, never silently ignored.

**Done when** an application can enable/disable any individual check within any
family from a versioned artifact, Family 2 included; a disabled check is
distinguishable from a check that passed; toggling re-evaluates; unknown ids
are rejected at load; and the inline path's behaviour is explicit either way.

---

## 9. Per-user audit logs (scoped to the logged-in user)

Not started. Today the audit log (`api-server` `rule_audit_log`, `/api/audit`)
and the decision-trace store are global — there is no notion of *whose* actions
a given viewer may see. Add per-logged-in-user scoping so a user's audit view
shows their own activity, with the wider view gated by role.

Open questions to settle first: where identity comes from (there is no auth
layer in the UI/api-server today), whether "user" here means the governed
*caller* (the trusted `X-*-Act-As` identity) or the human operating the
console, and how this interacts with the domain-neutrality rule (a user id is
domain vocabulary — keep it in config/artifacts, not engine code).

---

## 10. Ingestion scalability

Not started — think-about item, no design yet. The OOB path
(`oob-ingest` → ClickHouse) and eval-engine's re-evaluation both currently
assume demo-scale volumes: `PhoenixPoller` pages Phoenix REST on a fixed
interval with an in-memory seen-set, eval-engine reconstructs each session and
re-runs three check families over the full `spans` table. Neither has been
exercised at production span rates.

Things to work through: bounded/persisted seen-set vs. in-memory growth, poll
cadence vs. OTLP push under load, batching/backpressure on the ClickHouse
writes, incremental (vs. full-table) re-evaluation, and where the first
bottleneck actually is (measure before optimizing).

---

## 11. Report generation

Not started. A way to produce a shareable report (governance posture, findings
by family/tool/session, conformance over time) from the data already in the
decision-trace store + eval-engine's verdicts — rather than only the live
Dashboard/Findings views. Settle: scope (per-session, per-application,
time-window), format (in-app artifact vs. exportable file), and whether it
reads the existing `/eval/*` and `/api/*` surfaces or needs new aggregate
endpoints.

---

## 12. Configure PII handling when a schema is accepted

Not started. At schema-acceptance time (the Data Connector → Data Graph step,
where a customer confirms an introspected schema) let the customer mark which
columns are PII / sensitive and how they should be governed (mask, block,
scope), instead of that only being expressible later in Policy Studio rules or
the semantic overlay. Capture it as part of the accepted-schema artifact so it
flows into the same `build_bindings → build_query_templates → publish-policy`
tail. Keep the vocabulary (column names, sensitivity tags) in the artifact, not
engine code (Hard Rule 1).

---

## 13. Performance scaling: streaming ingestion (agent → Kafka → evaluator)

Not started — architecture sketch, related to entry 10. Instead of eval-engine
polling Phoenix/ClickHouse and re-reading the full `spans` table, move to a
streaming pipeline: the agent (or the OTLP tap) publishes spans/sessions onto a
**Kafka** topic, and one or more **evaluator instances** consume from it and
evaluate incrementally as sessions complete.

Why it helps scaling: decouples span production from evaluation (backpressure
via the topic rather than dropped/late polls), lets evaluator instances scale
horizontally by partition (e.g. keyed on `session.id` so a session's spans land
on one consumer), and replaces full-table re-evaluation with per-session
consume-and-evaluate.

To work through: partition key and ordering guarantees (a session's spans must
not be split across partitions out of order), where ClickHouse sits (still the
store of record, or downstream of the same topic), how the existing degrade-to-
zero and version-key discipline carry over, and whether this replaces or
augments the current poll/OTLP paths.

---

## 14. User auth, identity, role determination, and correlation to policy/intents

Not started — the foundational identity layer that entry 9 (per-user audit
logs) is blocked on. There is no auth in the UI/api-server today, and the
governed caller identity is trusted-layer only (`X-*-Act-As` / `X-LoanPro-*`
headers set by config, never by the agent). Four connected pieces to figure
out:

- **Auth** — how a human operating the console signs in (there is no auth layer
  at all today).
- **User identification** — who the authenticated principal is, and how it
  relates to the governed *caller* identity vs. the console *operator* (the
  same distinction flagged in entry 9).
- **Role determination** — how a role is derived for that principal. Note the
  runtime already resolves a caller's role from config/SQL (`identity.py`'s
  `IDENTITY_QUERY` aliasing onto the contract names `role`/`region`), so this
  should build on that mechanism rather than inventing a parallel one.
- **Correlation to business policy and intents** — tie the resolved role to the
  rules and intents it is authorized for, so policy (`rule_pack.yaml`) and the
  intent catalog reference the same role vocabulary the auth layer produces.

Constraint: user ids, roles, and tenant names are domain vocabulary — keep them
in config/artifacts, not engine code (Hard Rule 1). Settle this before entries
9 and 11 (per-user reports), both of which assume an identity to scope by.

---

## 15. Aliases across tool args and table columns

Not started. The same real-world concept is often named differently across
tools and tables — e.g. `loan_id` in one tool is `credit_id` in another, or a
column and a tool argument for the same key don't share a name. Today matching
is name-literal end to end: a rule symbol binds to a fact keyed by the literal
column name *or* the request-arg name (see "Engine mechanics that bite" — "A
symbol must resolve at publish AND match a fact at runtime"), so a naming
mismatch either fails to bind at publish or binds but never fires at runtime.
The same brittleness hits `policybind.root_table_by_intent`'s same-named-field
disambiguation and the checks that correlate a param across tools.

Allow a **declared alias map** — synonyms for a concept across tool args and
table columns — so publish-time binding and runtime fact lookup can canonicalize
to one name before matching.

To work through: where the alias artifact lives and whether it's per-datasource
or per-application (it's domain vocabulary — an artifact, not engine code, Hard
Rule 1); whether canonicalization happens at build/publish time (rewrite symbols
to the canonical name) or at runtime fact lookup (resolve through the alias map),
and the trade-off between the two; how it interacts with the version key so
editing aliases re-evaluates affected sessions; and how eval-engine's
cross-tool param-correlation checks (Family 3 provenance) consume the same map
so they don't read two aliases of one key as two different params.

---

## 16. Sessions spread across time — real session management

Not started. Today a "session" is reconstructed after the fact as every span
sharing a `session.id`, whatever trace it's in (see the OOB section, "Sessions"
— `list_sessions` groups on `session.id`). That works because a demo session is
short and contiguous. A real user session can span hours or days with long
gaps, so tracking one needs actual session management rather than a
group-by-attribute over recent spans.

Things to work through: session lifecycle (open / idle-timeout / explicit close,
and whether a gap starts a new session or continues the old one); how
eval-engine decides a session is *complete* enough to evaluate when spans keep
arriving later (its "reconstruct then run three families" model assumes a
bounded, finished session); whether verdicts are re-run as more spans land in an
already-evaluated session (interacts with the version key); and retention /
windowing so a long-lived session doesn't force an unbounded re-read (ties into
entries 10 and 13 on ingestion scalability).

---

## 17. Correlate sessions to a logged-in user

Not started. A session today carries `session.id`, `user.id`, `user.role`,
`channel` on its spans (the trusted-layer caller identity — see the OOB
"Sessions" columns and LoanPro's `X-LoanPro-*` headers), but that is the
governed *caller*, not the authenticated human operating the console. Tie each
session to a logged-in user so sessions can be listed, filtered, and scoped by
that user.

Depends on entry 14 (auth / user identification) for a real logged-in identity
to correlate to, and feeds entries 9 and 11 (per-user audit logs and reports).
To work through: whether the correlation is the caller identity already on the
span or a separate operator id, where it's stored (a session column vs. a
join), and — carrying entry 14's constraint — that the user id stays domain
vocabulary in config/artifacts, not engine code (Hard Rule 1).

---

## 18. Retention policy for spans and verdicts

Not started. Both the OOB span store (ClickHouse `prefront.spans`) and
eval-engine's outputs (`eval_verdicts`, `eval_conformance_tags`,
`eval_evaluated_sessions`) grow unbounded — there is no TTL or pruning today.
The only existing knobs are manual, all-or-nothing wipes (`DELETE /oob/spans`,
`DELETE /eval/verdicts`, `DELETE /oob/phoenix` — see "Clearing trace data spans
three services"). The decision-trace store is the only place with any bound at
all (`decision_trace` capped at 100), and that's a UI-layer cap, not a
retention policy.

Add a configurable retention policy so old spans and verdicts age out
automatically. To work through: TTL vs. row-count cap vs. time-window;
per-application / per-tenant retention (interacts with entries 8 and 14) vs. one
global setting; ClickHouse-native TTL on `spans` vs. an app-driven prune; how it
interacts with re-ingestion (oob-ingest re-pulls from Phoenix, so Phoenix
retention has to be coordinated — see "Clearing ClickHouse alone is only a
pause") and with the version key (a re-evaluation must not resurrect
aged-out verdicts). Ties into entries 10 and 13 (ingestion scalability), where
unbounded growth is the underlying pressure.

---

## 19. Dashboard settings page: retention config + storage metrics

Not started. The UI companion to entry 18. Add a settings page in the dashboard
that (a) lets an operator configure the retention policy from entry 18 (TTL /
cap / window, per-application if that lands) instead of it being env-var-only,
and (b) surfaces storage metrics — span and verdict counts and on-disk size
across the stores (ClickHouse `spans`, eval-engine's `eval_*` tables, the
`decision_*` tables) so growth is visible before it becomes a problem.

To work through: which service exposes the size/count metrics (oob-ingest
already has `/oob/status` with source counts; eval-engine would need a
storage-stats endpoint) and whether the settings write goes to a config
artifact vs. a live-tunable store; keep any application/tenant vocabulary in the
artifact, not engine code (Hard Rule 1). Depends on entry 18 for the policy it
configures.

---

## 20. Pinning specific user sessions

Not started. Let an operator pin specific sessions so they're kept and easy to
return to — flagged for follow-up (an interesting case, a suspected violation,
an investigation). Two things fall out of a pin: it should exempt the session
from the entry 18 retention policy (a pinned session must not age out), and it
should be surfaceable/filterable in the Sessions view.

To work through: where the pin is stored (a session flag/column vs. a separate
pins table) and how it survives re-ingestion (oob-ingest re-pulls spans, so a
pin keyed on `session.id` has to persist independently of the span rows); how it
overrides retention in entry 18; and — if pins are per-operator — that it
depends on entries 14/17 for the logged-in identity to attribute a pin to.

---

## 21. Severity ratings for findings

**Derived-severity half DONE (4c595ba); per-check DECLARED severity still open.**
Findings are no longer flat — each now carries a severity (critical / high /
medium / low) that can be ranked, filtered, and triaged.

**What shipped — a DERIVED rating, UI-layer only, engine untouched.** Severity is
a pure function of two fields already on every finding, `family` + `effect`
(`eval-engine/evalengine/contract.py:67,69`), resolved against a customer-editable
ordered rule-list (first-match-wins). Because both inputs already ride on the
`/eval/findings` row, there is **no eval-engine change, nothing stored on the
finding, and no version-key bump** — the whole point of this half. Pieces:
- config store `severity_rule` (`prefront-ui/lib/db/src/schema/severityRule.ts`,
  PK `(demo, ordinal)`), applied by api-server's startup `drizzle-kit push-force`;
- api-server `GET/PUT/DELETE /api/settings/severity`
  (`prefront-ui/artifacts/api-server/src/routes/settings.ts`), holding
  `DEFAULT_SEVERITY_RULES` (the seeded "effect wins" mapping — block→critical /
  approval_required→high for every family, family2/Integrity→medium as its
  fallback, flag/catch-all→low);
- resolver + display: `severity.ts` (`severityOf`, `SEVERITY_META`),
  `useSeverityRules`, a Severity column + filter in `DecisionTraces.tsx` (now
  sorted severity-first), a `bySeverity` roll-up + deep-link on `Overview.tsx`,
  and an editable rule-list with live preview in `Settings.tsx` (reached from the
  sidebar gear — the first "under the hood" Settings surface).

Domain-neutral by construction: rules key on engine concepts (family1/2/3,
block/approval_required/flag/allow) only, never a demo's tables/roles/fields, so
this stays out of engine code entirely (Hard Rule 1 holds).

**Still open — per-check DECLARED severity, the richer variant.** A severity
declared *per check* in the artifacts (`rule_pack.yaml` rules,
`intent_catalog.yaml` intents, and the built-in Family 2 checks each carrying a
baseline a finding can escalate), rather than derived from family+effect alone —
so e.g. a leaked SSN outweighs an under-declared `fields` list even though both
are the same (family, effect). This one DOES thread through `eval_verdicts` and
the version key (re-rating must re-evaluate), and any per-check severity that
names domain vocabulary must live in the artifact, not engine code (Hard Rule 1).
Reuse the existing scale rather than inventing a second one — `ReportFindings`
already ranks most-severe-first and Verdict shows `expected_findings`. Feeds the
same Findings sort/filter (already built) and future reports (entry 11).

---

## 22. Help menu + per-page documentation

Not started. The UI has no in-app help: a new operator has to read this repo's
CLAUDE.md / design docs to understand what each tab does. Add a help menu and
per-page documentation so each page can explain itself in-app.

Two pieces:

- **A help menu** — a global entry point (natural home is the sidebar, beside
  the gear that already opens `Settings.tsx`) surfacing the docs and an index
  of the pages.
- **Per-page documentation** — each tab gets its own contextual help
  explaining what it does and how it fits the pipeline. The tabs to cover are
  the pipeline order already documented above: **Data Connector → Data Graph →
  Business Graph → Policy Studio → Semantic**, plus the Dashboard, Decision
  Traces, Observability, and Settings surfaces, and Verdict (its own app —
  a change meant for both must be made twice; see "UI layout").

To work through: whether help is a modal/flyout per page vs. a single docs
route, where the copy lives (inline in each component vs. a shared content
module vs. sourced from the existing design docs so it doesn't drift), and
that any demo-specific wording stays UI-layer, not engine code (Hard Rule 1).
This is `prefront-ui` work only — no engine change. See `prefront-ui/CLAUDE.md`
for the tab architecture.
