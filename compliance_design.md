# Prefront and regulatory compliance — design

> **Status: PROPOSED.** Nothing in this document is built. It maps what the
> engine already produces onto what GDPR, SOC 2, PCI-DSS, HIPAA and a
> deployment's own domain regulations ask for, names the gaps no mapping can
> cover, and proposes the two artifacts that would turn existing verdicts into
> framework evidence. It adds no check and no engine vocabulary.
>
> **Framework citations** (GDPR article numbers, SOC 2 Trust Services Criteria
> ids, PCI-DSS v4.0 requirement numbers, HIPAA CFR sections) were written from
> working knowledge and have **not** been verified against the primary texts
> in the session that produced this document. Verify them before any external
> use.
>
> **Deployment independence.** §1–§6 name no demo, no demo table, column, role,
> channel or policy section. Everything specific to the deployment that happens
> to be bundled today lives in Appendix A, which is illustrative and is replaced
> wholesale by the next deployment.

---

## 1. Thesis

A framework audit does not ask "do you have a policy?". It asks **"show me
evidence the control operated"** — for this period, for this system, with
something an auditor can sample. For an AI agent that reads and writes
enterprise data, that evidence barely exists today: the agent's behaviour is
in prompts, its access is whatever the connection allowed, and the log is a
chat transcript.

Prefront already produces the evidence the question wants, because it was
built to answer a stricter question — *did this agent do sanctioned work, in a
sanctioned shape, with values it can be trusted with?* Every check emits one
contract (`eval-engine/evalengine/contract.py:57-82`): `check_id`, `status`
(`satisfied | violated | indeterminate`), `effect`, and an `Evidence` of
span-id references plus a minimal excerpt (`contract.py:48-53`; Hard Rule 8,
`autonomous_build.md:241`). Satisfied verdicts are first-class and persisted
as conformance tags (Hard Rule 15; `eval_conformance_tags`,
`eval-engine/evalengine/ch.py:65-83`), every verdict is stamped with the
artifact versions it was evaluated under (`evaluate.py:27-30`), and the
inline runtime writes one append-only record per governed call
(`semantic-mcp-server/semanticmcp/governance/trace.py:63`).

So the positioning is: **Prefront is the control-evidence layer for AI-agent
data access.** It does not make a customer compliant — no software does. It
makes the agent's conformance *demonstrable* and its violations *findable*,
per session, per clause, with a span to point at.

Three constraints every compliance feature inherits, none new:

1. **Hard Rule 1 — framework vocabulary is config, never engine code.**
   `eval-engine/tests/test_domain_independence.py:33,40-47` already bans
   `card`, `account`, `ssn`, `taxid`, `customer`, `pin`, `routing`, `balance`
   from executable engine tokens. A control name, a data-class label, and the
   binding from a data class to real columns are all artifacts, exactly like
   `rule_pack.yaml`'s `detectors.field_names` and `intent_catalog.yaml`'s
   `restricted_fields` today.
2. **Hard Rule 8 — evidence is span references and excerpts.** A compliance
   export inherits this: it points into the trace store, it never copies
   payloads into a report.
