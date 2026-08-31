# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

prefront-ui is the front-end of the Prefront engine. The parent `../CLAUDE.md`
covers the whole platform (services, the governance pipeline, the OOB
*pipeline*); this file is **UI-specific** — component layout, what each tab
renders, and the behaviour decisions behind them.

Package/workspace shape (pnpm workspace, the `verdict` second app, the shared
`lib/`s, the typecheck-in-Docker recipe) is in the parent's "UI layout" and
"Commands › UI dev" sections — not repeated here.

## Tab architecture (`artifacts/prefront-app/src/`)

`App.tsx` **derives** the active tab from the URL — `tabFromPath(useLoc().segs)`, not a `useState` (it held one until routing was added). `TABS` is still the source of truth for nav order. All tab bodies are mounted on first visit and toggled via `tab-hidden` CSS (not unmounted), so tab state survives navigation. Current nav order: **Overview → Data Connector → Policy Studio → Business Graph → Data Graph → Semantic Layer → Decision Traces → Intent Flows → Observability**. `completedTabs` in `App.tsx` drives the progress indicators (checkmarks).


## Routing & shareable links (`lib/router.ts`, `routes.ts`, `components/CopyLink.tsx`)

Every page is directly viewable by URL, and every artifact worth showing
someone has a **Copy link** button. Three small files, no router library.

- **`lib/router.ts` is a URL *mirror*, not a rendering authority** — `useLoc()`
  (a `useSyncExternalStore` over a module-level snapshot read at import time,
  plus a `popstate` listener), `navigate`, `buildHref`, `setParams`, `absUrl`.
  `wouter` sat unused in devDependencies and was removed: its value is
  `<Route>` conditional rendering, which would **unmount** tabs and destroy the
  keep-everything-mounted design above. The snapshot is read synchronously at
  module init, so a deep link never flashes the Overview first.
  `navigate` **no-ops when the target equals the current URL** — that one guard
  is what stops a URL → state → URL cycle from looping.
- **History policy, one rule**: `pushState` when the *path* changes (tab,
  sub-view, opening/closing an artifact); `replaceState` for app-performed
  corrections (Policy Studio's first-doc auto-select, dropping a stale node id,
  canonicalising `/traces` → `/traces/findings`, `?span=`).
- **`routes.ts` is the grammar**, shared by navigation and `CopyLink`:

  ```
  /  /data  /policy  /business-graph  /data-graph  /traces  /flows
  /observability  /settings  /semantic          (the last still has no nav entry)
  /traces/findings/{session_id}?event=&span=    ← a finding, the shareable "event"
  /observability/{overview|sessions|traces|llm|ingestion}
  /observability/sessions/{session_id} · /observability/traces/{trace_id}?span=
  /data-graph/{table or MCP tool name} · /business-graph/{ent-|proc-|role-|gov-…}
  /policy/{document_id}?tab=&rule={rule_key}
  ```

  Path = what you are looking at; query = the ids that qualify it. Filters,
  searches, time ranges and pagination stay in memory **by design** — a link
  reproduces a page and an artifact, not a transient view of a table.
- **The Observability tab's path is `/observability`, never `/oob`.** nginx
  proxies `/oob/` with a trailing slash, but Vite's dev proxy matches the
  **bare** prefix with `startsWith` — a route named `/oob` would work in prod
  and proxy to oob-ingest in dev. `routes.ts` asserts on the reserved list
  (`/api /design /oob /eval /pii /assets`) in DEV so a future rename can't
  reintroduce that.
- **Three traps that every path-derived value has to respect**, all of them
  consequences of tabs staying mounted:
  1. **`onTab(segs, tabId)` gates every derivation.** A component reading
     `segs[1]` unguarded reads *another page's* artifact id — the Data Graph
     saw `"sessions"` while you were on `/observability/sessions/<id>` and
     "corrected" the URL out from under it.
  2. **A mounted-but-hidden tab must not WRITE the URL either.** Policy
     Studio's document auto-select fired as soon as its document list resolved
     and navigated to `/policy/<id>` from whatever page you were actually on;
     both its writers now check `onTab(currentLoc().segs, "policy")` first.
  3. **Never strip an id before its data has loaded.** Both graphs revalidate
     the selected node against the rebuilt graph — guarded on `built.defs.length`,
     or a cold deep link erases its own node while the catalog is still loading.
     Same rule for the eventual-consistency case: an unresolvable session id
     stays in the URL and the page self-heals.
