/*
 * Decision Traces — the full, filterable governance decision log.
 *
 * Reads persisted traces from the DB (GET /api/decisions) and lets you slice
 * them every way the runtime records: decision, caller, role, intent, the
 * policy that fired, and free text. This is the "learning" surface — every
 * governed decision accumulates here as precedent, and filtering reveals the
 * patterns: what a given role keeps getting blocked on, everything one policy
 * has ever governed, how a single intent resolves across callers.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import type { FeedDecision, Trace } from "../hooks/useDecisionFeed";
import type { DemoConfig } from "../demos";
import { SessionFlyout, parseSource, type EvalVerdict } from "./Observability";
import { severityOf, SEVERITY_META, SEVERITY_ORDER, type SeverityLevel, type SeverityRule } from "../severity";
import { useSeverityRules } from "../hooks/useSeverityRules";

const DECISIONS: FeedDecision[] = ["ALLOWED", "MASKED", "APPROVAL", "BLOCKED"];

function chipTone(d: FeedDecision): string {
  return d === "BLOCKED" ? "red" : d === "APPROVAL" ? "amber" : d === "MASKED" ? "teal" : "green";
}

function fmtWhen(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString(undefined, {
    month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit",
  });
}

function argSummary(args: Record<string, any> | null): string {
  if (!args) return "";
  const parts = Object.entries(args).map(([k, v]) => `${k}=${v}`);
  return parts.length ? `(${parts.join(", ")})` : "";
}

/** The family's display name, as stamped by eval-engine. Falls back to the raw
 *  stored value so a row from an older response never renders blank. */
function famOf(r: { family: string; family_label?: string }): string {
  return r.family_label || r.family;
}

function uniqueSorted(vals: (string | null | undefined)[]): string[] {
  return Array.from(new Set(vals.filter((v): v is string => !!v))).sort();
}

function Select({ label, value, options, onChange }: {
  label: string; value: string; options: string[]; onChange: (v: string) => void;
}) {
  return (
    <label className="pf-tr-select">
      <span>{label}</span>
      <select value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">All</option>
        {options.map((o) => <option key={o} value={o}>{o}</option>)}
      </select>
    </label>
  );
}

/* ── Findings: eval-engine's shadow-evaluation log, moved here from the
   Observability tab (see Observability.tsx's VIEWS comment) — it's a
   governance-decision-log concept like the traces above, not an
   observability-pipeline-health one. Every displayed column is filterable,
   session_id is never shown (only used internally to open the trace
   flyout), and each row states what went wrong in plain language plus the
   policy section + verbatim quote it cites, when the check has one
   (Family 1 always does; Family 3 has a section with no quotable text -
   the intent catalog doesn't carry policy prose; Family 2 has neither -
   see eval-engine/CLAUDE.md's Hard Rule 17). ──────────────────────────── */

// "13.2 Verify Before Quoting / 5.3 KYC Refresh Requirement" -> ["13.2", "5.3"]
// "11.4, 12.6" -> ["11.4", "12.6"] - the leading numeric token of each
// slash/comma-separated clause, for filter matching; display uses the full string.
function policyNumbers(section: string): string[] {
  if (!section) return [];
  return section.split(/[/,]/).map((s) => (s.trim().match(/^[\d.]+/) || [])[0]).filter(Boolean) as string[];
}

const FINDING_RANGES: { label: string; seconds: number | null }[] = [
  { label: "1h", seconds: 3600 }, { label: "24h", seconds: 86400 }, { label: "7d", seconds: 604800 },
  { label: "All", seconds: null },
];