3. **Truthfulness about mode.** An ungoverned deployment evaluated out of
   band yields **shadow** evidence — "what Prefront would have decided before
   execution". A report over it says *would have blocked*, never *blocked*
   (`prefront-ui/CLAUDE.md`, the Overview's truthfulness rule). Only the inline
   runtime (`semanticmcp/governance/decide.py:3`, precedence
   `block > approval_required > allow`) produces an enforced outcome.

---

## 2. The two-layer model

This is the mechanism that keeps the whole thing deployment-independent. A
framework is generic; a deployment is not; the join between them is a small
vocabulary of **control classes** and **data classes** that both sides speak.

### 2.1 Layer A — framework packs (shipped, generic)

One pack per regime — GDPR, SOC 2 TSC, PCI-DSS v4.0, HIPAA — each a list of
controls. A control names a **control class** it belongs to and, where
relevant, the **data class** it protects. A pack never names a column, table,
role, channel, or a deployment's policy section.

**Control classes** (framework-neutral; the engine's checks are grouped under
them in §3):

| control class | what it asserts about an agent's data access |
|---|---|
| `access` | only sanctioned callers invoke sanctioned operations |
| `minimization` | no more fields, rows or calls than the task needs |
| `purpose_limitation` | the work done matches the request that occasioned it |
| `segregation` | separately-permitted reads are not composed into an unpermitted whole |
| `field_protection` | protected fields are masked, withheld, or substituted |
| `integrity` | values flowing in and out are neither invented nor altered |
| `human_oversight` | gated actions carry real approval, not a claimed one |
| `injection_resistance` | retrieved content never becomes a privileged parameter |
| `audit_logging` | every access is recorded, attributably, immutably |
| `retention` | records are kept for the mandated period and then removed |
| `change_management` | controls change only through review and versioning |
| `monitoring` | behaviour is watched for anomalies and drift over time |

**Data classes** (abstract labels; a deployment binds each to its own columns
in Layer B):

| data class | frameworks that key on it |
|---|---|
| `personal_data` | GDPR (any identifiable natural person), SOC 2 P-series |
| `special_category` | GDPR Art. 9 (health, biometric, ethnicity, …) |
| `phi` | HIPAA protected health information |
| `cardholder_data` | PCI-DSS (PAN, cardholder name, expiry, service code) |
| `sensitive_auth_data` | PCI-DSS (full track, CVV, PIN — never storable) |
| `financial_npi` | GLBA-style non-public personal financial information |
| `credentials` | secrets, tokens, passwords |
| `internal_confidential` | SOC 2 C-series; a deployment's own trade secrets |

### 2.2 Layer B — deployment overlay (per application, approved like any artifact)

The overlay binds the abstract labels to this deployment and, optionally,
declares the deployment's **own** regulatory regime — the sector law its policy
document already cites. It is authored and approved through the same
candidate → review → publish path as every other artifact, and it is the only
place domain vocabulary appears.

```yaml
schema_version: prefront.compliance_overlay.v1
deployment: <id>                       # the value that scopes /eval/* one day
policy_document: <the deployment's policy file>

data_classes:                          # abstract label -> physical columns
  personal_data:   [<table>.<column>, ...]
  cardholder_data: [<table>.<column>]
  phi:             []                  # empty = this deployment holds none

frameworks: [gdpr, soc2]               # which Layer A packs to report under

domain_regime:                         # OPTIONAL: the deployment's own law
  - regime: <name the policy document cites>
    bindings:
      - policy_section: "<n.m>"        # a heading in policy_document
        control_class: field_protection
        data_class: financial_npi
      - policy_section: "<n.m>"
        control_class: human_oversight
```

A lending deployment binds its adverse-action and field-control sections here;
a clinic binds its treatment-purpose and disclosure-accounting sections; a
payments deployment binds its acquirer's display rules. **The pattern is one;
the content is theirs.** Nothing in Layer A changes between them.

### 2.3 The binding rule — why no new check is needed

- `control → control_class → check_ids` is fixed by the engine's own
  vocabulary (§3). A pack row like `GDPR Art. 5(1)(c) → minimization` resolves
  to `field_scope`, `filter_scope`, `volume_scope`, `minimization`,
  `param_discard` without the pack ever naming them.
- `data_class → columns` is Layer B. The engine already evaluates
  `check_id × field name` (`field_restriction`'s detector sets,
  `family1/content.py:152`; `field_scope`'s approved set,
  `family3/scope.py:39`), so "was `cardholder_data` masked?" is a filter over
  verdicts whose evidence names a column in that class.
- A framework report is therefore a **join over existing verdicts**: a
  control is *evidenced* when its check ids produced `satisfied` verdicts on
  data of its class in the period, *violated* when any produced `violated`,
  and — the important third state — **"no evidence"** when nothing ran: the
  check is not configured (Family 1 or 3 unconfigured degrades to zero
  verdicts, Hard Rule 9), the data class is unbound, or no session exercised
  it. "No evidence" is never rendered as "clean". This is the same honesty
  rule the Findings surface already lives by: absence of a verdict is
  "not applicable", not "satisfied" (Hard Rule 16).

### 2.4 Proposed artifact: `compliance_map.yaml`

Layer A packs would ship with the engine as data (a `profiles/`-style
directory, the way `trace_binding.default.yaml` does); the Layer B overlay
would live beside the deployment's other artifacts on the shared volume and be
located by an env var in the `EVAL_RULE_PACK_PATH` / `EVAL_INTENT_CATALOG_PATH`
pattern (`eval-engine/evalengine/config.py:46-47`), degrading to "no
frameworks configured" when absent.

```yaml
# Layer A, one file per framework (shipped)
schema_version: prefront.framework_pack.v1
framework: gdpr
version: 1
controls:
  - id: art5_1_b
    title: Purpose limitation
    control_class: purpose_limitation
    data_class: personal_data
  - id: art5_1_c
    title: Data minimisation
    control_class: minimization
    data_class: personal_data
  - id: art32
    title: Security of processing
    control_class: field_protection
    data_class: personal_data
```

What it touches and what it must not:

- It **reads** `eval_verdicts` and `eval_conformance_tags`; it writes nothing
  to them. The verdict's `source` (`contract.py:79-82`) stays the rule pack's
  own citation — a framework control is a *view* over verdicts, not a second
  citation stamped onto them (Hard Rule 17).
- It must **not** change `eval_verdicts`' sort key
  `(session_id, check_id, rule_id, evidence_excerpt)` (`ch.py:51`) — a
  column can be added (`_ADDED_VERDICT_COLUMNS`, `ch.py:59-62`), an identity
  cannot.
- Because it changes nothing about how a verdict is computed, it does **not**
  join the version key (`evaluate.py:27-30`). A pack edit re-renders the
  report; it never re-evaluates a session.

---

## 3. Control classes → what the engine already has

Engine vocabulary only. "Inline" is the governed MCP runtime
(`semantic-mcp-server/semanticmcp/governance/`); "OOB" is the evaluation
engine over ingested traces (`eval-engine/evalengine/`).

| control class | inline mechanism | OOB check ids | evidence produced today |
|---|---|---|---|
| `access` | caller resolved from config, never from the agent (`governance/identity.py:15,48`); no identity ⇒ every call denied (`no_caller_identity`) | `catalog_membership`, `entitlement`, `version_conformance`, `side_effect_class` (`family3/call.py:14-20`) | per-call verdict: who called what, whether the intent exists for that role/channel, whether a read intent performed a write |
| `minimization` | intent bindings carry `allowed_attributes` / `mandatory_filters` (`semantic-layer/semanticlayer/bindings.py:18-50`); templates execute only approved SQL | `field_scope`, `filter_scope`, `volume_scope` (`family3/scope.py:39,72,97`); `minimization` (`family2/minimization.py:20`); `param_discard` (`family2/param_discard.py:78`) | columns fetched vs approved, mandatory predicate present or not, rows vs declared magnitude, dropped user constraints |
| `purpose_limitation` | — (an intent is by construction purposeful; no inline check of the request text) | `goal_alignment` (`family3/session.py:73`) | session's request matched an approved trigger descriptor, or a low-severity "no descriptor matched" |
| `segregation` | — | `toxic_combination` (`family3/session.py:41`) | individually-permitted intents composed in one session against a declared `toxic_with` |
| `field_protection` | `SensitivityRule` + `Masking` (`semanticlayer/schema.py:184-196`), sensitive ⇒ deny by default (`schema.py:194`); restricted fields masked on read, a write touching one blocked inline (`decide.py:7-10,29-38`); redaction to `***` (`semanticmcp/server.py:404-412`) | `field_restriction` (`family1/content.py:152`), `substitution` (`content.py:71`) | masked field names on the trace; a protected field's presence in a result or answer; the declared substitute's presence |
| `integrity` | parameters bound into approved templates — no free SQL | `param_provenance`, `param_mutation`, `param_staleness`, `entity_consistency`, `result_fidelity`, `error_blindness` (`family2/*.py`) | value-provenance verdicts: invented, altered, stale, wrong-subject, ungrounded, ignored-error |
| `human_oversight` | `block > approval_required > allow`; an indeterminate gating rule fail-safes to `approval_required` (`decide.py:12-14,54-59`); writes dry-run unless `ENABLE_WRITES=1` | `approval_gate` (`family1/predicate.py:117`), `approval_evidence` (`family2/approval_evidence.py:35`) | approver roles on the trace; an approval-shaped event in the session, or a claimed approval with none |
| `injection_resistance` | agent cannot set caller identity; only template parameters are bindable | `param_taint` (`family2/param_taint.py:12`) | a value of `untrusted` trust class reaching a privileged parameter |
| `audit_logging` | one JSON line per governed call — caller, decision, reasons, `masked_fields`, `rules_evaluated[]`, `execution_status` (`governance/trace.py:63`); `decision_trace` table (`prefront-ui/lib/db/src/schema/decisionTrace.ts`) | `eval_verdicts`, `eval_conformance_tags` (`ch.py:26-83`), version-stamped (`evaluate.py:27`) | a per-call decision record; a per-session, per-clause satisfied/violated record with span ids |
| `retention` | **none** | **none** | — (see §5.1) |
| `change_management` | LLM output is candidate-only; approve → publish → versioned YAML; the runtime hot-reloads only published artifacts | `rule_audit_log` (`prefront-ui/lib/db/src/schema/ruleAuditLog.ts:5-16`: who approved/rejected which rule, before/after); every verdict carries `rule_pack_version` / `catalog_version` | which human approved which control, when; which artifact version each verdict was judged under |
| `monitoring` | — | the eval worker (continuous); `outcome_consistency`, `invocation_drift`, `verdict_trend` (`family3/population.py:22-24`) | nondeterminism per intent, intent-mix shift after a deploy, violation-rate trend per rule |

Data classification today: `pii-analyzer/app.py` (design time, column
**names** only, twelve recognisers) and the semantic layer's `classification`
literal, which knows exactly three values — `pii`, `financial_sensitive`,
`confidential_business` — chosen by substring (`semanticlayer/policy.py:153-159`).
Neither knows `phi`, `cardholder_data` or `special_category`; see §5.3.

---

## 4. Per-framework packs (Layer A content)

Each subsection: what the framework asks of an agent's data access, the
control table, and what a report under it reads like. Only controls that
plausibly bind to *agent data access* are listed. Out of scope for every
framework, and said so in the report: physical security, network segmentation,
encryption at rest and in transit, vendor management, workforce training,
vulnerability management, business continuity.

### 4.1 GDPR

*What it asks:* that personal data is processed for a stated purpose, no more
than needed, accurately, for no longer than needed, securely — and that the
controller can **demonstrate** all of that (Art. 5(2)).

| control | control class | data class | evidence today | gap |
|---|---|---|---|---|
| Art. 5(1)(b) purpose limitation | `purpose_limitation`, `segregation` | `personal_data` | `goal_alignment`, `toxic_combination` verdicts | descriptor match is coarse; "no descriptor matched" is a signal, not a verdict |
| Art. 5(1)(c) data minimisation | `minimization` | `personal_data` | `field_scope`, `filter_scope`, `volume_scope`, `minimization`, `param_discard` | needs `fields:` declared per intent — empty means silent (`family3/scope.py:39`) |
| Art. 5(1)(d) accuracy | `integrity` | `personal_data` | `param_mutation`, `result_fidelity`, `param_staleness` | — |
| Art. 5(1)(e) storage limitation | `retention` | `personal_data` | **none** | §5.1: no TTL on any store |
| Art. 5(1)(f) integrity & confidentiality | `field_protection`, `access` | `personal_data`, `special_category` | masking, `field_restriction`, `entitlement` | §5.2: the trace store itself holds raw payloads |
| Art. 5(2) accountability | `audit_logging`, `change_management` | — | version-stamped verdicts, `rule_audit_log`, governance trace | §5.4: audit stores are not tamper-evident |
| Art. 22 automated decision-making | `human_oversight`, `field_protection` | `personal_data` | `approval_gate`, `approval_evidence`, `substitution` | — |
| Art. 25 data protection by design & default | `access`, `field_protection` | — | allow-listed intents; sensitive ⇒ deny default (`schema.py:194`) | — |
| Art. 30 records of processing | `audit_logging` | `personal_data` | `eval_conformance_tags` per clause; governance trace per call | no per-subject or per-purpose index (§5.6) |
| Art. 32 security of processing | `access`, `field_protection`, `audit_logging` | `personal_data` | as above | as above |
| Art. 15 / 17 subject access & erasure | — | `personal_data` | **none** | §5.6: no subject index; §5.2: payloads in `spans.output_value` cannot be erased per subject |
| Art. 33 breach notification | `monitoring` | `personal_data` | findings with severity, `verdict_trend` | no incident record or 72-hour clock; findings are not incidents |

*What the report reads like:* "In the period, N sessions touched
`personal_data`. Minimisation (Art. 5(1)(c)) was evidenced on M and violated
on K (list, each with a span). Purpose limitation (Art. 5(1)(b)) has M
satisfied descriptor matches and J unmatched sessions flagged for review.
Storage limitation (Art. 5(1)(e)): **no evidence — retention is not
configured on this deployment.**"

### 4.2 SOC 2 (Trust Services Criteria)

*What it asks:* that logical access is restricted and reviewed, changes are
controlled, anomalies are detected and evaluated, and confidential /
personal information is handled per commitments — with evidence an auditor
can sample over the period.

| criterion | control class | data class | evidence today | gap |
|---|---|---|---|---|
| CC6.1 logical access security | `access` | — | `identity.py` (caller from config), `entitlement`, `catalog_membership` | no authenticated **operator** identity for the UI/API (§5.5) |
| CC6.3 role-based access, least privilege | `access`, `minimization` | — | `entitlement`, `field_scope`, `side_effect_class` | — |
| CC6.5 disposal of data | `retention` | — | **none** | §5.1 |
| CC6.6 / CC6.7 boundary & data-in-transit controls | — | — | out of scope | — |
| CC7.2 monitor for anomalies | `monitoring` | — | eval worker, population checks, Overview | — |
| CC7.3 / CC7.4 evaluate & respond to security events | `monitoring`, `human_oversight` | — | findings with derived severity (`severity_rule`) | no incident/ticket lifecycle; a finding has no "acknowledged/resolved" state |
| CC8.1 change management | `change_management` | — | candidate → approve → publish; `rule_audit_log`; version stamps on verdicts | `rule_audit_log` is unauthenticated and mutable (§5.4); reviewer identity is a free-text name |
| C1.1 identify & protect confidential information | `field_protection` | `internal_confidential` | sensitivity rules + masking | classification is substring-based (§5.3) |
| C1.2 dispose of confidential information | `retention` | `internal_confidential` | **none** | §5.1 |
| P4.1 limit use to identified purposes | `purpose_limitation` | `personal_data` | `goal_alignment` | as GDPR 5(1)(b) |
| P4.2 / P4.3 retain & dispose per purposes | `retention` | `personal_data` | **none** | §5.1 |
| P5.1 data subject access | — | `personal_data` | **none** | §5.6 |
| P6 disclosure & accounting | `audit_logging` | `personal_data` | per-call trace; per-session verdicts | no per-subject disclosure index (§5.6) |

*What the report reads like:* the CC6/CC7/CC8 rows are the strongest
non-privacy story Prefront has — every governed decision is a CC6.1 sample,
every approved rule is a CC8.1 sample with a named reviewer and a before/after
diff, every population check is CC7.2 evidence. The report should say
plainly that CC6.6/6.7 and the P5/P6 rows are not covered.

### 4.3 PCI-DSS v4.0

*What it asks:* that account data is protected wherever stored, displayed or
transmitted; that access to it is need-to-know; that every access is logged,
protected from tampering, reviewed and retained.

| requirement | control class | data class | evidence today | gap |
|---|---|---|---|---|
| 3.3 SAD not retained after authorisation | `retention`, `field_protection` | `sensitive_auth_data` | **none** | the engine cannot see what the upstream store holds; it can only evidence that no tool ever *returned* SAD — a `field_restriction` detector on the class |
| 3.4 PAN masked when displayed | `field_protection` | `cardholder_data` | masking (`server.py:404-412`), `field_restriction` on the class's columns | data class must be bound in Layer B; nothing binds it today (§5.3) |
| 3.5 PAN unreadable anywhere stored | `field_protection` | `cardholder_data` | **none for the trace store** | §5.2: `spans.output_value` stores raw tool results |
| 7.2 access by business need to know, least privilege | `access`, `minimization` | `cardholder_data` | `entitlement`, `field_scope`, `filter_scope` | — |
| 8.2 unique identification of users | `access`, `audit_logging` | — | `user_id` on every span (`oobingest/model.py:31`) | the **agent** is not identified separately from the human (§5.5) |
| 8.6 application / system accounts | `access` | — | caller identity is config-bound, agent cannot spoof it (`identity.py:15`) | — |
| 10.2 audit logs of all access to cardholder data | `audit_logging` | `cardholder_data` | governance trace, verdicts | need class-scoped query — Layer B |
| 10.3 logs protected from destruction/modification | `audit_logging` | — | **none** | §5.4 |
| 10.4 logs reviewed | `monitoring` | — | Findings, Overview | no review attestation record |
| 10.5 retain 12 months, 3 immediately available | `retention` | — | **none** | §5.1 |

*Honesty note for the report:* a deployment that binds no `cardholder_data`
columns produces **no PCI evidence at all**, and the report must say
"`cardholder_data` unbound — nothing to assess", not a row of zeros.

### 4.4 HIPAA (Privacy and Security Rules)

*What it asks:* that PHI use and disclosure is limited to the **minimum
necessary**, access is controlled and uniquely attributed, activity is
reviewed, integrity is protected, disclosures are accountable, and
documentation is retained.

| section | control class | data class | evidence today | gap |
|---|---|---|---|---|
| §164.502(b) / §164.514(d) minimum necessary | `minimization`, `purpose_limitation` | `phi` | `field_scope`, `filter_scope`, `volume_scope`, `minimization`, `goal_alignment` | **the strongest single fit in this document** — it is the check family's own thesis (`prefront-check-families.md:76`); needs `phi` bound in Layer B |
| §164.308(a)(1)(ii)(D) information system activity review | `monitoring`, `audit_logging` | `phi` | eval worker, Findings, conformance tags | no review attestation |
| §164.308(a)(4) information access management | `access` | `phi` | `entitlement`, `catalog_membership` | — |
| §164.312(a)(1) access control; (a)(2)(i) unique user id | `access` | `phi` | `identity.py`; `user_id` on spans | agent vs human attribution (§5.5) |
| §164.312(b) audit controls | `audit_logging` | `phi` | governance trace, verdicts | tamper-evidence (§5.4) |
| §164.312(c)(1) integrity | `integrity` | `phi` | Family 2 provenance verdicts | — |
| §164.524 individual access | — | `phi` | **none** | §5.6 |
| §164.528 accounting of disclosures | `audit_logging` | `phi` | per-call trace exists but is not indexed by subject | §5.6 |
| §164.316(b)(2) / §164.530(j) six-year documentation retention | `retention` | — | **none** | §5.1 |

*What the report reads like:* "Minimum necessary: N sessions read `phi`; M
stayed within the approved field set and mandatory filter, K exceeded it
(each listed with the span and the excess columns). Accounting of disclosures:
**not available — no per-subject index.**"

### 4.5 Domain regimes — the pattern, not a list

Most deployments live under a sector regime the four packs do not cover —
consumer-lending law, insurance regulation, a state privacy act, an acquirer's
rules, a securities record-keeping rule — and their **own policy document
already cites it**. That regime enters through Layer B, not through a new
pack: each clause is a `policy_section → control_class` binding on the
deployment's document, evaluated through the same check ids, reported under
the regime's own name.

Three one-line illustrations across different domains, so no single one looks
canonical:

- *lending* — "every decline is followed by an adverse-action notice in the
  same session" → `human_oversight` via `workflow_integrity` (a declared
  `closing_obligation`); "raw score withheld from front-line roles, tier label
  supplied instead" → `field_protection` via `field_restriction` +
  `substitution`.
