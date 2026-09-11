import { useCallback, useEffect, useRef, useState } from "react";
import Overview from "./components/Overview";
import { DEMOS } from "./demos";
import DecisionTraces from "./components/DecisionTraces";
import RuntimeDiff from "./components/RuntimeDiff";
import ScenarioRunner from "./components/ScenarioRunner";
import LearnedIntents from "./components/LearnedIntents";
import IntentFlows from "./components/IntentFlows";
import PolicyStudio from "./components/PolicyStudio";
import DataConnector from "./components/DataConnector";
import DataGraph from "./components/DataGraph";
import BusinessGraph from "./components/BusinessGraph";
import Semantic from "./components/Semantic";
import Observability from "./components/Observability";
import Compliance from "./components/Compliance";
import Settings from "./components/Settings";
import DemoChooser from "./components/DemoChooser";
import CopyLink from "./components/CopyLink";
import { useDemo } from "./DemoContext";
import { useLoc } from "./lib/router";
import { TAB_PATH, tabFromPath, navTo, decisionsHref, findingsHref } from "./routes";
import { parseKV } from "./util";
import { useReviewSync, type ReviewEvent } from "./hooks/useReviewSync";

// Schema + intents are per-datasource, so their browser caches are namespaced by
// demo id. A global key (as these once were) desyncs from the persisted demo
// choice on reload — the reset-on-switch effect below is skipped on first mount —
// so one demo's schema/intents would load under another, e.g. LoanPro's loan
// tables surfacing as entities in the SecureBank Business Graph.
const schemaKey  = (demoId: string) => `prefront.schema.${demoId}`;
const intentsKey = (demoId: string) => `prefront.intents.${demoId}`;

// One-time cleanup of the old un-namespaced keys (their contents belonged to no
// particular demo, so there is nothing to migrate — just stop them from lingering).
try { localStorage.removeItem("prefront.schema"); localStorage.removeItem("prefront.intents"); } catch { /* ignore */ }

function loadJSON(key: string) {
  try { const v = localStorage.getItem(key); return v ? JSON.parse(v) : null; }
  catch { return null; }
}

// Order reflects the pipeline: connect → author policy → see the domain &
// schema maps → compare at runtime. Both Business Graph and Data Graph follow
// Policy Studio because they surface applied policies (Business Graph joins
// schema entities/intents with policy rules; Data Graph annotates the schema).
// Icons live on each tab so order changes can't desync the icon row.
const TABS = [
  { id: "dashboard",label: "Overview",        sub: "Governance at a glance",   icon: IconHome },
  { id: "data",     label: "Data Connector",  sub: "Connect datasource",       icon: IconDatabase },
  { id: "policy",   label: "Policy Studio",   sub: "Review & approve rules",   icon: IconShield },
  // Its own tab, not a Policy Studio sub-view: those live under a SELECTED
  // DOCUMENT (/policy/<id>?tab=), and this whole path exists for deployments
  // that have no document to select. Nesting it would have required picking a
  // policy document to reach the feature for people who have none.
  { id: "learned",  label: "Learned Intents", sub: "Mined from behaviour",     icon: IconLearn },
  { id: "bizgraph", label: "Business Graph",  sub: "Domain model & roles",     icon: IconBusiness },
  { id: "graph",    label: "Data Graph",      sub: "Schema & policy map",      icon: IconGraph },
  // Semantic Layer hidden for now — its tab body stays mounted below (never
  // shown, since `tab` can no longer become "semantic") so re-enabling it is a
  // one-line restore here.
  // Restored (see components/RuntimeDiff.tsx). Sits before Decision Traces
  // because it is where a governed decision is MADE and watched; the log is
  // what you read afterwards. ALWAYS in the nav, including for a demo whose
  // orchestrator serves no two-sided diff: hiding it there was a mistake —
  // the app opens on LoanPro, so the tab was simply absent and there was
  // nothing to click or discover. The body says why instead (see below),
  // which is what the nav entry is for.
  { id: "runtime",  label: "Runtime",         sub: "Governed vs ungoverned",   icon: IconSplit },
  { id: "traces",   label: "Decision Traces", sub: "Filterable decision log",  icon: IconList },
  // Intent Flows hidden for the demo, the same way as Semantic Layer above:
  // its tab body stays mounted below, so re-enabling it is restoring this line.
  // { id: "flows",    label: "Intent Flows",    sub: "Per-user intent sequences",icon: IconFlow },
  { id: "oob",      label: "Observability",   sub: "Traces, LLM, cost (OOB)",   icon: IconPulse },
  { id: "compliance", label: "Compliance",    sub: "Framework evidence",        icon: IconCheckShield },
];

