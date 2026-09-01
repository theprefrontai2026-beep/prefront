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
import CopyLink from "./CopyLink";
import { currentLoc, useLoc } from "../lib/router";
import { findingHref, findingsHref, navTo, onTab } from "../routes";
import type { FeedDecision, Trace } from "../hooks/useDecisionFeed";
import { DEMOS, type DemoConfig } from "../demos";
import { SessionFlyout, parseSource, type EvalVerdict } from "./Observability";
import { severityOf, SEVERITY_META, SEVERITY_ORDER, type SeverityLevel, type SeverityRule } from "../severity";
import { useSeverityRules } from "../hooks/useSeverityRules";
import { CLEAR_ALL_CONFIRM, clearAllTraceData } from "../api";

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

// When a record happened, for display AND for ordering: the activity's own
// time, falling back to the evaluation time only when the spans are gone (see
// EvalVerdict.occurred_at). Both are ISO-8601 with a zone, so a string parse
// is safe; an unparseable/empty value sorts last rather than to 1970.
function whenOf(r: EvalVerdict): number {
  const t = Date.parse(r.occurred_at || r.evaluated_at);
  return Number.isNaN(t) ? -Infinity : t;
}

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
  // A satisfied row isn't "wrong" - it's positive evidence, so state the
  // policy/rule the clean session was checked against and satisfied: the cited
  // section (Family 1 Policy / Family 3 Conformance) or, when there's no
  // citation (Family 2 Integrity invariants), the check that passed.
  if (r.status === "satisfied") {
    const section = parseSource(r.source)?.section || "";
    const text = r.detail || (section ? `§${section}` : r.check_id);
    return <div className="pf-tr-truncate pf-find-detail" title={r.detail || section || r.check_id}>✓ {text}</div>;
  }
  return (
    <div className="pf-tr-truncate pf-find-detail" title={r.detail}>{r.detail}</div>
  );
}

// A compact horizontal bar distribution (family or severity) shown above the
// Findings filters, reflecting the selected time range.
function DistBars({ title, rows }: { title: string; rows: { label: string; count: number; tone: string }[] }) {
  const max = Math.max(1, ...rows.map((r) => r.count));
  return (
    <div className="pf-find-dist">
      <div className="pf-find-dist-title">{title}</div>
      {rows.map((r) => (
        <div key={r.label} className="pf-find-dist-row">
          <span className="pf-find-dist-label" title={r.label}>{r.label}</span>
          <span className="pf-find-dist-track"><span className={`pf-find-dist-fill ${r.tone}`} style={{ width: `${(r.count / max) * 100}%` }} /></span>
          <span className="pf-find-dist-count">{r.count}</span>
        </div>
      ))}
    </div>
  );
}

const FAMILY_DIST: { key: string; label: string; tone: string }[] = [
  { key: "family1", label: "Policy", tone: "blue" },
  { key: "family2", label: "Integrity", tone: "teal" },
  { key: "family3", label: "Conformance", tone: "purple" },
];

// Outcome vocabulary for the unified feed. Violated leads (triage), then
// indeterminate, then satisfied — so a session's violations stay at the top of
// the table even though clean/satisfied rows now share it.
const OUTCOME_META: Record<string, { label: string; tone: string; rank: number }> = {
  violated:      { label: "violated",      tone: "red",   rank: 0 },
  indeterminate: { label: "indeterminate", tone: "amber", rank: 1 },
  satisfied:     { label: "satisfied",     tone: "green", rank: 2 },
};
const outcomeMeta = (s: string) => OUTCOME_META[s] || { label: s || "—", tone: "slate", rank: 3 };
const OUTCOME_ORDER = ["violated", "indeterminate", "satisfied"];

// `initialEffect` / `initialSeverity` let the Overview's tiles deep-link here
// prefiltered (block / approval_required / flag, or a severity level); each
// re-applies whenever it changes so a second click from the Overview isn't
// ignored. `rules` is the customer's severity mapping (severity is derived per
// row from family+effect, first-match-wins).
type LinkedFinding = { sessionId: string; spanId: string | null; eventId: string | null;
                      detail: string; source: string; status: string;
                      // The whole verdict when it resolved, so the flyout can
                      // name the check and where its expectation is declared
                      // without re-finding the row — and can still do it for a
                      // record whose check is disabled, which its own
                      // per-session fetch would filter out.
                      verdict: EvalVerdict | null };