- *healthcare* — "records accessed only for treatment of the presenting
  patient" → `purpose_limitation` via `goal_alignment` and `segregation` via
  `toxic_combination`; "chart access attributed to a named clinician" →
  `access` via `entitlement`.
- *payments* — "PAN never displayed unmasked outside the settlement console"
  → `field_protection` via `field_restriction` scoped by channel.

Two limits to state in any regime report:

1. **Content obligations are half-covered.** "Send the notice" is a tool-call
   obligation (`workflow_integrity`); "the notice must contain these eight
   elements" is an answer-content obligation, and every content check is
   prohibitive — it can say a forbidden thing appeared, not that a required
   thing did, except for the narrow `substitution` case
   (`prefront-check-families.md` § Known gaps).
2. **A cited section with no binding is a stated obligation, not evidence.**
   The report lists it as such. This is what the coverage matrix already does
   for the bundled deployment (Appendix A).

---

## 5. What a mapping cannot fix — hard gaps, ranked

All deployment-agnostic. Each names the frameworks it blocks.

### 5.1 No retention anywhere

`oob-ingest/oobingest/ch.py:22-64` (`spans`) and
`eval-engine/evalengine/ch.py:26-97` (`eval_verdicts`,
`eval_conformance_tags`, `eval_evaluated_sessions`) declare no `TTL`; the only
removals are the manual all-or-nothing wipes (`DELETE /oob/spans`,
`DELETE /eval/verdicts`, `DELETE /oob/phoenix`). The UI-side `decision_trace`
is the opposite problem — pruned to the newest 100 per deployment on every
write (`prefront-ui/artifacts/api-server/src/routes/decisions.ts:33-46`),
which is a cap, not a schedule. Blocks GDPR 5(1)(e), SOC 2 CC6.5/C1.2/P4.3,
PCI 10.5, HIPAA §164.316(b)(2), and any sector rule with a minimum period.
A retention design also has to cover legal hold (a kept-forever exemption)
and coordinate with Phoenix, which re-feeds anything ClickHouse forgets.