/* ── Sidebar icons ── */
function IconCheckShield() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>
      <path d="M9 12l2 2 4-4"/>
    </svg>
  );
}
function IconLearn() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M3 17c3 0 3-8 6-8s3 8 6 8 3-5 6-5"/>
      <circle cx="9" cy="9" r="1.4"/>
    </svg>
  );
}

function IconSplit() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="4" width="7" height="16" rx="1.5"/>
      <rect x="14" y="4" width="7" height="16" rx="1.5"/>
    </svg>
  );
}

function IconPulse() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M3 12h4l2-6 4 12 2-6h6"/>
    </svg>
  );
}
function IconFlow() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="4" width="6" height="5" rx="1"/><rect x="15" y="4" width="6" height="5" rx="1"/>
      <rect x="9" y="15" width="6" height="5" rx="1"/>
      <path d="M9 6.5h3a2 2 0 0 1 2 2V15"/><path d="M15 6.5h-3a2 2 0 0 0-2 2V15"/>
    </svg>
  );
}
function IconList() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/><line x1="8" y1="18" x2="21" y2="18"/>
      <line x1="3" y1="6" x2="3.01" y2="6"/><line x1="3" y1="12" x2="3.01" y2="12"/><line x1="3" y1="18" x2="3.01" y2="18"/>
    </svg>
  );
}
function IconHome() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M3 12l9-8 9 8"/>
      <path d="M5 10v10h5v-6h4v6h5V10"/>
    </svg>
  );
}
function IconDatabase() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <ellipse cx="12" cy="5" rx="9" ry="3"/>
      <path d="M3 5v6c0 1.66 4.03 3 9 3s9-1.34 9-3V5"/>
      <path d="M3 11v6c0 1.66 4.03 3 9 3s9-1.34 9-3v-6"/>
    </svg>
  );
}
function IconShield() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>
    </svg>
  );
}
function IconLayers() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <polygon points="12 2 2 7 12 12 22 7 12 2"/>
      <polyline points="2 17 12 22 22 17"/>
      <polyline points="2 12 12 17 22 12"/>
    </svg>
  );
}
function IconGraph() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="18" cy="5" r="3"/>
      <circle cx="6" cy="12" r="3"/>
      <circle cx="18" cy="19" r="3"/>
      <line x1="8.59" y1="13.51" x2="15.42" y2="17.49"/>
      <line x1="15.41" y1="6.51" x2="8.59" y2="10.49"/>
    </svg>
  );
}
function IconBusiness() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <rect x="2" y="7" width="6" height="13" rx="1.5"/>
      <rect x="9" y="2" width="6" height="18" rx="1.5"/>
      <rect x="16" y="11" width="6" height="9" rx="1.5"/>
    </svg>
  );
}
function IconBell() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/>
      <path d="M13.73 21a2 2 0 0 1-3.46 0"/>
    </svg>
  );
}
function IconSettings() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="3"/>
      <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/>
    </svg>
  );
}

