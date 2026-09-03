# Per-application isolation — design proposal

**Status: PROPOSED, not built.** Written in response to "we need app-specific
views; each one will have its own configs, policies, oob or inline mode
properties; loanpro and securebank are 2 different apps, their data need
isolation."

This supersedes nothing. It gives `TODO.md` entry 8 ("Per-APPLICATION settings")
the boundary object it says is missing, and answers its first design question —
*"What identifies an application?"* — which blocks entries 9, 11 and 17 too.

---

## 1. The problem, measured

**"Application" is not a concept in this system.** It is approximated three
different ways that do not line up, and one whole layer has no notion of it at
all.

| layer | what scopes data | evidence |
|---|---|---|
| UI / api-server | a `demo` **string column** | `decisionTrace.ts:18`, `decisionStat.ts:14`, `severityRule.ts:18` (PK `(demo, ordinal)`), plus `demos.ts`, `?demo=`, per-demo `localStorage` |
| semantic-layer | a `datasource_id` | `datasources` table (`store.py:55`), artifacts at `/artifacts/<datasource_id>/` |
| semantic-mcp-server | one process per app | isolated by deployment: `securebank-mcp` / `loanpro-mcp`, each with its own `POLICY_PATH` |
| **eval-engine** | **nothing** | `eval_verdicts` has 20 columns and none identifies an app; 0 of 14 `/eval/*` endpoints take an app filter; exactly one `EVAL_RULE_PACK_PATH` / `EVAL_INTENT_CATALOG_PATH` / `EVAL_COMPLIANCE_OVERLAY_PATH` / `EVAL_TRACE_BINDING_PATH` per deployment (`config.py:40-55`); `eval_settings` holds **one** deployment-wide row |
| **oob-ingest** | **nothing** | `spans` has `project`, `service`, `session_id` — no app id |

The consequence is already written down as a known limitation in
`prefront-ui/CLAUDE.md:103`:

> *"`/oob/*` and `/eval/*` are not demo-scoped, so re-enabling SecureBank
> alongside LoanPro would mix demos on this page (out of scope)."*

That is exactly the isolation failure to fix. Today it is masked only because
one demo produces traces at a time.

**Mode is in the same state.** `EVAL_MODE` exists (`config.py:71`) but is
cosmetic — it is reported as `mode`/`shadow` in `/eval/status` and
`/eval/compliance` (`api.py:142,195-196`) and drives no behaviour — and it is
deployment-wide. Mode is now genuinely a **per-application** property:

| app | inline (in-band) | out-of-band |
|---|---|---|
| LoanPro | yes — `loanpro-mcp` proxying `loanpro-app-mcp`, driven by `loanpro-governed` | yes — `loanpro-ungoverned` taps to oob-ingest; the 39-scenario grading baseline |
| SecureBank | yes — `securebank-mcp`, driven by `securebank-orchestrator` | **no** — no OTLP tap, no scenario catalogue, no eval artifacts until this branch |

A single `EVAL_MODE` cannot describe those two at once.

---

## 2. Why this is also the answer to the Policy Studio / Business Graph question

The separation complaint that started this is a symptom. Neither tab should own
application identity — but because nothing else does, each view improvises:

- `BusinessGraph.tsx:784-802` and `DataGraph.tsx:921-941` each hold their own
  `bound` state, their own `getPolicy(datasourceId)` effect and their own
  `buildPolicyIndex(catalog, rules, bound)` — **byte-identical copy-paste**.
  Two consumers independently re-deriving one app's policy state.
- `PolicyStudio.tsx:18,31` declares and destructures `setIntents` and **never
  calls it**; only `Semantic.tsx:216` edits intents. The type asserts an
  ownership that does not exist.
- `policyIndex.ts` mixes policy semantics (`DECISION_LABEL`, `DECISION_SEV`,
  `deriveRoles`, `canonRole`) with a cosmetic name→icon heuristic
  (`deriveKind`, lines 35-43) that hardcodes `loan|credit|mortgage`,
  `account|wallet|balance`, `transaction|payment|ledger` — contradicting
  `demos.ts:1-6`, which claims to be the single home for demo vocabulary.
- Root `CLAUDE.md:105` states the pipeline as *Data Connector → Data Graph →
  Business Graph → Policy Studio*; the actual order (`App.tsx:43-56`) is the
  reverse for those three, and the code is right — both graphs consume Policy
  Studio's rules.

Once an application is a real object, each tab becomes "this view, **for the
selected app**", and the improvisation has one place to live.

---

## 3. The boundary object

An **Application** is the unit of isolation. One registry entry declares
everything that is currently spread across compose files, env vars and a UI
constant:

```yaml
application:
  id: loanpro                 # the scope key, everywhere
  label: LoanPro
  modes: [inline, oob]        # what this app actually runs
  datasource_id: loanpro-demo # -> /artifacts/<id>/ (semantic-layer)
  telemetry:
    phoenix_project: loanpro  # the ingestion partition (see §4)
  artifacts:                  # today: one env var per deployment
    rule_pack:        rule_pack.yaml
    intent_catalog:   intent_catalog.yaml
    compliance_overlay: compliance_overlay.yaml
    trace_binding:    ""      # "" = the bundled default profile
  checks:
    disabled: []              # TODO entry 8's per-app set
```