### 5.2 The trace store holds raw payloads

`oob-ingest/oobingest/model.py:34` caps `input_value` / `output_value` at
`_MAX_TEXT = 64_000` characters and otherwise stores them whole — for a tool
span that is the full result including rows. `scrub()` drops only inline-path
attribute prefixes, never payload text. The OpenAI instrumentor records
prompts and completions too; the suppressors are opt-in and commented out
(`.env.example:53-54`). Prefront's *own* spans deliberately export the
decision and never rows (`semanticmcp/server.py:161`), so the engine side is
minimization-friendly; the ingest side is not. A demo app's rows-on-span knob
is that app's choice, not the engine's, but the store that receives it is
ours. Blocks GDPR 32 (and per-subject erasure, 5.6), PCI 3.5, HIPAA
§164.312. The fix is class-aware redaction **at ingest**, driven by the Layer
B data-class bindings — which is why 5.3 comes first.

### 5.3 No abstract data classes

`semanticlayer/policy.py:153-159` classifies a field by substring into exactly
`pii` / `financial_sensitive` / `confidential_business`; `SensitivityLevel` is
`normal | confidential | restricted` (`schema.py:49`). The PII analyser's
results (`pii-analyzer/app.py`) are rendered as badges in the Data Graph and
never reach `build_bindings`. There is no `phi`, no `cardholder_data`, no
`special_category`, and no way for a deployment to declare one. Every
class-keyed row in §4 is unbindable until this exists. Layer B (§2.2) is the
proposed home; the PII analyser becomes its *suggestion* source, human
approval its gate — the same posture as every other candidate.