const PAGE_META: Record<string, { title: string; desc: string }> = {
  dashboard:{ title: "Overview",          desc: "Moving agents from demo to production — every action follows business rules, uses approved context, and produces decision evidence." },
  learned:  { title: "Learned Intents",   desc: "For a deployment with no policy document: what the traces imply. Tool calls are grouped by operation, profiled by counting, and — optionally — read by a model that states the rule the behaviour appears to follow. Candidates to review, never a published policy." },
  runtime:  { title: "Runtime",           desc: "The same request answered twice — a realistic app-layer agent with typed business functions and no authorization policy, versus the identical request through the Prefront runtime with identity injected and policy enforced. The verdict, the rows and the model's own answer, side by side." },
  traces:   { title: "Decision Traces",  desc: "The full governance decision log — filter every recorded decision by outcome, caller, role, intent, or policy." },
  flows:    { title: "Intent Flows",     desc: "Profile which intents each user invokes, in what order, within a session." },
  data:     { title: "Data Connector",   desc: "Point Prefront at a datasource and introspect its schema." },
  graph:    { title: "Data Graph",       desc: "Interactive map of tables, relationships, sensitive columns, and applied governance policies." },
  bizgraph: { title: "Business Graph",   desc: "Domain model showing business entities, processes, roles, and applied governance policies." },
  policy:   { title: "Policy Studio",   desc: "Upload policy documents, extract rules, and manage the review pipeline." },
  semantic: { title: "Semantic Layer",  desc: "Build governed interfaces — SQL query templates or MCP tool bindings — from approved rules and your schema." },
  oob:      { title: "Observability",    desc: "Out-of-band traces from Phoenix → ClickHouse: sessions, the app agent's turns, LLM calls, and tool calls — latency, tokens, cost. Nothing inline." },
  compliance:{ title: "Compliance",       desc: "Framework evidence — GDPR, SOC 2, PCI-DSS, HIPAA and the deployment's own regime, each control resolved over the verdicts already recorded: evidenced, violated, or honestly no evidence." },
  settings: { title: "Settings",         desc: "Under-the-hood configuration. Choose which of the engine's checks this deployment runs, and tune how the findings they produce are rated and triaged." },
};

function ReviewerDot({ name, color, focused }: { name: string; color: string; focused: boolean }) {
  return (
    <div
      className="pf-reviewer-dot"
      title={focused ? `${name} (reviewing a rule)` : name}
      style={{ background: color, boxShadow: focused ? `0 0 0 2px ${color}55` : "none" }}
    >
      {name[0]}
    </div>
  );
}

