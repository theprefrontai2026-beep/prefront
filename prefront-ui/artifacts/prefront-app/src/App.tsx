import { useCallback, useEffect, useState } from "react";
import Dashboard from "./components/Dashboard";
import PolicyStudio from "./components/PolicyStudio";
import DataConnector from "./components/DataConnector";
import DataGraph from "./components/DataGraph";
import BusinessGraph from "./components/BusinessGraph";
import Semantic from "./components/Semantic";
import RuntimeDiff from "./components/RuntimeDiff";
import { parseKV } from "./util";
import { useReviewSync, type ReviewEvent } from "./hooks/useReviewSync";

const SCHEMA_KEY = "prefront.schema";
const INTENTS_KEY = "prefront.intents";

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
  { id: "bizgraph", label: "Business Graph",  sub: "Domain model & roles",     icon: IconBusiness },
  { id: "graph",    label: "Data Graph",      sub: "Schema & policy map",      icon: IconGraph },
  { id: "runtime",  label: "Runtime",         sub: "Governed vs ungoverned",   icon: IconDiff },
];

/* ── Sidebar icons ── */
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
function IconDiff() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="3" width="7" height="18" rx="1"/>
      <rect x="14" y="3" width="7" height="18" rx="1"/>
      <path d="M6.5 8h0M6.5 12h0M6.5 16h0" strokeWidth="2.5"/>
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
  dashboard:{ title: "Overview",          desc: "Decision intelligence — can I trust what my agents are doing right now?" },
  data:     { title: "Data Connector",   desc: "Point Prefront at a datasource and introspect its schema." },
  graph:    { title: "Data Graph",       desc: "Interactive map of tables, relationships, sensitive columns, and applied governance policies." },
  bizgraph: { title: "Business Graph",   desc: "Domain model showing business entities, processes, roles, and applied governance policies." },
  policy:   { title: "Policy Studio",   desc: "Upload policy documents, extract rules, and manage the review pipeline." },
  semantic: { title: "Semantic Layer",  desc: "Build governed SQL interfaces from approved rules and your schema." },
  runtime:  { title: "Runtime Diff",    desc: "Compare governed vs. ungoverned query results across test scenarios." },
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
  const [tab, setTab] = useState("dashboard");
  const [graphMounted, setGraphMounted] = useState(false);
  const [bizGraphMounted, setBizGraphMounted] = useState(false);
  const [rules, setRules] = useState<any[]>([]);
  const [domain, setDomain] = useState("");
  const [schema, setSchema] = useState<any>(() => loadJSON(SCHEMA_KEY));
  const [metricsText, setMetricsText] = useState(
    "available_credit = credit_limit - current_balance\n" +
    "credit_utilization_pct = current_balance / credit_limit * 100"
  );
  const [callerScopeText, setCallerScopeText] = useState(
    "region = region_id\nrep_id = rep_id"
  );
  const [intents, setIntents] = useState<string>(() => {
    try { return localStorage.getItem(INTENTS_KEY) || ""; } catch { return ""; }
  });

  const [remoteRuleUpdates, setRemoteRuleUpdates] = useState<ReviewEvent[]>([]);

  const handleRuleStatus = useCallback((evt: ReviewEvent) => {
    setRemoteRuleUpdates(prev => [...prev, evt]);
    setTimeout(() => setRemoteRuleUpdates(prev => prev.filter(e => e !== evt)), 100);
  }, []);

  const { connected, reviewers, myId, focus, broadcastRuleStatus, identify } =
    useReviewSync({ onRuleStatus: handleRuleStatus });

  const [named, setNamed] = useState(false);
  useEffect(() => {
    if (connected && !named) {
      setNamed(true);
      const stored = sessionStorage.getItem("pf.reviewer.name");
      if (stored) { identify(stored); return; }
      const n = window.prompt("Your reviewer name (leave blank for auto-assign):", "");
      if (n?.trim()) {
        sessionStorage.setItem("pf.reviewer.name", n.trim());
        identify(n.trim());
      }
    }
  }, [connected, named, identify]);

  function onSchema(s: any) {
    setSchema(s);
    try { localStorage.setItem(SCHEMA_KEY, JSON.stringify(s)); } catch { /* quota */ }
    if (s?.suggestedIntents?.length && !intents.trim()) {
      setIntents(s.suggestedIntents.join(", "));
    }
  }

  function onDisconnect() {
    setSchema(null);
    setIntents("");
    try {
      localStorage.removeItem(SCHEMA_KEY);
      localStorage.removeItem(INTENTS_KEY);
    } catch { /* ignore */ }
  }

  useEffect(() => {
    try {
      if (intents) localStorage.setItem(INTENTS_KEY, intents);
      else localStorage.removeItem(INTENTS_KEY);
    } catch { /* quota */ }
  }, [intents]);

  const completedTabs = new Set<string>();
  if (schema?.datasourceId) completedTabs.add("data");
  if (rules.some(r => r.review_status === "approved")) completedTabs.add("policy");

  const others = reviewers.filter(r => r.id !== myId);
  const meta = PAGE_META[tab];

  return (
    <div className="pf-shell">
      {/* ── Left icon sidebar ── */}
      <aside className="pf-sidebar">
        {/* Logo — "pf" wordmark (p solid, f outline) */}
        <div className="pf-sidebar-logo" title="Prefront">
          <span className="pf-logo-wordmark">
            <span className="pf-logo-p">p</span><span className="pf-logo-f">f</span>
          </span>
        </div>

        {/* Nav icons */}
        {TABS.map((t) => {
          const Icon = t.icon;
          const isActive = tab === t.id;
          const isDone = completedTabs.has(t.id) && !isActive;
          return (
            <button
              key={t.id}
              className={`pf-nav-item ${isActive ? "active" : ""} ${isDone ? "done" : ""}`}
              onClick={() => { setTab(t.id); if (t.id === "graph") setGraphMounted(true); if (t.id === "bizgraph") setBizGraphMounted(true); }}
              title={t.label}
            >
              <Icon />
            </button>
          );
        })}

        <div className="pf-sidebar-divider" />

        {/* Bottom utility icons */}
        <div className="pf-sidebar-bottom">
          <button className="pf-nav-item" title="Notifications"><IconBell /></button>
          <button className="pf-nav-item" title="Settings"><IconSettings /></button>
        </div>
      </aside>

      {/* ── Main content ── */}
      <div className="pf-content">
        {/* Page header */}
        <header className="pf-page-header">
          <div>
            <div className="pf-page-title">{meta.title}</div>
            <div className="pf-page-desc">{meta.desc}</div>
          </div>

          <div className="pf-page-actions">
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

        {/* Tab bodies */}
        <div className="pf-body">
          <div className={tab === "dashboard" ? "" : "tab-hidden"}>
            <Dashboard />
          </div>
          <div className={tab === "data" ? "" : "tab-hidden"}>
            <DataConnector active={tab === "data"} onSchema={onSchema} onDisconnect={onDisconnect} restored={schema} />
          </div>
          {graphMounted && (
            <div className={tab === "graph" ? "" : "tab-hidden"}>
              <DataGraph catalog={schema?.catalog} datasourceId={schema?.datasourceId} rules={rules} pii={schema?.pii} />
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
              setIntents={setIntents}
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
          <div className={tab === "runtime" ? "" : "tab-hidden"}>
            <RuntimeDiff />
          </div>
        </div>
      </div>
    </div>
  );
}