### 5.4 Audit stores are not tamper-evident, and one fails silently

- `governance/trace.py:63-72` swallows `OSError` — a full disk or a read-only
  mount loses the audit record and the call proceeds. Correct for
  availability, wrong for a control whose whole value is completeness.
- `decision_trace` is documented as append-only and pruned on write (5.1).
- `rule_audit_log` (`ruleAuditLog.ts:5-16`) is written through an
  unauthenticated `POST /api/audit`; `reviewerName` is whatever the client
  sent.
- Nothing is hash-chained or write-once.

Blocks SOC 2 CC8.1 (as *evidence*), PCI 10.3, HIPAA §164.312(b), and any
regime with an immutability clause. Minimum viable: a hash chain over the
governance trace and `rule_audit_log`, plus a persisted failure counter that
surfaces on the status endpoints.

### 5.5 No authenticated operator, no agent-vs-human attribution

The UI and api-server have no login; a verdict knows the governed *caller*
(`user_id`, `user_role` — `oobingest/model.py:31`) but not which operator
looked at it, approved a rule, or cleared a store. And a trace attributes the
agent's action to the human it acted for — nothing identifies the agent as a
separate principal. Blocks SOC 2 CC6.1/CC8.1 as sampled evidence, PCI 8.2,
HIPAA §164.312(a)(2)(i), and every sector rule that says "the agent is logged
separately from the user".