function findingWhen(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

// Table cell version: just the one-liner, truncated to fit a fixed column
// width with the full text on hover (native title tooltip) - the full
// policy-quote corroboration lives in the flyout now (SessionDetail's
// findingDetail/findingSource, opened by clicking the row), not repeated
// here where every column is deliberately kept narrow.
function WhatWentWrong({ r }: { r: EvalVerdict }) {
  return (
    <div className="pf-tr-truncate pf-find-detail" title={r.detail}>{r.detail}</div>
  );
}

// `initialEffect` / `initialSeverity` let the Overview's tiles deep-link here
// prefiltered (block / approval_required / flag, or a severity level); each
// re-applies whenever it changes so a second click from the Overview isn't
// ignored. `rules` is the customer's severity mapping (severity is derived per
// row from family+effect, first-match-wins).
function FindingsSection({ initialEffect = "", initialSeverity = "", rules, active = true }: {
  initialEffect?: string; initialSeverity?: string; rules: SeverityRule[]; active?: boolean;
}) {
  const [rows, setRows] = useState<EvalVerdict[]>([]);
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");
  const [error, setError] = useState("");
  const [flyout, setFlyout] = useState<{ sessionId: string; spanId: string | null; eventId: string | null; detail: string; source: string } | null>(null);

  // ── Filters, one per displayed column ──
  const [range, setRange] = useState<number | null>(86400);
  const [eventId, setEventId] = useState("");
  const [family, setFamily] = useState("");
  const [checkId, setCheckId] = useState("");
  const [effect, setEffect] = useState(initialEffect);
  const [severity, setSeverity] = useState(initialSeverity);
  const [policyNum, setPolicyNum] = useState("");
  const [q, setQ] = useState("");
  useEffect(() => { setEffect(initialEffect); if (initialEffect) setRange(null); }, [initialEffect]);
  useEffect(() => { setSeverity(initialSeverity); if (initialSeverity) setRange(null); }, [initialSeverity]);

  const sevOf = useCallback((r: EvalVerdict): SeverityLevel => severityOf({ family: r.family, effect: r.effect }, rules), [rules]);

  const load = useCallback(async () => {
    setStatus("loading");
    setError("");
    try {
      // The most recent 500 (server-sorted by evaluated_at DESC, the
      // endpoint's own cap) - filtered further client-side below, same
      // pattern as the Decisions log above (fetch a recent slice once,
      // slice-and-dice in the browser rather than a filter param per column).
      const res = await fetch("/eval/findings?limit=500");
      const json = await res.json();
      if (!res.ok) throw new Error(json?.error || `${res.status} ${res.statusText}`);
      setRows(json.findings || []);
      setStatus("ready");
    } catch (e: any) {
      setError(String(e?.message || e));
      setStatus("error");
    }
  }, []);

  // Re-fetch whenever the tab becomes visible again, not just on mount.
  // App.tsx keeps every tab MOUNTED and toggles `tab-hidden` (so tab state
  // survives navigation), which means coming back to Findings runs no effect
  // at all — it kept showing whatever it fetched the first time, while
  // eval-engine had since evaluated more sessions. The Decisions section
  // beside it already reloads on `active`; this is the same wiring.
  useEffect(() => { if (active) load(); }, [active, load]);

  // Filter and display on eval-engine's family display name (Policy /
  // Integrity / Conformance), falling back to the raw family1|2|3 for a row
  // served before the label existed.
  const families = useMemo(() => uniqueSorted(rows.map((r) => famOf(r))), [rows]);
  const checks = useMemo(() => uniqueSorted(rows.map((r) => r.check_id)), [rows]);
  const effects = useMemo(() => uniqueSorted(rows.map((r) => r.effect)), [rows]);
  const policies = useMemo(
    () => Array.from(new Set(rows.flatMap((r) => policyNumbers(parseSource(r.source)?.section || "")))).sort(),
    [rows],
  );

  const filtered = useMemo(() => {
    const needle = q.trim().toLowerCase();
    const cutoff = range ? Date.now() - range * 1000 : null;
    const out = rows.filter((r) => {
      if (cutoff && new Date(r.evaluated_at).getTime() < cutoff) return false;
      if (eventId.trim() && !r.event_id.includes(eventId.trim())) return false;
      if (family && famOf(r) !== family) return false;
      if (checkId && r.check_id !== checkId) return false;
      if (effect && r.effect !== effect) return false;
      if (severity && sevOf(r) !== severity) return false;
      if (policyNum) {
        const src = parseSource(r.source);
        if (!policyNumbers(src?.section || "").includes(policyNum)) return false;
      }
      if (needle) {
        const src = parseSource(r.source);
        const hay = [r.detail, src?.section, src?.text, src?.document, r.check_id].join(" ").toLowerCase();
        if (!hay.includes(needle)) return false;
      }
      return true;
    });
    // Triage order: most-severe first, then most-recent within a severity —
    // the whole point of a severity rating (falls back to server's DESC order).
    return out.sort((a, b) =>
      SEVERITY_META[sevOf(b)].rank - SEVERITY_META[sevOf(a)].rank
      || new Date(b.evaluated_at).getTime() - new Date(a.evaluated_at).getTime());
  }, [rows, range, eventId, family, checkId, effect, severity, policyNum, q, sevOf]);

  const activeFilters = (eventId.trim() ? 1 : 0) + (family ? 1 : 0) + (checkId ? 1 : 0) + (effect ? 1 : 0) + (severity ? 1 : 0) + (policyNum ? 1 : 0) + (q.trim() ? 1 : 0);
  const clearAll = () => { setEventId(""); setFamily(""); setCheckId(""); setEffect(""); setSeverity(""); setPolicyNum(""); setQ(""); };

  return (
    <>
      <section className="pf-panel">
        <div className="pf-dash-panel-head">
          <h2>Findings</h2>
          <button className="pf-dash-link" type="button" onClick={load} disabled={status === "loading"}>
            {status === "loading" ? "Loading…" : "Refresh ↻"}
          </button>
        </div>
        <p className="pf-hint" style={{ marginTop: 0 }}>
          eval-engine's shadow evaluation of every ingested session — violations only (see
          eval-engine/CLAUDE.md). Never on the request path; nothing here blocked anything, it's what
          the checks found after the fact. A clean stack shows none.
        </p>

        <div className="pf-tr-filters">
          <div className="pf-tr-chips">
            {FINDING_RANGES.map((r) => (
              <button key={r.label} type="button" className={`pf-tr-chip ${range === r.seconds ? "on" : ""}`}
                     onClick={() => setRange(r.seconds)} aria-pressed={range === r.seconds}>
                {r.label}
              </button>
            ))}
          </div>
          <div className="pf-tr-selects">
            <Select label="Family" value={family} options={families} onChange={setFamily} />
            <Select label="Check" value={checkId} options={checks} onChange={setCheckId} />
            <Select label="Effect" value={effect} options={effects} onChange={setEffect} />
            <Select label="Severity" value={severity} options={SEVERITY_ORDER as string[]} onChange={setSeverity} />
            <Select label="Policy §" value={policyNum} options={policies} onChange={setPolicyNum} />
            <label className="pf-tr-select">
              <span>Event</span>
              <input className="pf-tr-search" style={{ width: 90 }} placeholder="id…" value={eventId}
                    onChange={(e) => setEventId(e.target.value)} />
            </label>
          </div>
          <input
            className="pf-tr-search"
            placeholder="Search detail, policy text…"
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />
        </div>

        <div className="pf-tr-summary">
          <span className="pf-tr-count">
            {filtered.length}<span className="muted"> of {rows.length}</span> findings
          </span>
          {activeFilters > 0 && (
            <button className="pf-dash-link" type="button" onClick={clearAll}>
              Clear filters ✕ ({activeFilters})
            </button>
          )}
        </div>
      </section>

      <section className="pf-panel" style={{ marginTop: 14 }}>
        {status === "error" && <div className="pf-dash-feed-status error">Couldn’t load findings ({error}).</div>}
        {status !== "error" && filtered.length === 0 && (
          <div className="pf-dash-feed-status">
            {rows.length === 0 ? "No findings — either nothing violated, or Family 1/3 have no rule_pack/intent_catalog configured." : "No findings match these filters."}
          </div>
        )}
        {filtered.length > 0 && (
          <table className="pf-dash-table pf-tr-table pf-find-table">
            {/* Check and Policy stay filterable above (families/checks/policies
               dropdowns) but aren't shown as columns here - narrower table,
               less redundant with the one-liner + flyout. Every column is
               width-capped (.pf-tr-truncate) with the full value on hover
               (native title tooltip) so long text never blows out the layout. */}
            <thead>
              <tr><th>Event</th><th>When</th><th>Severity</th><th>Family</th><th>Effect</th><th>User query</th><th>What went wrong</th></tr>
            </thead>
            <tbody>
              {filtered.map((r, i) => {
                const sev = sevOf(r);
                return (
                <tr key={r.event_id || r.session_id + r.check_id + r.evidence_excerpt + i} className="clickable"
                    onClick={() => setFlyout({ sessionId: r.session_id, spanId: r.evidence_span_ids?.[0] ?? null, eventId: r.event_id || null, detail: r.detail, source: r.source })}>
                  <td className="mono pf-tr-truncate narrow" title={r.event_id || undefined}>{r.event_id || "—"}</td>
                  <td className="pf-tr-when">{findingWhen(r.evaluated_at)}</td>
                  <td><span className={`pf-dash-chip ${SEVERITY_META[sev].tone}`}>{SEVERITY_META[sev].label}</span></td>
                  <td className="pf-tr-truncate" title={famOf(r)}>{famOf(r)}</td>
                  <td><span className={`pf-dash-chip ${r.effect === "block" ? "red" : r.effect === "approval_required" ? "amber" : "teal"}`}>{r.effect}</span></td>
                  <td className="pf-tr-truncate" title={r.user_query || undefined}>{r.user_query || <span className="muted">—</span>}</td>
                  <td><WhatWentWrong r={r} /></td>
                </tr>
              );})}
            </tbody>
          </table>
        )}
      </section>

      {flyout && (
        <SessionFlyout sessionId={flyout.sessionId} initialSpanId={flyout.spanId} eventId={flyout.eventId}
                       findingDetail={flyout.detail} findingSource={flyout.source} refreshKey={0}
                       onClose={() => setFlyout(null)} />
      )}
    </>
  );
}

