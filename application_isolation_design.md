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
deployment-wide. Mode is now genuinely a **per-datasource** property, rolled up
per application (§3):

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

**An application HAS MANY datasources, and the two stay separate concepts**
(settled; see §6.1). That is not a detail — it splits the artifacts into two
tiers, and the split falls exactly where the code already puts it:

| tier | artifacts | written / read by | evidence |
|---|---|---|---|
| **per-datasource** | `query_templates.yaml`, `policy.yaml` | semantic-layer writes them; one governed MCP instance serves ONE of them | `api.py:76,321` (`/artifacts/<datasource_id>/`); `--templates=/artifacts/<ds>/query_templates.yaml` in both demo composes |
| **per-application** | `rule_pack.yaml`, `intent_catalog.yaml`, `compliance_overlay.yaml`, `trace_binding.yaml` | eval-engine reads them — today as one env var per DEPLOYMENT | `config.py:40,46,47,54` |

The reason the tiers differ: a query template binds an intent to **one** schema
or tool surface, so it can only be per-datasource. A rule pack and an intent
catalog describe the **agent's** approved behaviour, and one agent may reach
across several datasources in a single session — so they are per-app, and their
entries name tools that may resolve to different datasources.

```yaml
application:
  id: loanpro                 # the isolation key, everywhere
  label: LoanPro
  telemetry:
    phoenix_project: loanpro  # the ingestion partition (see §4)

  # Per-APP: the agent's policy and approved-intent surface. One each,
  # spanning every datasource below. Today these are single-valued env vars.
  artifacts:
    rule_pack:          rule_pack.yaml
    intent_catalog:     intent_catalog.yaml
    compliance_overlay: compliance_overlay.yaml
    trace_binding:      ""    # "" = the bundled default profile
  checks:
    disabled: []              # TODO entry 8's per-app set

  # Per-DATASOURCE: how the agent reaches each one, and what Prefront
  # publishes for it. `mode` lives HERE, not on the application — see §6.2.
  datasources:
    - id: loanpro-demo        # -> /artifacts/loanpro-demo/{query_templates,policy}.yaml
      mode: inline            # a governed MCP sits in front of it
      runtime: http://loanpro-mcp:8090/sse
    - id: loanpro-warehouse   # illustrative: same app, second datasource
      mode: oob               # observed only; no governed MCP
```

The application's own `modes` is then **derived** — the union of its
datasources' — not declared. One less place to disagree with itself.

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

**Phase 5 — rename `demo` to `app_id`.** Simplified by §6.1: `demo=loanpro` was
always an application label and `datasource_id=loanpro-demo` a datasource one,
so this is a rename with no merge and `datasource_id` is untouched. Last,
because it is the only step that breaks a stored value.

---

## 6. Decisions

### 6.1 SETTLED — an application has many datasources; the two stay separate

Confirmed. Consequences, all of which simplify rather than complicate:

- **The registry carries both keys**, and `/artifacts/<datasource_id>/` stays
  keyed by datasource — no path moves, and semantic-layer needs no change to
  where it writes.
- **Artifacts split into two tiers** along a line the code already draws
  (§3): query templates and the bound policy bundle are per-datasource because
  they bind to one schema or tool surface; the rule pack, intent catalog,
  compliance overlay and trace binding are per-app because they describe the
  agent, which may cross datasources within a single session.
- **`app_id` is the isolation key on spans and verdicts, not `datasource_id`.**
  A session is an agent's, and an agent belongs to an application. Recording a
  datasource per *tool call* is a later refinement (§6.5), not part of the
  isolation work.
- **Phase 5 gets simpler.** `demo=loanpro` was always an application label and
  `datasource_id=loanpro-demo` a datasource one; they were never two names for
  one thing. So `demo` → `app_id` is a rename with no merge, and
  `datasource_id` is untouched.
- **One app may run several governed MCP instances** — one per inline
  datasource, since each serves exactly one `query_templates.yaml`.

### 6.2 NEW, created by 6.1 — is `mode` per-datasource or per-application?

Asked as an application property ("its own ... oob or inline mode properties"),
but once an app has several datasources the honest answer is that an application
does not *have* a mode: **Prefront either does or does not sit in the access
path, and there is one access path per datasource.** An app could reasonably
govern its own Postgres inline while only observing a third-party MCP
out-of-band.

Proposed (and drafted in §3): declare `mode` on the datasource, derive the
application's `modes` as the union. Strictly more expressive, collapses to the
asked-for answer when an app has one datasource, and avoids two declarations
that can contradict each other. **This is the one place the design extends the
instruction rather than implementing it — worth a yes/no before Phase 1**, since
it decides where the field lives.

### 6.3 Open — what happens to an unattributed session?

One whose spans carry no project mapping. Proposed: a reserved `""` app that is
reported, never merged into a real one. It must not read as "clean" — the same
distinction `TODO` entry 8 draws between "passed" and "never checked".

### 6.4 Open — does mode gate evaluation, or only presentation?

An inline-only app produces no traces, so OOB is vacuous for it either way. But
an app declaring `mode: inline` that *does* emit traces should probably still be
evaluated, with mode shaping the UI rather than suppressing evidence. Suppressing
evaluation because of a declaration is how an engine goes quietly blind.

### 6.5 Open — does per-app check enablement replace or nest under the deployment-wide set?

`TODO` entry 8 leaves this open. Two independent switches with unclear
precedence is the worst of the three outcomes. Related: whether a *tool call*
records which datasource it hit, which only matters once an app has several.

### 6.6 Open — multi-app in one Phoenix project

The proposal assumes one project per app. A customer who cannot partition their
collector needs a fallback — probably a span-attribute binding in
`trace_binding.yaml`, which is already the per-subject-app artifact.