- **`graphMounted`/`bizGraphMounted` flip in an effect on the derived tab**, not
  in the sidebar's `onClick` where they used to live — otherwise `/data-graph`
  renders blank on a deep link or a Back/forward.
- **A finding is addressed by `session_id`, not `event_id`.** `event_id` is not
  server-resolvable: `/eval/verdicts` takes only
  `status|check_id|family|limit|offset|since`, and the only by-id endpoint is
  `/eval/sessions/{sid}/verdicts`. `useLinkedFinding` (`DecisionTraces.tsx`)
  resolves in three tiers — the newest-1000 rows the table already has, then
  that per-session endpoint matched on `event_id`, then the session alone with
  no finding banner.
- **`?demo=` rides on every link.** The same path renders different content per
  demo (`/api/*` is demo-scoped, localStorage caches are namespaced), so
  `CopyLink` always injects it and `DemoContext.loadDemoId` reads it **before**
  localStorage, returning `chosen: true` so the first-run chooser overlay
  doesn't swallow a shared link. Answering that chooser is not a *switch*, so
  it keeps the deep path; actually switching demos cuts the path back to the
  tab, since an artifact id from one demo is meaningless in the other.
- **`App.tsx` remembers the last URL per tab** (`lastPath` ref, cleared on demo
  switch) so clicking the sidebar returns you to the sub-view/artifact you left
  — the behaviour tab-state-survives-navigation gave for free before the
  sub-view lived in the path.
- **`CopyLink` copies an ABSOLUTE URL and needs its non-clipboard fallback.**
  `navigator.clipboard` is undefined on a non-secure origin and this app is
  served over plain HTTP on :5173 — which is why `PolicyStudio.tsx`'s "Copy
  log" silently does nothing there. Falls back to a hidden textarea +
  `execCommand`, then `window.prompt`. It also `stopPropagation()`s, since rows,
  cards and graph nodes are themselves clickable. It renders in the page header,
  the Overview hero, every findings row, the session flyout, trace detail,
  session detail, both graph detail panels, and each rule card.

**`Overview.tsx` (the "dashboard" tab) is a buyer-facing dashboard with five plain-titled sections** — Decisions before execution / Rules applied / Business-context controls / Decision context / Decision evidence (copy in its `SECTIONS` const). Each maps to one buyer concern from the positioning doc (Head of AI "is this another layer?", CIO "too many governance tools", CISO "IAM already answers who can access what", Data Governance "we already have catalogs", Compliance "evidence gathering is manual") but reads as a dashboard, not a pitch — a first cut used the quoted objections as headings and was rejected for reading like a sales script. Each section is backed by **live data only** from `hooks/useOverviewData.ts`: eval-engine's shadow evaluation (`/eval/status` for the hero totals and rule-pack/catalog coverage, `/eval/findings?limit=500` grouped by effect/rule/family, and the new `GET /eval/conformance` for cross-session positive evidence) plus oob-ingest (`/oob/overview`, `/oob/sessions` for off-catalog calls and distinct intents). It replaced `Dashboard.tsx`, which read only the `decision_*` store (`useDecisionFeed`, `/api/*`) — **structurally empty for LoanPro**, since `api-server`'s `toInsert` (`routes/decisions.ts`) requires a `governed` key LoanPro's ungoverned orchestrator never emits, so every panel showed zeros and the "populate from the demo" button both timed out (115 s abort, 34 live-LLM scenarios) and inserted nothing. That store now backs only Decision Traces › Decisions and a conditional "Governed runtime" section at the bottom of the Overview, rendered **only when `/api/stats.total > 0`** — never a zero-panel implying inline enforcement that didn't happen; the populate button is gone from the UI (`populate()` stays in the hook for API completeness). Truthfulness rule the page is built on: LoanPro is ungoverned, so every finding is labelled shadow evaluation — "what Prefront would have decided before execution" — and nothing is ever called "blocked". Drill-ins: the hero Findings counter and the effect tiles open Decision Traces › Findings (the effect tiles prefiltered — `App.tsx` lifts `tracesSection`/`findingsEffect` state and `DecisionTraces` takes `section`/`onSection`/`findingsEffect` props, `FindingsSection` an `initialEffect`); evidence rows open the same `SessionFlyout` the Findings table uses. The one check-vocabulary special case (`entitlement` = the only role-only check, everything else "business context IAM has no concept of") is flagged in `useOverviewData.ts`; sensitive-field names come from `demos.ts`, never the component — `grep -in "loan\|applicant\|underwrit"` over `Overview.tsx`/`useOverviewData.ts` must stay empty. `Observability.tsx` now exports its formatters (`num`/`ms`/`pct`/`ago`), `getJSON`/`qs`, `Kpi`/`Bars`/`Empty`, and the `Overview`/`Status`/`SessionRow`/`PopulationRow`/`EvalStatus` types for it. Caveat: `/oob/*` and `/eval/*` are not demo-scoped, so re-enabling SecureBank alongside LoanPro would mix demos on this page (out of scope). `DecisionTraces.tsx` is the filterable decision log (Decisions | Findings sub-nav).