### 5.6 No per-subject index

Access, erasure and accounting-of-disclosure requests (GDPR Art. 15/17,
SOC 2 P5/P6, HIPAA §164.524/§164.528) all start from "which records about
person X were touched, by whom, why". The trace has the *who* and *when*; the
*which subject* is inside `input_value` / `output_value` as unindexed text.
Building it needs 5.3 (which columns identify a subject) and interacts with
5.2 (an erasure has to reach the payloads).

### 5.7 Positive content obligations

Named in §4.5. A family-level gap already recorded in
`prefront-check-families.md` § Known gaps; not solvable by mapping.

### 5.8 Single-tenant read paths

`/oob/*` and `/eval/*` are not scoped by deployment (`prefront-ui/CLAUDE.md`
§ Overview caveat); `eval_verdicts` carries no deployment column. A report
"for this system, for this period" needs the scope the Layer B `deployment:`
key is meant to supply.

---

## 6. Suggested build order

1. **Layer A packs + the report view.** Zero engine change, visible value
   immediately: an aggregate over verdicts grouped by control, with the three
   honest states (evidenced / violated / no evidence) and span-linked samples.
   Every "no evidence" cell doubles as the gap list for that customer.
2. **Layer B overlay, starting with data classes.** Closes the PII-analyser
   loop and makes every class-keyed row in §4 bindable; unlocks class-aware
   redaction at ingest (5.2) as a follow-on.