export default function App() {
  const { demo, demoId, openChooser } = useDemo();
  // The active tab is DERIVED from the URL, not held in state — that is what
  // makes every page directly reachable by link. `tabFromPath` falls back to
  // the Overview for anything unknown, so PAGE_META below can't be undefined.
  const loc = useLoc();
  const tab = tabFromPath(loc.segs);

  // Where you last were inside each tab. Tab bodies never unmount, so before
  // routing existed a sub-view (Observability's Sessions, an open session)
  // simply survived navigating away and back; remembering the last URL per
  // tab keeps that true now that the sub-view lives in the path.
  const lastPath = useRef<Record<string, string>>({});
  useEffect(() => { lastPath.current[tab] = loc.key; }, [tab, loc.key]);
  // Lifted so the Overview can deep-link into Decision Traces > Findings,
  // optionally prefiltered by effect (block / approval_required / flag).
  const [findingsEffect, setFindingsEffect] = useState("");
  const [findingsSeverity, setFindingsSeverity] = useState("");
  // Both graphs are mounted lazily (they are the two expensive subtrees) and
  // the latch is one-way, so they never unmount once opened. Keyed off the
  // DERIVED tab rather than the nav click, so a deep link / Back-forward
  // mounts them too — a /data-graph link rendered blank when this lived in
  // the sidebar's onClick.
  const [graphMounted, setGraphMounted] = useState(() => tab === "graph");
  const [bizGraphMounted, setBizGraphMounted] = useState(() => tab === "bizgraph");
  useEffect(() => {
    if (tab === "graph") setGraphMounted(true);
    if (tab === "bizgraph") setBizGraphMounted(true);
  }, [tab]);
  const [rules, setRules] = useState<any[]>([]);
  const [domain, setDomain] = useState("");
  const [schema, setSchema] = useState<any>(() => loadJSON(schemaKey(demoId)));
  const [metricsText, setMetricsText] = useState(demo.defaultMetrics);
  const [callerScopeText, setCallerScopeText] = useState(demo.defaultCallerScope);
  const [intents, setIntents] = useState<string>(() => {
    try { return localStorage.getItem(intentsKey(demoId)) || ""; } catch { return ""; }
  });

  const [remoteRuleUpdates, setRemoteRuleUpdates] = useState<ReviewEvent[]>([]);

  const handleRuleStatus = useCallback((evt: ReviewEvent) => {
    setRemoteRuleUpdates(prev => [...prev, evt]);
    setTimeout(() => setRemoteRuleUpdates(prev => prev.filter(e => e !== evt)), 100);
  }, []);

  const { connected, reviewers, myId, focus, broadcastRuleStatus } =
    useReviewSync({ onRuleStatus: handleRuleStatus });

  // Reviewer names are always auto-assigned by the server on connect — no prompt.

  function onSchema(s: any) {
    setSchema(s);
    try { localStorage.setItem(schemaKey(demoId), JSON.stringify(s)); } catch { /* quota */ }
    if (s?.suggestedIntents?.length && !intents.trim()) {
      setIntents(s.suggestedIntents.join(", "));
    }
  }

  function onDisconnect() {
    setSchema(null);
    setIntents("");
    try {
      localStorage.removeItem(schemaKey(demoId));
      localStorage.removeItem(intentsKey(demoId));
    } catch { /* ignore */ }
  }

  // Persist intents to the active demo's key. Skip the render on which the demo
  // just changed: `intents` still holds the previous demo's value there, and the
  // switch effect below is about to load the new demo's cache — writing now would
  // clobber the new demo's saved intents with the old demo's.
  const persistDemo = useRef(demoId);
  useEffect(() => {
    if (persistDemo.current !== demoId) { persistDemo.current = demoId; return; }
    try {
      if (intents) localStorage.setItem(intentsKey(demoId), intents);
      else localStorage.removeItem(intentsKey(demoId));
    } catch { /* quota */ }
  }, [intents, demoId]);

  // Switching demos: schema/rules/intents are per-datasource, so swap them out for
  // the newly-selected demo's own cache (each demo remembers its own connection)
  // and reset the design-time defaults. Only fires on an actual change (not first
  // mount), so a reload keeps the demo you were in with its own schema/intents.
  const prevDemo = useRef(demoId);
  useEffect(() => {
    if (prevDemo.current === demoId) return;
    prevDemo.current = demoId;
    // Artifact ids are demo-scoped, so the remembered per-tab URLs from the
    // previous demo are meaningless now.
    lastPath.current = {};
    setSchema(loadJSON(schemaKey(demoId)));
    try { setIntents(localStorage.getItem(intentsKey(demoId)) || ""); } catch { setIntents(""); }
    setRules([]);
    setDomain("");
    setMetricsText(demo.defaultMetrics);
    setCallerScopeText(demo.defaultCallerScope);
  }, [demoId]); // eslint-disable-line react-hooks/exhaustive-deps

  // Sidebar: an icon rail by default, expandable to show each tab's name.
  // Remembered per browser — a layout preference, not shared state.
  const [navOpen, setNavOpen] = useState<boolean>(() => {
    try { return localStorage.getItem("prefront.nav.open") === "1"; } catch { return false; }
  });
  useEffect(() => {
    try { localStorage.setItem("prefront.nav.open", navOpen ? "1" : "0"); } catch { /* ignore */ }
  }, [navOpen]);

  const completedTabs = new Set<string>();
  if (schema?.datasourceId) completedTabs.add("data");
  if (rules.some(r => r.review_status === "approved")) completedTabs.add("policy");

  const others = reviewers.filter(r => r.id !== myId);
  // The Data Graph renders two different things depending on what's connected —
  // a table/relationship map, or an MCP server's tools — so its subtitle can't
  // be a constant without describing the wrong one half the time.
  const baseMeta = PAGE_META[tab] ?? PAGE_META.dashboard;
  const meta = (tab === "graph" && schema?.sourceType === "mcp")
    ? { ...baseMeta, desc: "Every tool this MCP server declares — parameters, declared outputs, behaviour annotations, and applied governance policies." }
    : baseMeta;

  return (
    <>
    <DemoChooser />
    <div className={`pf-shell${navOpen ? " nav-open" : ""}`}>
      {/* ── Left icon sidebar ── */}
      <aside className="pf-sidebar">
        {/* Logo — "pf" wordmark (p solid, f outline) */}
        <div className="pf-sidebar-logo" title="Prefront">
          <span className="pf-logo-wordmark">
            <span className="pf-logo-p">p</span><span className="pf-logo-f">f</span>
          </span>
        </div>

        {/* Active-demo pill — click to reopen the demo switcher */}
        <button
          className="pf-demo-pill"
          style={{ ["--demo-accent" as any]: demo.accent }}
          title={`Demo: ${demo.label} — click to switch`}
          onClick={openChooser}
        >
          <span className="pf-demo-pill-glyph" aria-hidden>{demo.glyph}</span>
          <span className="pf-demo-pill-label">{demo.label}</span>
        </button>

        {/* Nav icons */}
        {TABS.map((t) => {
          const Icon = t.icon;
          const isActive = tab === t.id;
          const isDone = completedTabs.has(t.id) && !isActive;
          return (
            <button
              key={t.id}
              className={`pf-nav-item ${isActive ? "active" : ""} ${isDone ? "done" : ""}`}
              onClick={() => navTo(lastPath.current[t.id] ?? TAB_PATH[t.id])}
              title={t.label}
            >
              <Icon />
              <span className="pf-nav-label">{t.label}</span>
            </button>
          );
        })}

        <div className="pf-sidebar-divider" />

        {/* Bottom utility icons */}
        <div className="pf-sidebar-bottom">
          <button className="pf-nav-item" title="Notifications"><IconBell /><span className="pf-nav-label">Notifications</span></button>
          <button className={`pf-nav-item ${tab === "settings" ? "active" : ""}`} title="Settings" onClick={() => navTo(lastPath.current.settings ?? TAB_PATH.settings)}><IconSettings /><span className="pf-nav-label">Settings</span></button>
          <button className="pf-nav-item pf-nav-toggle" type="button" aria-expanded={navOpen}
                  title={navOpen ? "Collapse sidebar" : "Expand sidebar"}
                  onClick={() => setNavOpen((v) => !v)}>
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"
                 strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <path d="M13 17l5-5-5-5" /><path d="M6 17l5-5-5-5" />
            </svg>
            <span className="pf-nav-label">Collapse</span>
          </button>
        </div>
      </aside>

      {/* ── Main content ── */}
      <div className="pf-content">
        {/* Page header */}
        {/* The Overview has its own editorial hero, so the whole app header
            (title/desc + presence) is omitted there — otherwise it leaves an
            empty white bar with just the presence text. */}
        {tab !== "dashboard" && (
        <header className="pf-page-header">
          <div>
            <div className="pf-page-title">{meta.title}</div>
            <div className="pf-page-desc">{meta.desc}</div>
          </div>

          <div className="pf-page-actions">
            {/* Share the page you are on (each artifact has its own button) */}
            <CopyLink label="Copy link" compact={false} title="Copy a link to this page" />
            {/* Live presence */}
            <div className="pf-presence">
              {connected ? (
                <>
                  <span className="pf-live-dot" title="Review session live" />
                  {others.length > 0 && (
                    <div className="pf-reviewer-dots">
                      {others.map(r => (
                        <ReviewerDot key={r.id} name={r.name} color={r.color}
                          focused={r.focusedRuleId !== null} />
                      ))}
                    </div>
                  )}
                  <span className="pf-presence-label">
                    {others.length === 0 ? "Just you" : `+${others.length} reviewer${others.length !== 1 ? "s" : ""}`}
                  </span>
                </>
              ) : (
                <span className="pf-presence-label offline">● offline</span>
              )}
            </div>
          </div>
        </header>
        )}

        {/* Tab bodies — keyed on the active demo so per-tab state resets on switch */}
        <div className="pf-body" key={demoId}>
          <div className={tab === "dashboard" ? "" : "tab-hidden"}>
            <Overview demo={demo} active={tab === "dashboard"}
                      onOpenFindings={(effect) => { setFindingsEffect(effect ?? ""); setFindingsSeverity(""); navTo(findingsHref()); }}
                      onOpenFindingsSeverity={(sev) => { setFindingsSeverity(sev ?? ""); setFindingsEffect(""); navTo(findingsHref()); }}
                      onOpenDecisions={() => navTo(decisionsHref())}
                      onOpenObservability={() => navTo(TAB_PATH.oob)}
                      onOpenSettings={() => navTo(TAB_PATH.settings)} />
          </div>
          <div className={tab === "learned" ? "" : "tab-hidden"}>
            <LearnedIntents demo={demo} active={tab === "learned"} />
          </div>
          <div className={tab === "runtime" ? "" : "tab-hidden"}>
            {/* One tab, one question — "what does this application do at
                runtime?" — answered in the form the application supports.
                An IN-BAND app (SecureBank) answers with the before/after diff:
                the same request with and without Prefront in the path. An
                OUT-OF-BAND one (LoanPro) has no such pair — Prefront only
                observes it — so its answer is a session and the findings the
                evaluator raised about it afterwards. `key` remounts on a demo
                switch so no catalogue or run result crosses applications. */}
            {demo.runtimeDiff ? <RuntimeDiff demo={demo} />
             : demo.scenarioCatalogue ? <ScenarioRunner key={demo.id} app={demo} />
             : (
              // Not an error state: this demo genuinely has no two-sided diff
              // to show. Say which demo does, and offer the switch, rather
              // than leaving the reader to work out that the tab is fine and
              // the demo is the problem.
              <main><div className="pf-panel">
                <h2>Not available for {demo.label}</h2>
                <p className="pf-hint">
                  This view runs one request twice — through the app layer with no
                  policy, and through the Prefront runtime — and shows the two
                  side by side. {demo.label}'s orchestrator does not serve that
                  pair: its before/after is a whole governed <em>session</em>,
                  which the Verdict app runs instead.
                </p>
                {DEMOS.filter((d) => d.runtimeDiff || d.scenarioCatalogue).map((d) => (
                  <button key={d.id} className="pf-btn" style={{ marginTop: 10 }}
                          onClick={() => openChooser()}>
                    Switch to {d.label} to see it
                  </button>
                ))}
              </div></main>
            )}
          </div>
          <div className={tab === "traces" ? "" : "tab-hidden"}>
            <DecisionTraces active={tab === "traces"} demo={demo} findingsEffect={findingsEffect} findingsSeverity={findingsSeverity} />
          </div>
          <div className={tab === "compliance" ? "" : "tab-hidden"}>
            <Compliance demo={demo} active={tab === "compliance"} schema={schema} />
          </div>
          <div className={tab === "settings" ? "" : "tab-hidden"}>
            <Settings demo={demo} active={tab === "settings"} />
          </div>
          <div className={tab === "flows" ? "" : "tab-hidden"}>
            <IntentFlows active={tab === "flows"} demo={demo} />
          </div>
          <div className={tab === "data" ? "" : "tab-hidden"}>
            <DataConnector active={tab === "data"} demo={demo} onSchema={onSchema} onDisconnect={onDisconnect} restored={schema} />
          </div>
          {graphMounted && (
            <div className={tab === "graph" ? "" : "tab-hidden"}>
              <DataGraph catalog={schema?.catalog} datasourceId={schema?.datasourceId} rules={rules}
                pii={schema?.pii} sourceType={schema?.sourceType} mcpTools={schema?.mcpTools} />
            </div>
          )}
          {bizGraphMounted && (
            <div className={tab === "bizgraph" ? "" : "tab-hidden"}>
              <BusinessGraph
                catalog={schema?.catalog}
                datasourceId={schema?.datasourceId}
                rules={rules}
                intents={intents}
                domain={domain}
                pii={schema?.pii}
              />
            </div>
          )}
          <div className={tab === "policy" ? "" : "tab-hidden"}>
            <PolicyStudio
              onRules={(rs: any[], dm: string) => { setRules(rs); setDomain(dm); }}
              schema={schema}
              metrics={parseKV(metricsText)}
              intents={intents}
              appId={demo.id}
              reviewers={reviewers}
              myId={myId}
              onFocusRule={focus}
              broadcastRuleStatus={broadcastRuleStatus}
              remoteRuleUpdates={remoteRuleUpdates}
            />
          </div>
          <div className={tab === "semantic" ? "" : "tab-hidden"}>
            <Semantic
              rules={rules}
              domain={domain}
              schema={schema}
              metricsText={metricsText}
              setMetricsText={setMetricsText}
              callerScopeText={callerScopeText}
              setCallerScopeText={setCallerScopeText}
              intents={intents}
              setIntents={setIntents}
            />
          </div>
          <div className={tab === "oob" ? "" : "tab-hidden"}>
            <Observability active={tab === "oob"} demo={demo} />
          </div>
        </div>
      </div>
    </div>
    </>
  );
}