**`DataGraph.tsx` renders two different things**, branching on
`sourceType === "mcp" && mcpTools.length > 0`. A Postgres source gets the
original table/relationship map (`buildFromCatalog` → `GraphTableNode` →
`DetailPanel`, dagre LR layout, FK edges). An **MCP** source gets a tool view
(`buildFromMcpTools` → `GraphToolNode` → `ToolDetailPanel`, plus `McpStatsBar`
and `McpLegend`) fed by `schema.mcpTools` — the full `mcp_tools` record from
`/design/semantic/mcp/introspect`, NOT `schema.catalog`, whose one-table-per-tool
projection carries only input properties. Three things to keep in mind:

- **Edges are SHARED ARGUMENTS.** MCP tools have no foreign keys, but two tools
  taking the same parameter are operating on the same thing — `loan_id` across
  7 of LoanPro's tools is the closest an MCP catalog gets to a join key, and
  it is the governance-relevant fact (a caller holding that id reaches all of
  them). One edge per PAIR, not per (pair, param): two tools sharing two
  arguments are one relationship carrying both names. A parameter used by
  exactly one tool connects nothing and is skipped — on LoanPro that leaves 4
  shared params of 17, and 37 edges. Colours are assigned most-connected
  first, so the keys that actually structure the server get the distinct hues,
  and `McpLegend` maps colour → param → tool count.
- **Layout depends on whether edges exist.** With edges, dagre LR, same as the
  Postgres view. With none, masonry — dagre puts every edgeless node in one
  rank, so a 19-tool server with no shared arguments would render as a single
  unreadable column; each card goes to the shortest column instead.
- **"Undeclared" must never render like "empty".** Most MCP servers declare no
  `outputSchema`; the node shows a hatched *no output schema declared* note and
  the panel says so in words, and `McpStatsBar` surfaces the count as a
  first-class "Undeclared out" stat. Behaviour annotations are tri-state the
  same way — `HintChips` lists which hints the server actually declared and
  names the ones it didn't.
- `PolicySection` is shared by both detail panels: the policy index is keyed by
  name and an MCP tool's name IS its catalog table name, so both resolve rules
  identically. `App.tsx` swaps the Data Graph subtitle for MCP sources, since
  one constant would describe the wrong view half the time.

## Observability & Findings (the `/oob/*` + `/eval/*` surfaces)

The ingestion pipeline itself (two sources/one table, `FINAL`, the exclusion
rules, the NaN and ClickHouse-alias traps) is in the parent CLAUDE.md's "OOB
observability" section. What follows is the UI over it.
- UI: `components/Observability.tsx` (tab id `oob`; views Overview / Sessions / Traces /
  LLM / Ingestion), CSS under `.pf-oob-*`. Findings queries eval-engine's `/eval/*`
  (autonomous_build.md step 16), a separate nginx `location /eval/` block (and
  `VITE_EVAL_TARGET` dev proxy) alongside the existing `/oob/` one; Verdict's
  `SessionDetail.tsx` and `verdict-nginx.conf` carry the same addition (its own
  hand-curated CSS copy already had the `.pf-oob-chip.red/green/amber` tone
  classes). Vite dev proxies `/oob` → `VITE_OOB_TARGET` (default `http://localhost:8110`).