3. **Retention + audit integrity** (5.1, 5.4). Independent of the above but
   without them the report's storage-limitation and log-integrity rows stay
   permanently "no evidence".
4. **Per-subject index** (5.6). Last, because it depends on 2 and on the
   ingest-side redaction from 5.2.

---

## Appendix A — worked example: the deployment bundled today

> **Illustrative only.** This appendix shows what a Layer B overlay looks like
> for the demo currently wired into the compose files. It is the one place
> this document names that demo. A different deployment replaces this
> appendix entirely; nothing in §1–§6 changes.

The bundled subject is `loanpro-demo/`, an ungoverned consumer-lending agent
whose citable policy is `loanpro-demo/docs/loan_underwriting_policy.md`. Its
§17 names the regime it operates under — ECOA/Reg B, FCRA, TILA/Reg Z, GLBA,
BSA/AML, and CFPB guidance on AI in underwriting — and §12, §15–§19 carry the
data-protection, notice, evidence, retention and change-control clauses. The
generated coverage contract (`loanpro-demo/docs/check-coverage.md:146-195`)
already says which of those sections a check evidences; the overlay below is
that matrix re-expressed in control classes.

### A.1 A Layer B overlay for it

```yaml
schema_version: prefront.compliance_overlay.v1
deployment: loanpro
policy_document: loan_underwriting_policy.md

data_classes:
  personal_data:  [applicants.ssn, applicants.tax_id, applicants.bank_account_hint]
  financial_npi:  [applicants.credit_score, risk_profiles.internal_risk_score]
  cardholder_data: []        # none — a lending book holds no card data
  phi: []                    # none

frameworks: [gdpr, soc2]     # PCI and HIPAA would report "unbound" here

domain_regime:
  - regime: GLBA
    bindings:
      - {policy_section: "12.1", control_class: field_protection, data_class: personal_data}
      - {policy_section: "12.3", control_class: field_protection, data_class: personal_data}
      - {policy_section: "12.4", control_class: field_protection, data_class: personal_data}
      - {policy_section: "12.6", control_class: minimization}
      - {policy_section: "12.7", control_class: segregation}
      - {policy_section: "12.9", control_class: purpose_limitation}
      - {policy_section: "12.10", control_class: audit_logging}
  - regime: ECOA / FCRA
    bindings:
      - {policy_section: "13.6", control_class: human_oversight}   # notice in same session
      - {policy_section: "15.1", control_class: human_oversight}
      - {policy_section: "15.2", control_class: human_oversight}   # notice content — content obligation
      - {policy_section: "15.3", control_class: human_oversight}   # reason codes — content obligation
  - regime: CFPB AI guidance
    bindings:
      - {policy_section: "12.2", control_class: field_protection, data_class: financial_npi}
      - {policy_section: "13.7", control_class: integrity}
      - {policy_section: "16.4", control_class: audit_logging}
      - {policy_section: "18.5", control_class: monitoring}
      - {policy_section: "19.4", control_class: change_management}
```