/**
 * Reconstruct the open finding from the URL, for a link someone was sent.
 *
 * `event_id` is not server-resolvable on its own — /eval/verdicts takes only
 * status|check_id|family|limit|offset|since — so resolution is three-tiered:
 *   (a) find it in the newest-1000 rows this table already fetched;
 *   (b) otherwise GET /eval/sessions/{sid}/verdicts and match the event id
 *       (covers a finding older than that window);
 *   (c) otherwise open the session anyway with no finding banner. The flyout
 *       fetches /oob/sessions/{id} itself and the eval pipeline is eventually
 *       consistent, so the id STAYS in the URL and the page self-heals.
 */
function useLinkedFinding(
  sessionId: string | null,
  eventId: string | null,
  spanId: string | null,
  rows: EvalVerdict[],
  listStatus: "loading" | "ready" | "error",
): LinkedFinding | null {
  const [fetched, setFetched] = useState<EvalVerdict | null>(null);
  const [tried, setTried] = useState("");

  const inList = useMemo(
    () => (sessionId && eventId ? rows.find((r) => r.event_id === eventId) ?? null : null),
    [rows, sessionId, eventId],
  );

  useEffect(() => {
    const key = `${sessionId}|${eventId}`;
    // `tried` keeps a re-render (this component re-renders on every keystroke
    // in the filter row) from re-issuing the lookup.
    if (!sessionId || !eventId || inList || listStatus !== "ready" || tried === key) return;
    setTried(key);
    let alive = true;
    fetch(`/eval/sessions/${encodeURIComponent(sessionId)}/verdicts`)
      .then((r) => r.json())
      .then((j) => { if (alive) setFetched((j.verdicts || []).find((v: EvalVerdict) => v.event_id === eventId) ?? null); })
      .catch(() => { /* tier (c) */ });
    return () => { alive = false; };
  }, [sessionId, eventId, inList, listStatus, tried]);

  if (!sessionId) return null;
  const r = inList ?? fetched;
  return r
    ? { sessionId, spanId: r.evidence_span_ids?.[0] ?? spanId, eventId: r.event_id || null,
        detail: r.detail, source: r.source, status: r.status, verdict: r }
    : { sessionId, spanId, eventId, detail: "", source: "", status: "", verdict: null };
}