- **Findings lives in `components/DecisionTraces.tsx` now, not Observability** -
  a `FindingsSection`, toggled via a "Decisions | Findings" sub-nav at the top
  of that tab (`.pf-oob-view`/`.pf-oob-views`, reused from Observability's own
  tab-bar styling), moved there because findings are a governance-decision-log
  concept like decision traces are, not an observability-pipeline-health one.
  `Observability.tsx` exports `EvalVerdict`/`ConformanceTag`/`STATUS_TONE`/
  `SessionFlyout` for `DecisionTraces.tsx` to import (`SessionDetail` was
  already exported) - same app, so sharing code across this file boundary is
  fine, unlike the deliberate non-sharing between `prefront-app` and `verdict`.
  Every displayed column is filterable, PLUS two that are filterable but
  deliberately not shown as columns - Check and Policy section still have
  their own dropdowns (the check/policy `useMemo`s and `<Select>`s never
  went away), they're just not rendered per-row any more, per explicit
  request to narrow the table. Columns, left to right: Event, When, Family,
  Effect, User query, What went wrong. `session_id` is not a column either
  (only used internally to open the flyout). Fetches the most recent 500
  findings once (server-sorted, the endpoint's own cap) and filters/
  paginates entirely client-side, same pattern the Decisions log above it
  already used, rather than a filter query param per column.
- **Findings show family display names, not `family1/2/3`** - eval-engine
  stamps a `family_label` on every verdict/finding read (`contract.FAMILY_LABELS`,
  applied in `ch.rows()`): `family1` → **Policy**, `family2` → **Integrity**,
  `family3` → **Conformance**. The stored `family` value is unchanged (renaming
  a persisted column orphans ReplacingMergeTree rows); the label is derived at
  read time and is a category noun rather than an outcome word, because the
  same label appears next to `satisfied` verdicts. `DecisionTraces.tsx`'s
  `famOf()` renders and filters on it (falling back to the raw value), and
  `useOverviewData.ts`'s `byFamily` groups on it. Not to be confused with the
  demo's SCENARIO families (`F1/F2/F3/POP/BASE`) that Verdict's `SessionRunner`
  and OOB's `SessionRow.family` use - a separate vocabulary.
- **"User query" is the session's first user turn** - joined in from the
  shared `spans` table at READ time, not a stored `eval_verdicts` column:
  `ch.py`'s `_first_user_messages(session_ids)` (eval-engine) runs one extra
  ClickHouse query per `list_findings` call (`argMinIf(substring(input_value,
  1, 240), start_time, name LIKE 'turn %%')` grouped by `session_id` - the
  literal `%` in `LIKE 'turn %%'` needs the double-`%%` because
  clickhouse_connect's client-side parameter binding runs the SQL string
  through Python `%`-formatting; a single `%` there raised `ValueError:
  unsupported format character` in a live run before this was caught), same
  pattern oob-ingest's own `first_input` column already uses for its Sessions
  view, kept as an independent copy rather than shared code (separate Docker
  build context, the established convention). Empty string, not an error,
  for a session with no turn spans (yet, or ever).
- **Every column is width-capped (`table-layout: fixed`, `.pf-find-table`
  `nth-child` widths) with the full value on hover** (a native `title`
  attribute, not a custom tooltip component) - a long user query or a long
  `detail` sentence truncates with an ellipsis instead of blowing out the
  table. `WhatWentWrong` (the "What went wrong" cell) now renders ONLY
  `Verdict.detail`, truncated the same way - the policy citation + verbatim
  quote that used to render underneath it in this cell moved to the flyout's
  top summary exclusively (`SessionDetail`'s `findingDetail`/`findingSource`,
  see below), per explicit request to keep this column to "just the brief
  one-liner." Family 1 always has a quotable `source.text` there (Hard Rule
  17); Family 3 has a `section` with no quotable text (the intent catalog
  only carries section numbers); Family 2 has neither.