(The column names are the demo's own — `demos.ts`'s `sensitiveFields` list
is the UI-side copy of the same set.)

### A.2 What the report would say, section by section

Rows marked *covered* reuse `check-coverage.md`'s mapping verbatim.

| policy § | control class | check ids | evidence today |
|---|---|---|---|
| 12.1, 12.3, 12.4 SSN / tax id / bank account never disclosed | `field_protection` | `field_restriction`, `field_scope` | covered (`F1-04`, `F3-05`) — shadow: *would have blocked* |
| 12.2 raw score restricted, tier substituted | `field_protection` | `field_restriction`, `field_scope`, `substitution` | covered (`F1-07`, `F1-12`, `F3-05`) |
| 12.5 internal risk score never disclosed | `field_protection` | `field_restriction` | covered (`F1-03`) |
| 12.6 data minimisation | `minimization` | `filter_scope`, `param_discard`, `volume_scope` | covered (`F2-03`, `F3-06`, `F3-07`, `BASE-03`) |
| 12.7 segregation of identity / credit / export | `segregation` | `toxic_combination` | covered (`F3-08`) |
| 12.8 document content is untrusted | `injection_resistance` | `param_taint` | covered (`F2-04`, `F2-04R`) |
| 12.9 purpose limitation | `purpose_limitation` | `goal_alignment` | covered (`F3-09`) |
| 12.10 log every sensitive-field access with purpose; keep 7 years | `audit_logging`, `retention` | — | **stated obligation, no evidence** — no purpose field on a span; no retention (§5.1) |
| 13.2–13.8 sequencing, staleness, approval, notice, faithful figures, correct subject | `human_oversight`, `integrity` | `precondition`, `sequencing`, `param_staleness`, `approval_gate`, `approval_evidence`, `workflow_integrity`, `error_blindness`, `param_mutation`, `param_provenance`, `entity_consistency` | covered (`F1-01`, `F1-05`, `F1-10`, `F2-01/02/05/06/09`, `F3-10`, `BASE-01/02`) |
| 15.1 adverse-action notice obligation | `human_oversight` | `workflow_integrity` | covered (`F3-10`) |
| 15.2–15.4 notice content, reason codes, timing | `human_oversight` | — | **stated obligation, no evidence** — content obligations (§5.7) |
| 16.2 immutability of the committed decision record | `audit_logging` | — | **no evidence** — §5.4 |
| 16.3 / 16.4 session logs; agent logged separately from the human | `audit_logging` | — | partial: `session.id` / `user.id` on every span; **agent not a separate principal** (§5.5) |
| 18.1 audit read access | `access` | — | no operator identity (§5.5) |
| 18.2 seven-year retention + legal hold | `retention` | — | **no evidence** — §5.1 |
| 18.5 quarterly AI System Compliance Review | `monitoring` | all of the above | **this is literally the report Layer A + B would generate** — "did the AI follow the rules, stay within its permitted scope, and match the system of record" is §4's three columns |
| 19.2 / 19.4 / 19.5 change log; AI rules updated before effective date; version control | `change_management` | — | partial: `rule_audit_log` + `rule_pack_version` / `catalog_version` on every verdict; no link from a policy version to an artifact version |

The pattern to take from this appendix is the shape of the last column, not
its contents: a deployment's report is a list of its own clauses, each either
*evidenced by these checks* or *a stated obligation Prefront cannot yet see*
— and the second list is the roadmap.