function FindingsSection({ initialEffect = "", initialSeverity = "", rules, active = true }: {
  initialEffect?: string; initialSeverity?: string; rules: SeverityRule[]; active?: boolean;
}) {
  const [rows, setRows] = useState<EvalVerdict[]>([]);
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");
  const [error, setError] = useState("");
  const [clearing, setClearing] = useState(false);
  const [clearError, setClearError] = useState("");
  // The open finding is URL state, not component state: /traces/findings/
  // <session_id>?event=<event_id>&span=<span_id>. That id trio is what a
  // shared link carries, and the flyout is reconstructed from it below.
  const loc = useLoc();
  const here = onTab(loc.segs, "traces");
  const openSession = here ? loc.segs[2] ?? null : null;
  const openEvent = here ? loc.query.get("event") : null;

  // ── Filters, one per displayed column ──
  const [range, setRange] = useState<number | null>(86400);
  const [eventId, setEventId] = useState("");
  const [family, setFamily] = useState("");
  const [checkId, setCheckId] = useState("");
  const [outcome, setOutcome] = useState("");
  const [effect, setEffect] = useState(initialEffect);
  const [severity, setSeverity] = useState(initialSeverity);
  const [policyNum, setPolicyNum] = useState("");
  const [q, setQ] = useState("");
  // Not a filter — a rollup of the satisfied rows (see `displayed` below).
  const [collapse, setCollapse] = useState(true);
  // Records written by a check that has since been DISABLED in Settings.
  // eval-engine hides them on every normal read (they are hidden, never
  // deleted — disabling is reversible), so the feed is fetched with
  // `include_disabled` and they are hidden HERE instead, behind a control
  // that says how many there are. Otherwise the only way to find out what a
  // disabled check is holding back is to turn it back on.
  const [disabledChecks, setDisabledChecks] = useState<string[]>([]);
  const [showHidden, setShowHidden] = useState(false);
  useEffect(() => { setEffect(initialEffect); if (initialEffect) setRange(null); }, [initialEffect]);
  useEffect(() => { setSeverity(initialSeverity); if (initialSeverity) setRange(null); }, [initialSeverity]);

  const sevOf = useCallback((r: EvalVerdict): SeverityLevel => severityOf({ family: r.family, effect: r.effect }, rules), [rules]);

  const load = useCallback(async () => {
    setStatus("loading");
    setError("");
    try {
      // The most recent 1000 (server-sorted by evaluated_at DESC), filtered
      // further client-side below - same fetch-a-slice-then-slice-and-dice
      // pattern as the Decisions log above. /eval/verdicts is the UNIFIED feed
      // (every status), so a clean session shows up too, associated with the
      // policy/rule it satisfied - not /eval/findings, which is violations
      // only. The cap is higher than the old findings-only 500 because a clean
      // deployment emits far more satisfied rows than violations, and we don't
      // want those to push older violations past the window (violations still
      // sort to the top regardless).
      const res = await fetch("/eval/verdicts?limit=1000&include_disabled=true");
      const json = await res.json();
      if (!res.ok) throw new Error(json?.error || `${res.status} ${res.statusText}`);
      setRows(json.verdicts || []);
      setDisabledChecks(json.disabled_checks || []);
      setStatus("ready");
    } catch (e: any) {
      setError(String(e?.message || e));
      setStatus("error");
    }
  }, []);

  const clearData = useCallback(async () => {
    if (!window.confirm(CLEAR_ALL_CONFIRM)) return;
    setClearing(true);
    setClearError("");
    try {
      const res = await clearAllTraceData(DEMOS.map((demo) => demo.id));
      if (!res.ok) {
        setClearError(`Everything else cleared, but the Phoenix purge failed (${res.phoenixError}) — its traces will be re-pulled on the next poll.`);
      }
      // A finding flyout open on a row that no longer exists would sit there
      // resolving nothing, so close it before reloading.
      if (onTab(currentLoc().segs, "traces")) navTo(findingsHref(), { replace: true });
      await load();
    } catch (e: any) {
      setClearError(String(e?.message || e));
    } finally { setClearing(false); }
  }, [load]);

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

  const isHidden = useCallback((r: EvalVerdict) => disabledChecks.includes(r.check_id), [disabledChecks]);

  // Everything matching the column filters, INCLUDING the disabled-check
  // records — `filtered` below drops those unless they're being shown, and
  // the difference between the two is the count the control offers.
  const matched = useMemo(() => {
    const needle = q.trim().toLowerCase();
    const cutoff = range ? Date.now() - range * 1000 : null;
    const out = rows.filter((r) => {
      // Windowed on when it HAPPENED (whenOf), the same clock the When column
      // shows — filtering on the evaluation time would make "last 1h" mean
      // "evaluated in the last hour", which after any re-evaluation is
      // everything the engine still has.
      if (cutoff && whenOf(r) < cutoff) return false;
      if (eventId.trim() && !r.event_id.includes(eventId.trim())) return false;
      if (family && famOf(r) !== family) return false;
      if (checkId && r.check_id !== checkId) return false;
      if (outcome && r.status !== outcome) return false;
      if (effect && r.effect !== effect) return false;
      // Severity is a violation-triage concept — a satisfied row has none, so
      // only apply the severity filter to rows that are actually violations.
      if (severity && (r.status === "satisfied" || sevOf(r) !== severity)) return false;
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
    // Chronological, newest first, oldest at the bottom — full stop. This used
    // to lead with a triage order (violations, then severity, then time),
    // which reads as a ranking rather than a log and scatters one session's
    // records across the table; the satisfied rollup above is what keeps a
    // violation from being buried now. Ties (same instant, common inside one
    // session) fall back to the event id, which is a monotonic serial, so the
    // order is total and stable rather than dependent on the sort's stability.
    return out.sort((a, b) =>
      whenOf(b) - whenOf(a)
      || (Number(b.event_id || 0) - Number(a.event_id || 0)));
  }, [rows, range, eventId, family, checkId, outcome, effect, severity, policyNum, q, sevOf]);

  const hiddenMatches = useMemo(() => matched.filter(isHidden).length, [matched, isHidden]);
  const filtered = useMemo(
    () => (showHidden ? matched : matched.filter((r) => !isHidden(r))),
    [matched, showHidden, isHidden],
  );

  // ── One session's satisfied checks collapse to a single row ────────────
  // Every check that ran emits a verdict, so ONE scenario lands ~10 rows here
  // and its two real violations read as a minority of a mostly-green list.
  // Per session: if something went wrong, the satisfied rows are noise beside
  // it — drop them; if nothing did, keep exactly ONE as the evidence that the
  // session was checked and came back clean (dropping them all would make a
  // clean session look unevaluated, which is the opposite of this table's
  // point). Grouping is per session because that's the unit a check runs
  // over — a feed of many sessions still shows every session.
  // Skipped whenever Outcome is filtered explicitly: someone who asked for
  // satisfied rows and got one per session would read that as a bug.
  const collapsing = collapse && !outcome;
  const { displayed, collapsed } = useMemo(() => {
    if (!collapsing) return { displayed: filtered, collapsed: 0 };
    // Both halves read the ENABLED rows only, so revealing the disabled-check
    // records can only ever add rows to the table, never silently remove one
    // (a hidden violation deciding that a session's satisfied row must go
    // would make the reveal toggle change things it has no business changing).
    const dirty = new Set(filtered.filter((r) => r.status !== "satisfied" && !isHidden(r)).map((r) => r.session_id));
    const kept = new Set<string>();
    const out = filtered.filter((r) => {
      if (r.status !== "satisfied" || isHidden(r)) return true;
      if (dirty.has(r.session_id) || kept.has(r.session_id)) return false;
      kept.add(r.session_id);
      return true;
    });
    return { displayed: out, collapsed: filtered.length - out.length };
  }, [filtered, collapsing, isHidden]);

  // The open finding, resolved from the URL (see useLinkedFinding above).
  const flyout = useLinkedFinding(openSession, openEvent, loc.query.get("span"), rows, status);

  // Distribution over the selected time range only (independent of the column
  // filters), so the family/severity charts always show the full breakdown for
  // the chosen period.
  // Never counts a disabled check's records, even while they're being shown:
  // eval-engine keeps them out of every aggregate it serves (/eval/status, the
  // compliance report), and a chart here that disagreed with those would be
  // worse than the one missing row. Revealing them is for reading individual
  // records, not for restating the deployment's numbers.
  const rangeRows = useMemo(() => {
    const live = rows.filter((r) => !isHidden(r));
    if (!range) return live;
    const cutoff = Date.now() - range * 1000;
    return live.filter((r) => whenOf(r) >= cutoff);
  }, [rows, range, isHidden]);
  const familyDist = useMemo(
    () => FAMILY_DIST.map((f) => ({ label: f.label, tone: f.tone, count: rangeRows.filter((r) => r.family === f.key).length })),
    [rangeRows],
  );
  const severityDist = useMemo(
    () => SEVERITY_ORDER.map((s) => ({ label: SEVERITY_META[s].label, tone: SEVERITY_META[s].tone, count: rangeRows.filter((r) => r.status === "violated" && sevOf(r) === s).length })),
    [rangeRows, sevOf],
  );
  const outcomeDist = useMemo(
    () => OUTCOME_ORDER.map((s) => ({ label: outcomeMeta(s).label, tone: outcomeMeta(s).tone, count: rangeRows.filter((r) => r.status === s).length })),
    [rangeRows],
  );
  const rangePhrase = range ? `last ${FINDING_RANGES.find((r) => r.seconds === range)?.label ?? ""}` : "all time";

  const activeFilters = (eventId.trim() ? 1 : 0) + (family ? 1 : 0) + (checkId ? 1 : 0) + (outcome ? 1 : 0) + (effect ? 1 : 0) + (severity ? 1 : 0) + (policyNum ? 1 : 0) + (q.trim() ? 1 : 0);
  const clearAll = () => { setEventId(""); setFamily(""); setCheckId(""); setOutcome(""); setEffect(""); setSeverity(""); setPolicyNum(""); setQ(""); };

  return (
    <>
      <section className="pf-panel">
        <div className="pf-dash-panel-head">
          <h2>Decision evidence</h2>
          <div className="pf-dash-panel-actions">
          <button className="pf-dash-link" type="button" onClick={load} disabled={status === "loading"}>
            {status === "loading" ? "Loading…" : "Refresh ↻"}
          </button>
          {/* One button, everything gone. Clearing eval-engine's verdicts alone
              would look like it worked and then undo itself: the spans stay in
              ClickHouse, oob-ingest keeps re-pulling from Phoenix, and the
              worker re-evaluates — the rows are back within a poll. So this
              fires the same full sequence as Observability's own control
              (api.ts's clearAllTraceData: Phoenix, then spans, verdicts and the
              governed decision log), rather than a partial clear that reads as
              a bug. */}
          <button className="pf-btn sm reject" type="button" onClick={clearData} disabled={clearing}
                  title="Phoenix projects, every ClickHouse table (spans, findings, conformance) and the governed decision log. The lifetime counters behind /api/stats are cumulative by design and survive.">
            {clearing ? "Clearing…" : "Clear all data"}
          </button>
          </div>
        </div>
        {clearError && <div className="pf-dash-feed-status error">{clearError}</div>}
        <p className="pf-hint" style={{ marginTop: 0 }}>
          eval-engine's shadow evaluation of every ingested session — <strong>every outcome</strong>, not
          only violations (see eval-engine/CLAUDE.md). A clean session isn't absent: it shows as
          one <em>satisfied</em> row, associated with the policy or business rule it was checked
          against — a session that has violations shows those instead, its satisfied checks rolled
          up into the count below.
          Never on the request path; nothing here blocked anything — it's what the checks found after
          the fact.
        </p>

        {rangeRows.length > 0 && (
          <div className="pf-find-dists">
            <DistBars title={`Outcome · ${rangePhrase}`} rows={outcomeDist} />
            <DistBars title={`Families · ${rangePhrase}`} rows={familyDist} />
            <DistBars title={`Severity of violations · ${rangePhrase}`} rows={severityDist} />
          </div>
        )}

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
            <Select label="Outcome" value={outcome} options={OUTCOME_ORDER} onChange={setOutcome} />
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
            {displayed.length}<span className="muted"> of {showHidden ? rows.length : rows.filter((r) => !isHidden(r)).length}</span> records
          </span>
          {collapsing && collapsed > 0 && (
            <button className="pf-dash-link" type="button" onClick={() => setCollapse(false)}
                    title="Show every satisfied check, one row per check that ran">
              Show {collapsed} more satisfied {collapsed === 1 ? "check" : "checks"}
            </button>
          )}
          {!collapse && (
            <button className="pf-dash-link" type="button" onClick={() => setCollapse(true)}>
              Collapse satisfied checks
            </button>
          )}
          {/* Records a disabled check wrote before it was switched off. They
              are hidden, NOT deleted — re-enabling the check brings them back
              on its own — so the honest control is one that says how many
              there are and shows them on request. */}
          {hiddenMatches > 0 && (
            <button className="pf-dash-link" type="button" onClick={() => setShowHidden((v) => !v)}
                    title={`Written by ${disabledChecks.join(", ")} — disabled in Settings › Checks, so normally hidden. Nothing was deleted; re-enabling the check restores them everywhere.`}>
              {showHidden
                ? `Hide ${hiddenMatches} from disabled check${disabledChecks.length === 1 ? "" : "s"}`
                : `Show ${hiddenMatches} hidden by disabled check${disabledChecks.length === 1 ? "" : "s"}`}
            </button>
          )}
          {activeFilters > 0 && (
            <button className="pf-dash-link" type="button" onClick={clearAll}>
              Clear filters ✕ ({activeFilters})
            </button>
          )}
        </div>
      </section>

      <section className="pf-panel" style={{ marginTop: 14 }}>
        {status === "error" && <div className="pf-dash-feed-status error">Couldn’t load decision evidence ({error}).</div>}
        {status !== "error" && displayed.length === 0 && (
          <div className="pf-dash-feed-status">
            {rows.length === 0 ? "No evaluated sessions yet — run a scenario against a demo agent, then eval-engine's shadow evaluation appears here." : "Nothing matches these filters."}
          </div>
        )}
        {displayed.length > 0 && (
          <table className={`pf-dash-table pf-tr-table pf-find-table ${showHidden && hiddenMatches > 0 ? "showing-off" : ""}`}>
            {/* `showing-off` widens the Outcome column for the "off" tag — the
               table is fixed-layout, so the tag would be clipped otherwise. */}
            {/* Check and Policy stay filterable above (families/checks/policies
               dropdowns) but aren't shown as columns here - narrower table,
               less redundant with the one-liner + flyout. Every column is
               width-capped (.pf-tr-truncate) with the full value on hover
               (native title tooltip) so long text never blows out the layout. */}
            <thead>
              <tr><th>Event</th><th>When</th><th>Outcome</th><th>Severity</th><th>Family</th><th>Effect</th><th>User query</th><th>Policy / detail</th><th aria-label="Share" /></tr>
            </thead>
            <tbody>
              {displayed.map((r, i) => {
                const sev = sevOf(r);
                const om = outcomeMeta(r.status);
                const satisfied = r.status === "satisfied";
                const hidden = isHidden(r);
                return (
                <tr key={r.event_id || r.session_id + r.check_id + r.evidence_excerpt + i} className={`clickable ${hidden ? "pf-find-off" : ""}`}
                    onClick={() => navTo(findingHref(r.session_id, r.event_id, r.evidence_span_ids?.[0] ?? null))}>
                  <td className="mono pf-tr-truncate narrow" title={r.event_id || undefined}>{r.event_id || "—"}</td>
                  <td className="pf-tr-when"
                      title={`${r.occurred_at ? `Happened ${findingWhen(r.occurred_at)}` : "Time unknown — the session's spans are no longer stored"} · evaluated ${findingWhen(r.evaluated_at)}`}>
                    {findingWhen(r.occurred_at || r.evaluated_at)}
                  </td>
                  <td className="pf-find-outcome-cell">
                    <span className={`pf-dash-chip ${om.tone}`}>{om.label}</span>
                    {hidden && (
                      <span className="pf-dash-chip slate pf-find-off-tag" title={`${r.check_id} is disabled in Settings › Checks — this record is normally hidden everywhere, but it was never deleted.`}>
                        off
                      </span>
                    )}
                  </td>
                  {/* Severity is a violation-triage rating; a satisfied row has none. */}
                  <td>{satisfied ? <span className="muted">—</span> : <span className={`pf-dash-chip ${SEVERITY_META[sev].tone}`}>{SEVERITY_META[sev].label}</span>}</td>
                  <td className="pf-tr-truncate" title={famOf(r)}>{famOf(r)}</td>
                  <td>{r.effect ? <span className={`pf-dash-chip ${r.effect === "block" ? "red" : r.effect === "approval_required" ? "amber" : "teal"}`}>{r.effect}</span> : <span className="muted">—</span>}</td>
                  <td className="pf-tr-truncate" title={r.user_query || undefined}>{r.user_query || <span className="muted">—</span>}</td>
                  <td><WhatWentWrong r={r} /></td>
                  {/* Share THIS event — the link opens straight into its flyout. */}
                  <td className="pf-tr-share"><CopyLink href={findingHref(r.session_id, r.event_id, r.evidence_span_ids?.[0] ?? null)}
                                                        title="Copy a link to this event" /></td>
                </tr>
              );})}
            </tbody>
          </table>
        )}
      </section>

      {flyout && (
        <SessionFlyout sessionId={flyout.sessionId} initialSpanId={flyout.spanId} eventId={flyout.eventId}
                       findingDetail={flyout.detail} findingSource={flyout.source} findingStatus={flyout.status}
                       findingVerdict={flyout.verdict} refreshKey={0}
                       shareHref={findingHref(flyout.sessionId, flyout.eventId, flyout.spanId)}
                       onClose={() => navTo(findingsHref())} />
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
  const [internal, setInternal] = useState<TracesSection>("findings");
  const rawSection = controlled ?? internal;
  const setSection = (s: TracesSection) => { setInternal(s); onSection?.(s); };
  // The Decisions view is disabled: the /api/decisions store is empty for the
  // active (ungoverned) demo, so Decision Traces shows Findings only. The
  // decisions markup below is retained but never rendered.
  const section = "findings" as TracesSection;
  void rawSection;
  void setSection;
  // Canonicalise a bare /traces to /traces/findings (replace — it is an app
  // correction, not a place the user navigated to, so Back skips it). The URL
  // grammar already has /traces/decisions for when that view comes back.
  const tracesLoc = useLoc();
  useEffect(() => {
    if (active && tracesLoc.segs[0] === "traces" && !tracesLoc.segs[1]) navTo(findingsHref(), { replace: true });
  }, [active, tracesLoc.segs]);
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
      {/* Decisions sub-tab hidden for now — this tab shows Findings only, so the
          single-button sub-nav is dropped. `setSection` stays wired below for
          when it's restored. */}
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