- **Clicking a Findings row opens a `SessionFlyout`**, a slide-in side panel
  (`.pf-flyout*` CSS, ported into `prefront-app/src/index.css` from Verdict's
  `SessionRunner.tsx`/`index.css` — no shared code between the two apps, so
  keep both copies in sync by hand) wrapping the SAME `SessionDetail` used by
  Observability's Sessions view, rather than navigating away. It pre-selects
  the finding's own offending span (`SessionDetail`'s `initialSpanId` prop,
  from `evidence_span_ids[0]`) so the SpanInspector's raw input/output is
  visible immediately, no extra click, and shows the finding's `event_id` in
  the flyout header for reference. Escape and a backdrop click both close it;
  "jump to trace" (`onOpenTrace`, threaded from `App.tsx` as
  `onOpenObservability={() => setTab("oob")}`, since Decision Traces has no
  Traces view of its own to deep-link into) switches to the Observability tab.
  Live-verified in a real browser, from both Findings' new home and (before
  the move) the old one.
- **`SessionDetail`'s layout is reordered so the flyout leads with the
  finding, not the session.** Two new optional props, `findingDetail`/
  `findingSource` (the clicked row's `detail` + raw `source` JSON, threaded
  `FindingsSection` → `SessionFlyout` → `SessionDetail`) render a one-liner +
  policy-quote block (`.pf-find-flyout-top`, reusing the same
  `.pf-find-quote`/`.pf-find-cite` markup `DecisionTraces.tsx`'s table cell
  uses - `parseSource`/`PolicySource` moved from that file into
  `Observability.tsx` and are exported, so both places share one parser) as
  the FIRST thing in the panel - only set by the Findings call site;
  Sessions/Traces (no single "this is the finding" to lead with) skip it.
  The flyout reads as a narrative, top to bottom: a **conversation block**
  (`.pf-oob-convo`, "User asked" / "Agent answered" per turn, from the same
  turn/answer steps `stepsOf` orders), then a **"What was wrong"** block
  (`.pf-find-flyout-top`: the finding's one-liner + policy quote/reference —
  this used to sit at the very top, above the conversation, and was moved
  below it per explicit request), then the collapsed trace. All always visible — a finding can't be judged
  without seeing what was asked and what came back, and the trace is
  collapsed. Multi-turn sessions get a `turn N` separator per pair.
  The step-by-step trace is now a collapsed-by-default `<details>`
  (`.pf-oob-steps-collapse`, "▸ Full trace (N steps)") instead of always
  expanded - collapsing it does NOT hide the `SpanInspector` for a
  pre-selected span, so the finding's own evidence is still visible without
  expanding anything. Session id, trace id(s), role/channel/turn counts, and
  the "checks triggered"/"actual verdicts" chip rows all moved into a new
  `.pf-oob-detail-footer` below the trace - secondary context now, not the
  first thing you see. This reorder applies to EVERY `SessionDetail` call
  site (Sessions view included, not just the flyout) - one component, one
  layout; live-verified both ways: a Family 1 finding's flyout shows its
  one-liner + verbatim policy quote up top and the collapsed trace/footer
  below, and Observability's Sessions view still renders correctly with
  the same reordered layout, trace collapsed there too.
- **The footer's "checks triggered"/"actual verdicts" rows were themselves
  cluttered** - a conformance tag's chip used to inline its FULL
  comma-separated policy section list (`entitlement · §7.3, 13.2, 13.3, 13.4,
  13.7, 13.8 ✓`), repeated near-verbatim across every satisfied check tied to
  the same intent, wrapping into a wall of text (a real session showed 24
  conformance tags this way). Fixed to match the rest of this file's
  hover-for-detail convention: every chip is now `check_id ✓` (or
  `check_id · status` for a violated/indeterminate one), with the full
  `check_id · §section` (+ clause text, when there is one) moved to its
  `title`. The satisfied ones (`tags`, always green, often many) are further
  collapsed behind a `<details>` ("▸ N checks satisfied",
  `.pf-oob-footer-collapse`, same disclosure pattern as the trace) - the
  violated/indeterminate ones (`verdicts` filtered to `status !== "satisfied"`,
  now named `violated` in the component) stay directly visible, since those
  are the actionable signal; a clean session has nothing to hide behind a
  toggle. The two long inline "← checks this scenario is built to
  trigger..." / "← eval-engine's actual verdicts..." caption sentences
  became a single small `CHECKS` label (`.pf-oob-footer-label`) with the
  same explanation as its `title`. `tools.length` ("N tool calls") was
  dropped from the session summary line entirely, per explicit request -
  redundant with the (collapsible) full trace, which already shows every
  tool call. Live-verified: the same session that prompted this (24
  conformance tags, previously several wrapped lines of huge chips) now
  shows three compact chips + one "▸ 13 checks satisfied" toggle; hovering
  a collapsed chip still surfaces its full section citation.