export type TracesSection = "decisions" | "findings";

// `section`/`onSection` make the Decisions|Findings sub-nav controllable
// (App.tsx lifts it so the Overview can deep-link straight into Findings);
// uncontrolled fallback keeps the component usable standalone.
export default function DecisionTraces({ active = true, demo, section: controlled, onSection, findingsEffect, findingsSeverity }: {
  active?: boolean; demo: DemoConfig; section?: TracesSection; onSection?: (s: TracesSection) => void; findingsEffect?: string; findingsSeverity?: string;
}) {
  const [internal, setInternal] = useState<TracesSection>("decisions");
  const section = controlled ?? internal;
  const setSection = (s: TracesSection) => { setInternal(s); onSection?.(s); };
  const roleAgents = demo.roleAgents;
  const { rules: severityRules } = useSeverityRules(demo.id, active);
  const [traces, setTraces] = useState<Trace[]>([]);
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");
  const [error, setError] = useState("");

  // ── Filters ──
  const [picked, setPicked] = useState<Set<FeedDecision>>(new Set());
  const [role, setRole] = useState("");
  const [caller, setCaller] = useState("");
  const [intent, setIntent] = useState("");
  const [policy, setPolicy] = useState("");
  const [q, setQ] = useState("");

  const load = useCallback(async () => {
    setStatus("loading");
    setError("");
    try {
      const res = await fetch(`/api/decisions?limit=30&demo=${encodeURIComponent(demo.id)}`);
      const json = await res.json();
      if (!res.ok) throw new Error(json?.error || `${res.status} ${res.statusText}`);
      setTraces(json.traces || []);
      setStatus("ready");
    } catch (e: any) {
      setError(String(e?.message || e));
      setStatus("error");
    }
  }, [demo.id]);

  useEffect(() => { load(); }, [load]);
  // Refetch when the tab becomes visible again — newly-run scenarios show up.
  useEffect(() => { if (active) load(); }, [active, load]);

  const roles = useMemo(() => uniqueSorted(traces.map((t) => t.role)), [traces]);
  const callers = useMemo(() => uniqueSorted(traces.map((t) => t.caller)), [traces]);
  const intents = useMemo(() => uniqueSorted(traces.map((t) => t.intent)), [traces]);
  const policies = useMemo(() => uniqueSorted(traces.map((t) => t.policy)), [traces]);

  const filtered = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return traces.filter((t) => {
      if (picked.size && !picked.has(t.decision)) return false;
      if (role && t.role !== role) return false;
      if (caller && t.caller !== caller) return false;
      if (intent && t.intent !== intent) return false;
      if (policy && t.policy !== policy) return false;
      if (needle) {
        const hay = [
          t.caller, t.role, t.intent, t.policy, t.capability, t.outcome,
          ...(t.reasons || []), argSummary(t.args),
        ].join(" ").toLowerCase();
        if (!hay.includes(needle)) return false;
      }
      return true;
    });
  }, [traces, picked, role, caller, intent, policy, q]);

  // Live per-decision distribution of the *filtered* set — the pattern the
  // current slice reveals.
  const tally = useMemo(() => {
    const t: Record<FeedDecision, number> = { ALLOWED: 0, MASKED: 0, APPROVAL: 0, BLOCKED: 0 };
    for (const row of filtered) t[row.decision] = (t[row.decision] ?? 0) + 1;
    return t;
  }, [filtered]);

  const toggleDecision = (d: FeedDecision) =>
    setPicked((prev) => {
      const next = new Set(prev);
      next.has(d) ? next.delete(d) : next.add(d);
      return next;
    });

  const activeFilters = picked.size + (role ? 1 : 0) + (caller ? 1 : 0) + (intent ? 1 : 0) + (policy ? 1 : 0) + (q.trim() ? 1 : 0);
  const clearAll = () => { setPicked(new Set()); setRole(""); setCaller(""); setIntent(""); setPolicy(""); setQ(""); };

  return (
    <main className="pf-tr">
      <div className="pf-oob-views" style={{ marginBottom: 14 }}>
        <button className={`pf-oob-view ${section === "decisions" ? "active" : ""}`} onClick={() => setSection("decisions")}>Decisions</button>
        <button className={`pf-oob-view ${section === "findings" ? "active" : ""}`} onClick={() => setSection("findings")}>Findings</button>
      </div>
      {section === "findings" && <FindingsSection initialEffect={findingsEffect} initialSeverity={findingsSeverity} rules={severityRules} active={active} />}
      {section === "decisions" && <>
      <section className="pf-panel">
        <div className="pf-dash-panel-head">
          <h2>Decision Trace Log</h2>
          <button className="pf-dash-link" type="button" onClick={load} disabled={status === "loading"}>
            {status === "loading" ? "Loading…" : "Refresh ↻"}
          </button>
        </div>
        <p className="pf-hint" style={{ marginTop: 0 }}>
          Every governed decision the runtime makes is recorded here as precedent. Filter the log to
          see the patterns — what a role keeps getting blocked on, everything a policy has governed,
          how one intent resolves across callers.
        </p>

        {/* ── Filter bar ── */}
        <div className="pf-tr-filters">
          <div className="pf-tr-chips">
            {DECISIONS.map((d) => (
              <button
                key={d}
                type="button"
                className={`pf-tr-chip ${chipTone(d)} ${picked.has(d) ? "on" : ""}`}
                onClick={() => toggleDecision(d)}
                aria-pressed={picked.has(d)}
              >
                {d}
              </button>
            ))}
          </div>
          <div className="pf-tr-selects">
            <Select label="Role" value={role} options={roles} onChange={setRole} />
            <Select label="Caller" value={caller} options={callers} onChange={setCaller} />
            <Select label="Intent" value={intent} options={intents} onChange={setIntent} />
            <Select label="Policy" value={policy} options={policies} onChange={setPolicy} />
          </div>
          <input
            className="pf-tr-search"
            placeholder="Search reason, args, capability…"
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />
        </div>

        {/* ── Result summary + live distribution ── */}
        <div className="pf-tr-summary">
          <span className="pf-tr-count">
            {filtered.length}<span className="muted"> of {traces.length}</span> decisions
          </span>
          <span className="pf-tr-tally">
            {DECISIONS.map((d) => (
              <span key={d} className={`pf-dash-chip ${chipTone(d)}`} style={{ opacity: tally[d] ? 1 : 0.35 }}>
                {tally[d]} {d.toLowerCase()}
              </span>
            ))}
          </span>
          {activeFilters > 0 && (
            <button className="pf-dash-link" type="button" onClick={clearAll}>
              Clear filters ✕ ({activeFilters})
            </button>
          )}
        </div>
      </section>

      <section className="pf-panel" style={{ marginTop: 14 }}>
        {status === "error" && (
          <div className="pf-dash-feed-status error">Couldn’t load traces ({error}).</div>
        )}
        {status !== "error" && filtered.length === 0 && (
          <div className="pf-dash-feed-status">
            {traces.length === 0 ? "No governed decisions recorded — this log fills only for demos with a governed orchestrator (see the Findings tab for shadow-evaluation evidence)." : "No decisions match these filters."}
          </div>
        )}
        {filtered.length > 0 && (
          <table className="pf-dash-table pf-tr-table">
            <thead>
              <tr>
                <th>When</th><th>Decision</th><th>Agent · Caller</th><th>Intent</th><th>Policy</th><th>Reason</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((t) => (
                <tr key={t.id}>
                  <td className="pf-tr-when">{fmtWhen(t.createdAt)}</td>
                  <td><span className={`pf-dash-chip ${chipTone(t.decision)}`}>{t.decision}</span></td>
                  <td>
                    <div className="pf-tr-agent">{roleAgents[t.role] || "Agent"}</div>
                    <div className="muted">{t.caller} · {t.role}</div>
                  </td>
                  <td>
                    <code className="pf-tr-intent">{t.intent || "—"}</code>
                    <span className="muted pf-tr-args">{argSummary(t.args)}</span>
                  </td>
                  <td>{t.policy ? <code className="pf-tr-policy">{t.policy}</code> : <span className="muted">—</span>}</td>
                  <td className="pf-tr-reason">{(t.reasons && t.reasons[0]) || t.outcome || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
      </>}
    </main>
  );
}