Three properties this must have, carried from existing rules:

1. **It is an artifact, not engine code.** An application id, its tables and its
   roles are domain vocabulary — Hard Rule 1. `eval-engine/tests/
   test_domain_independence.py` must still pass with no new exemption.
2. **Its version joins the evaluation version key.** `evaluate.py:27,50-52`
   already composes `{ENGINE_VERSION}:{binding}:{visibility}:{skill}@{ver}:
   catalog@{ver}:checks@{ver}`. An app's config version belongs in that string,
   so editing it re-evaluates *that app's* sessions and strands nothing.
3. **Unknown ids are rejected at load**, never silently matched — same rule as
   `CheckSettings.from_ids`.

---

## 4. What identifies a session's application

Three candidates were checked against the live store:

| candidate | verdict |
|---|---|
| `service` prefix (`loanpro-*`) | **No.** A naming convention, not a contract; breaks the moment a service is renamed, and `orchestrator` carries no prefix at all (measured: 130 spans). |
| a new `app_id` column written at ingest | Necessary, but not sufficient on its own — something must still tell ingest which app a span belongs to. |
| **Phoenix `project`** | **Yes.** Already a column on every span; `tracing/prefront_tracing.py:121` already lets each service set `PHOENIX_PROJECT_NAME`; and oob-ingest **already polls a list** (`PHOENIX_PROJECTS`, `config.py:21`) keeping a per-project watermark in `ingest_state`. Today every service reports to one project (`prefront`), which is why the partition is unused, not absent. |

**Proposal: the Phoenix project is the transport partition; `app_id` is the
stored column.** Each demo compose sets `PHOENIX_PROJECT_NAME` to its own app;
oob-ingest maps project → `app_id` from the registry and writes it on every row.
Downstream code then filters one canonical column instead of parsing names, and
a deployment that never sets a project keeps working under a single default app
(the degrade-safe path, Hard Rule 9).

This also isolates at the **source**: two apps' traces stop sharing a Phoenix
project, so the mixing `prefront-ui/CLAUDE.md:103` warns about cannot occur even
before the column exists.

---

## 5. Phased change

Each phase is independently shippable and leaves the system working.

**Phase 1 — partition the telemetry.** Set `PHOENIX_PROJECT_NAME` per demo
compose. Add `app_id` to `spans` via the existing self-healing
`ALTER TABLE … ADD COLUMN IF NOT EXISTS` convention (`ch.py`), populated from the
project. Add an optional `app` filter to `/oob/*`. No eval-engine change yet.

**Phase 2 — scope the verdicts.** Add `app_id` to `eval_verdicts`,
`eval_conformance_tags`, `eval_evaluated_sessions` (same convention), resolved
from the session's spans. Add `app` to the `/eval/*` read endpoints. Verdicts
written before this are `""` — reported as "unattributed", never silently folded
into an app.

**Phase 3 — per-app configuration.** Replace the single-valued
`EVAL_*_PATH` env vars with a registry lookup, keeping the env vars as the
one-app default so existing deployments are unaffected. Make `eval_settings`
per-app (`TODO` entry 8's core ask). Fold the app config version into the
evaluation version key.

**Phase 4 — the UI.** One app selector (the existing `?demo=` becomes `?app=`),
and every tab scoped by it. This is where the Policy Studio / Business Graph
cleanup lands: a single `useApplication(appId)` owning catalog + rules + bound
bundle + mode, replacing the two copy-pasted `getPolicy` effects; `deriveKind`'s
vocabulary moved into the registry; `setIntents` dropped from Policy Studio.
Tabs render mode-appropriately — an inline-only app shows no shadow-evaluation
panels, an OOB-only app shows no Decisions view.

**Phase 5 — retire the `demo` column** in favour of `app_id`, or keep `demo` as
its alias. Decided last, when the two vocabularies are known to agree.

---

## 6. Open decisions — these need answering before Phase 2

1. **Is "application" the same thing as "datasource"?** Today
   `datasource_id=loanpro-demo` and `demo=loanpro` are different strings for
   nearly the same thing. One app with two datasources is plausible; two apps
   sharing one datasource is also plausible. If they are distinct, the registry
   needs both keys — and every `/artifacts/<datasource_id>/` path stays keyed by
   datasource, not app.
2. **What happens to an unattributed session** — one whose spans carry no
   project mapping? Proposed: a reserved `""` app that is reported, never
   merged. It must not read as "clean".
3. **Does an app's mode gate evaluation, or only presentation?** An inline-only
   app produces no traces, so OOB is vacuous for it either way; but an app
   declaring `modes: [inline]` that *does* emit traces should probably still be
   evaluated, and the mode should shape the UI rather than suppress evidence.
4. **Does per-app check enablement replace or nest under the deployment-wide
   set?** `TODO` entry 8 leaves this open; two independent switches with
   unclear precedence is the worst outcome.
5. **Multi-app in one Phoenix project.** The proposal assumes one project per
   app. A customer who cannot partition their collector needs a fallback —
   probably a span-attribute binding in `trace_binding.yaml`, which is already
   the per-subject-app artifact.