- **The trace id/button are gone from `SessionDetail` entirely now** - first
  the passive "· trace(s) {ids}" summary-line text (purely redundant with a
  button showing the same id right below it), then the button itself
  (`onOpenTrace`, "jump to the raw OTEL span waterfall in Observability's
  Traces view") - per explicit request, since the full trace is already one
  click away in this SAME panel (the collapsed `<details>` above). Removing
  the button meant `onOpenTrace` was never invoked by anything in
  `SessionDetail` any more, so the whole now-dead plumbing chain came out
  too rather than leaving an orphaned prop: `SessionDetail`'s
  `onOpenTrace` prop, `SessionFlyout`'s (it only ever forwarded to
  `SessionDetail`), `SessionsView`'s (same), the
  `onOpenTrace={goTrace}` passed to `SessionsView` from the root
  `Observability` component (`goTrace` itself stays - `TracesView`/
  `LlmView` still use it independently), `DecisionTraces`'s
  `onOpenObservability` prop, and `App.tsx`'s
  `onOpenObservability={() => setTab("oob")}` wiring - four files, one
  coherent removal. Also caught two variables in `SessionDetail` that had
  quietly gone dead in the PREVIOUS pass (`tools`/`short()`'s only callers
  were the already-removed "N tool calls" text and this button) and removed
  those too.
- **A real overflow bug, not just clutter: the Effect chip
  (`APPROVAL_REQUIRED`, the longest value) was wider than its column and
  visually overlapped the User Query text next to it** -
  `table-layout: fixed` caps a cell's own box but does nothing to a child
  element wider than that box, and no cell had `overflow: hidden` to clip
  it. Fixed both ends: `.pf-find-table td { overflow: hidden }` as a general
  clip (defends against any future case), and the Effect column widened
  from 90px to 140px so `APPROVAL_REQUIRED` fits on one line in practice, rather
  than relying on the clip to hide it. Live-verified: filtered Findings to
  `effect=approval_required` and confirmed every row's chip now sits fully
  inside its own column with no overlap.
- **The Ingestion view's clear button (now labelled "Clear all trace data")
  also purges Phoenix.** Clearing ClickHouse alone was only a pause: oob-ingest
  re-pulls every span from Phoenix on its next poll and eval-engine re-evaluates
  them, so findings came back within a minute. `DELETE /oob/phoenix` (oob-ingest,
  `PhoenixPoller.purge`) now deletes every Phoenix project but `default` via
  Phoenix's own `DELETE /v1/projects/{id}` (a project is recreated on the next
  span it receives) and resets the poller's in-memory watermarks/seen-set/held
  orphans/aliases under the sync lock; the UI calls it FIRST, then the two
  ClickHouse truncates. After a clear the stack is genuinely empty until a new
  session runs, and event ids restart at 1.
- **"Clear ClickHouse" (the button's earlier name) clears ALL FIVE
  ClickHouse tables, not just oob-ingest's.** `DELETE /oob/spans`
  (`ch.truncate()`, oob-ingest) only ever cleared `spans`/`ingest_state` — it
  left eval-engine's `eval_verdicts`/`eval_conformance_tags`/
  `eval_evaluated_sessions` untouched, so Findings kept showing stale rows
  after a "clear". The button now also fires `DELETE /eval/verdicts`
  (eval-engine's own truncate-all, already existed for its own dev use) in
  parallel. Findings didn't self-refresh on this action either (no
  `refreshKey` wired to the old `FindingsView`) - moot now that Findings
  fetches its own page-load snapshot in `DecisionTraces.tsx`'s
  `FindingsSection` rather than reading Observability's shared `tick`. Both
  gaps found
  and fixed together; Phoenix is untouched by design (oob-ingest re-pulls from
  it), so `spans` repopulates on the next poll — that's a re-pull, not
  retention, and is what the confirm dialog now says explicitly.
