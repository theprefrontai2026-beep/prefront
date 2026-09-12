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

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import CopyLink from "./CopyLink";
import { currentLoc, useLoc } from "../lib/router";
import { decisionsHref, findingHref, findingsHref, navTo, onTab,
         tracesSectionFromPath, type TracesSection } from "../routes";
import type { FeedDecision, Trace } from "../hooks/useDecisionFeed";
import { DEMOS, type DemoConfig } from "../demos";
import { SessionFlyout, parseSource, type EvalVerdict } from "./Observability";
import { severityOf, SEVERITY_META, SEVERITY_ORDER, type SeverityLevel, type SeverityRule } from "../severity";
import { useSeverityRules } from "../hooks/useSeverityRules";
import { clearConfirm, clearAllTraceData } from "../api";

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
/** Model headlines for this app's findings, keyed by event id.
 *
 *  Two ways in, because neither alone is enough. The background explainer
 *  (semantic-layer) writes them as findings appear, and `headlines` polls that
 *  store — a read that never calls a model. But a finding created a moment ago
 *  has not been reached yet, and a reader looking straight at it should not
 *  have to wait for a poll, so `ensure` asks for the rows ON SCREEN now. Both
 *  land in the same server-side cache, so neither pays twice, and a finding
 *  with no summary either way shows the check's own wording. */
const HEADLINE_FILL_MAX = 20;

function useFindingHeadlines(app: string): {
  headlines: Record<string, string>;
  ensure: (rows: EvalVerdict[]) => void;
  /** Rows whose summary is being written right now, so the cell can say so
   *  rather than showing the rule text the summary is about to replace. */
  pending: Set<string>;
} {
  const [m, setM] = useState<Record<string, string>>({});
  const [pending, setPending] = useState<Set<string>>(new Set());
  const inFlight = useRef<Set<string>>(new Set());
  // Mirrors of state for `ensure`, which must not re-run when they change:
  // depending on the map would rebuild the callback on every fill and re-fire
  // the effect that calls it.
  const have = useRef<Record<string, string>>({});
  const asked = useRef<Set<string>>(new Set());

  useEffect(() => {
    let alive = true;
    have.current = {};
    asked.current = new Set();
    inFlight.current = new Set();
    setM({});
    setPending(new Set());
    const load = () =>
      fetch(`/design/semantic/findings/explanations?app=${encodeURIComponent(app)}`)
        .then((r) => (r.ok ? r.json() : null))
        .then((j) => {
          if (!alive || !j) return;
          const ex: Record<string, { headline?: string }> = j.explanations || {};
          const next = Object.fromEntries(Object.entries(ex).map(([k, v]) => [k, String(v.headline || "")]));
          have.current = { ...have.current, ...next };
          setM(have.current);
        })
        .catch(() => {});
    load();
    // Matched to the explainer's own poll: a summary written in the background
    // should reach the table in about the time it took to write.
    const id = window.setInterval(load, 10000);
    return () => { alive = false; window.clearInterval(id); };
  }, [app]);

  const ensure = useCallback((rows: EvalVerdict[]) => {
    const wanted = rows
      .filter((r) => r.event_id && r.status !== "satisfied" && r.detail
                     && !have.current[r.event_id] && !asked.current.has(r.event_id))
      .slice(0, HEADLINE_FILL_MAX);
    if (!wanted.length) return;
    for (const r of wanted) {
      asked.current.add(r.event_id);
      inFlight.current.add(r.event_id);
    }
    setPending(new Set(inFlight.current));
    const settle = (id: string) => {
      inFlight.current.delete(id);
      setPending(new Set(inFlight.current));
    };
    for (const r of wanted) {
      fetch("/design/semantic/findings/explain", {
        method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({
          check_id: r.check_id, rule_id: r.rule_id, family_label: r.family_label || "",
          status: r.status, effect: r.effect, detail: r.detail,
          evidence_excerpt: r.evidence_excerpt, source: r.source, user_query: r.user_query,
          indeterminate_reason: r.indeterminate_reason || "",
          event_id: r.event_id, app_id: r.app_id || app,
        }),
      })
        .then((res) => (res.ok ? res.json() : Promise.reject(new Error(String(res.status)))))
        .then((j) => {
          const headline = String(j.headline || "");
          if (!headline) return;
          have.current = { ...have.current, [r.event_id]: headline };
          setM(have.current);
        })
        // Left for the background explainer and the next poll; the row falls
        // back to the check's own wording meanwhile.
        .catch(() => { asked.current.delete(r.event_id); })
        .finally(() => settle(r.event_id));
    }
  }, [app]);

  return { headlines: m, ensure, pending };
}

function WhatWentWrong({ r, headline, summarising }: {
  r: EvalVerdict; headline?: string; summarising?: boolean;
}) {
  // A satisfied row isn't "wrong" - it's positive evidence, so state the
  // policy/rule the clean session was checked against and satisfied: the cited
  // section (Family 1 Policy / Family 3 Conformance) or, when there's no
  // citation (Family 2 Integrity invariants), the check that passed.
  if (r.status === "satisfied") {
    const section = parseSource(r.source)?.section || "";
    const text = r.detail || (section ? `§${section}` : r.check_id);
    return <div className="pf-tr-truncate pf-find-detail" title={r.detail || section || r.check_id}>✓ {text}</div>;
  }
  // A newly arrived finding is on screen before its summary exists — the
  // summary is one model call behind the row. Say so for that second or two
  // rather than showing the rule text it is about to replace; the check's own
  // wording is on hover throughout, and returns if the summary fails.
  if (!headline && summarising) {
    return <div className="pf-tr-truncate pf-find-detail pf-find-summarising" title={r.detail}>Summarising…</div>;
  }
  // The model's headline when one has been written; the check's own wording
  // is always on hover, and is what shows until then.
  return (
    <div className="pf-tr-truncate pf-find-detail"
         title={headline ? `${headline}\n\nThe check's own wording: ${r.detail}` : r.detail}>
      {headline || r.detail}
    </div>
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

/* ── Live follow ──────────────────────────────────────────────────────────
 *
 * The findings feed is a LOG of an ongoing process: eval-engine evaluates a
 * session ~10-20s after it ends, so a page opened while scenarios are running
 * is stale seconds later and there was no way to tell — the table simply sat
 * there looking complete. These few pieces make it follow the feed instead.
 */

/** Milliseconds between live polls.
 *
 *  This is FASTER than the feed can actually change: eval-engine's own worker
 *  runs on EVAL_POLL_SECONDS (10s) plus a quiet window, so most ticks re-read
 *  a set that has not moved. That is deliberate — it buys latency at the one
 *  moment the page is being watched, and an unchanged read is nearly free
 *  because `apply` drops it before it touches state (no re-render, no memo
 *  recompute). What it costs is the request itself, which is why the two
 *  on-screen gates in useLivePoll matter more at this cadence than they would
 *  at the engine's own. */
const LIVE_INTERVAL_MS = 3_000;

/** How long a newly-arrived row stays highlighted. Long enough to catch the
 *  eye on a page you are already reading, short enough that a busy feed does
 *  not end up entirely highlighted. */
const LIVE_HIGHLIGHT_MS = 8_000;

const LIVE_KEY = "pf.findings.live";

/** Live follow is ON unless this reader has turned it off before. Persisted
 *  rather than reset per visit: a preference you have to set again every time
 *  is not a preference. Storage can throw outright (private mode, site data
 *  blocked), so every access falls back to the default rather than taking the
 *  page down with it. */
function liveDefault(): boolean {
  try { return window.localStorage.getItem(LIVE_KEY) !== "off"; } catch { return true; }
}
function storeLive(on: boolean): void {
  try { window.localStorage.setItem(LIVE_KEY, on ? "on" : "off"); } catch { /* preference only */ }
}

/** Identity of a row for arrival tracking. `event_id` is a monotonic serial and
 *  is what the table already keys on; the composite is the fallback for a row
 *  served without one. Deliberately NOT the table's `key`, which mixes in the
 *  array index and so changes for a row that merely moved. */
function rowKey(r: EvalVerdict): string {
  return r.event_id || `${r.session_id}|${r.check_id}|${r.evidence_excerpt}`;
}

const sameList = (a: string[], b: string[]) => a.length === b.length && a.every((v, i) => v === b[i]);

/** Run `tick` on an interval, but ONLY while this page is actually being
 *  looked at. Both halves are load-bearing and neither implies the other:
 *
 *   - `enabled` carries the section's own `active` flag. App.tsx keeps every
 *     tab MOUNTED and toggles `tab-hidden` so tab state survives navigation,
 *     which means a component on another tab keeps running its effects —
 *     without this the feed would poll forever from behind whatever page you
 *     are actually on.
 *   - `document.visibilityState` is the browser tab being foregrounded. A
 *     backgrounded tab polling every few seconds is a ClickHouse read per tick
 *     nobody can see.
 *
 * Two smaller guarantees: a tick never overlaps its predecessor (`busy` holds
 * the slot, since each one is a full re-read of the feed), and coming back
 * from a backgrounded tab ticks IMMEDIATELY rather than waiting out an
 * interval — that moment is precisely when the page is most stale. Returning
 * to this tab from another one does NOT double-tick, because the section
 * already reloads on `active`.
 */
function useLivePoll(enabled: boolean, intervalMs: number, tick: () => Promise<void>): boolean {
  const busy = useRef(false);
  const wasHidden = useRef(false);
  const latest = useRef(tick);
  latest.current = tick;

  const [visible, setVisible] = useState(
    () => typeof document === "undefined" || document.visibilityState !== "hidden");

  useEffect(() => {
    const onChange = () => {
      const now = document.visibilityState !== "hidden";
      if (!now) wasHidden.current = true;
      setVisible(now);
    };
    document.addEventListener("visibilitychange", onChange);
    return () => document.removeEventListener("visibilitychange", onChange);
  }, []);

  const on = enabled && visible;

  useEffect(() => {
    if (!on) return;
    let alive = true;
    const run = async () => {
      if (busy.current || !alive) return;
      busy.current = true;
      try { await latest.current(); } finally { busy.current = false; }
    };
    const id = window.setInterval(run, intervalMs);
    if (wasHidden.current) { wasHidden.current = false; void run(); }
    return () => { alive = false; window.clearInterval(id); };
  }, [on, intervalMs]);

  return on;
}

function FindingsSection({ initialEffect = "", initialSeverity = "", rules, active = true, app, project, appLabel }: {
  initialEffect?: string; initialSeverity?: string; rules: SeverityRule[]; active?: boolean;
  app: string; project: string; appLabel: string;
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

  // ── Live follow (see the block above this component) ──
  const [live, setLive] = useState(liveDefault);
  // A poll that fails must not replace a working table with an error banner —
  // one blip between two good reads is not a broken page — so its error lands
  // here, beside the toggle, and the rows stay put.
  const [liveError, setLiveError] = useState("");
  // Rows that arrived while you were looking, briefly highlighted so a new
  // finding announces itself instead of silently shifting the table down.
  const [fresh, setFresh] = useState<Set<string>>(new Set());
  const seen = useRef<Set<string>>(new Set());
  const primed = useRef(false);

  // Switching application replaces the whole feed, and none of it is an
  // "arrival" — without this every row of the newly selected application
  // would light up as new. Declared ABOVE the load effect so it resets the
  // baseline before the reload that follows an `app` change fills it.
  useEffect(() => {
    primed.current = false;
    seen.current = new Set();
    setFresh(new Set());
    setLiveError("");
  }, [app]);

  useEffect(() => {
    if (fresh.size === 0) return;
    const id = window.setTimeout(() => setFresh(new Set()), LIVE_HIGHLIGHT_MS);
    return () => window.clearTimeout(id);
  }, [fresh]);
  useEffect(() => { setEffect(initialEffect); if (initialEffect) setRange(null); }, [initialEffect]);
  useEffect(() => { setSeverity(initialSeverity); if (initialSeverity) setRange(null); }, [initialSeverity]);

  const sevOf = useCallback((r: EvalVerdict): SeverityLevel => severityOf({ family: r.family, effect: r.effect }, rules), [rules]);

  const { headlines, ensure: ensureHeadlines, pending: pendingHeadlines } = useFindingHeadlines(app);

  const fetchVerdicts = useCallback(async (): Promise<{ verdicts: EvalVerdict[]; disabled: string[] }> => {
    // The most recent 1000 (server-sorted by evaluated_at DESC), filtered
    // further client-side below - same fetch-a-slice-then-slice-and-dice
    // pattern as the Decisions log above. /eval/verdicts is the UNIFIED feed
    // (every status), so a clean session shows up too, associated with the
    // policy/rule it satisfied - not /eval/findings, which is violations
    // only. The cap is higher than the old findings-only 500 because a clean
    // deployment emits far more satisfied rows than violations, and we don't
    // want those to push older violations past the window (violations still
    // sort to the top regardless).
    // Scoped to this application (application_isolation_design.md Phase 2).
    // Unscoped, this feed showed every application's verdicts under whichever
    // app's label the page happened to be wearing.
    const res = await fetch(
      `/eval/verdicts?limit=1000&include_disabled=true&app=${encodeURIComponent(app)}`);
    const json = await res.json();
    if (!res.ok) throw new Error(json?.error || `${res.status} ${res.statusText}`);
    return { verdicts: json.verdicts || [], disabled: json.disabled_checks || [] };
  }, [app]);

  /** Fold a read into the table, whoever asked for it.
   *
   *  Two things happen here that a bare `setRows` would not, both because this
   *  now runs every few seconds rather than once per visit:
   *
   *  1. An IDENTICAL read is dropped before it touches state. A fresh `rows`
   *     array re-runs every filter, rollup and distribution memo over the whole
   *     feed, so handing one over for a result that cannot have changed is a
   *     full recompute per tick for nothing. Same count and nothing new means
   *     the same set, since a row never leaves without the count changing.
   *  2. Rows absent from the previous read are marked as ARRIVALS. The first
   *     read is the BASELINE — everything already there when you arrived is not
   *     an arrival — which is what `primed` distinguishes. Without it, opening
   *     the page would flash the entire table as new.
   */
  const apply = useCallback((verdicts: EvalVerdict[], disabled: string[]) => {
    const ids = verdicts.map(rowKey);
    const first = !primed.current;
    primed.current = true;
    const added = first ? [] : ids.filter((k) => !seen.current.has(k));
    const same = !first && added.length === 0 && ids.length === seen.current.size;
    if (!same) {
      seen.current = new Set(ids);
      setRows(verdicts);
    }
    // Returning `prev` unchanged makes React bail out of the re-render — same
    // reasoning as `same` above, for the far smaller disabled-check list.
    setDisabledChecks((prev) => (sameList(prev, disabled) ? prev : disabled));
    if (added.length) {
      setFresh((prev) => {
        const next = new Set(prev);
        added.forEach((k) => next.add(k));
        return next;
      });
    }
  }, []);

  /** The loud read: mount, becoming active, the Refresh button, after a clear.
   *  Shows its progress and reports failure as a page-level error. */
  const load = useCallback(async () => {
    setStatus("loading");
    setError("");
    try {
      const { verdicts, disabled } = await fetchVerdicts();
      apply(verdicts, disabled);
      setLiveError("");
      setStatus("ready");
    } catch (e: any) {
      setError(String(e?.message || e));
      setStatus("error");
    }
  }, [fetchVerdicts, apply]);

  /** The quiet read, on the live interval. It deliberately does NOT touch
   *  `status`: that drives the Refresh button's label and, more importantly,
   *  gates useLinkedFinding's per-session fallback lookup
   *  (`listStatus !== "ready"`), so flipping it to "loading" every tick would
   *  flicker the button and repeatedly re-arm that lookup underneath an open
   *  flyout. */
  const poll = useCallback(async () => {
    try {
      const { verdicts, disabled } = await fetchVerdicts();
      apply(verdicts, disabled);
      setLiveError("");
    } catch (e: any) {
      setLiveError(String(e?.message || e));
    }
  }, [fetchVerdicts, apply]);

  // Polls only while this page is genuinely on screen — see useLivePoll.
  const polling = useLivePoll(active && live, LIVE_INTERVAL_MS, poll);

  // Scoped to THIS application, matching Observability's button. This page is
  // labelled with one application; a clear on it that took out every other
  // application's evidence was mislabelling that could not be undone.
  const clearData = useCallback(async () => {
    if (!window.confirm(clearConfirm(appLabel, false))) return;
    setClearing(true);
    setClearError("");
    try {
      const res = await clearAllTraceData(DEMOS.map((demo) => demo.id), { appId: app, project });
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
  }, [load, app, project, appLabel]);

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

  /** Arrivals the reader can actually SEE — the badge sits beside this table,
   *  so it has to count this table's rows. Counting every arriving VERDICT
   *  instead reads as a bug: one session emits a verdict per check that ran
   *  (~19 for LoanPro), of which the satisfied-rollup below shows one and the
   *  time filter may drop the rest, so a real run flashed "+56" next to two new
   *  lines. Same set the highlight is drawn from, so the number and the
   *  highlighted rows always agree. */
  const freshVisible = useMemo(
    () => (fresh.size === 0 ? 0 : displayed.filter((r) => fresh.has(rowKey(r))).length),
    [displayed, fresh],
  );

  // A finding on screen without a summary is asked for NOW rather than waited
  // for: the background explainer reaches it within seconds, but a reader
  // looking at a brand-new finding would otherwise watch the check's raw
  // wording until the next poll. Bounded and de-duplicated inside `ensure`.
  useEffect(() => { ensureHeadlines(displayed); }, [displayed, ensureHeadlines]);

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
          {/* Live follow. On by default because this is a feed of an ongoing
              process — eval-engine evaluates a session ~10-20s after it ends,
              so a static page is stale seconds after it loads and says nothing
              about it. Off is a stored preference, for reading a fixed set of
              rows without the table moving underneath you. It polls only while
              this page is actually on screen (see useLivePoll). */}
          <label className={`pf-live${polling ? " on" : ""}`}
                 title={live
                   ? "New findings appear on their own, within a few seconds, and are highlighted briefly as they arrive. Polls only while this page is open and its browser tab is in the foreground."
                   : "Live follow is off — the table changes only when you press Refresh."}>
            <input type="checkbox" checked={live}
                   onChange={(e) => { setLive(e.target.checked); storeLive(e.target.checked); }} />
            <span className="pf-live-dot" aria-hidden="true" />
            Live
            {freshVisible > 0 && <span className="pf-live-new">+{freshVisible}</span>}
          </label>
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
        {/* A failed POLL, not a failed page: the rows on screen are still the
            last good read, so this says the follow stalled rather than
            claiming the feed could not be loaded. */}
        {liveError && !clearError && (
          <div className="pf-dash-feed-status">
            Live follow paused — couldn’t reach eval-engine ({liveError}). Showing the last good read; it will retry.
          </div>
        )}
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
                <tr key={r.event_id || r.session_id + r.check_id + r.evidence_excerpt + i}
                    className={`clickable ${hidden ? "pf-find-off" : ""} ${fresh.has(rowKey(r)) ? "pf-find-new" : ""}`}
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
                  <td><WhatWentWrong r={r} headline={r.event_id ? headlines[r.event_id] : undefined}
                                     summarising={!!r.event_id && pendingHeadlines.has(r.event_id)} /></td>
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

export type { TracesSection } from "../routes";

export default function DecisionTraces({ active = true, demo, findingsEffect, findingsSeverity }: {
  active?: boolean; demo: DemoConfig; findingsEffect?: string; findingsSeverity?: string;
}) {
  const tracesLoc = useLoc();
  const onTraces = onTab(tracesLoc.segs, "traces");

  // Canonicalise a bare /traces to /traces/findings (replace — it is an app
  // correction, not a place the user navigated to, so Back skips it).
  useEffect(() => {
    if (active && tracesLoc.segs[0] === "traces" && !tracesLoc.segs[1]) navTo(findingsHref(), { replace: true });
  }, [active, tracesLoc.segs]);

  // The sub-view is DERIVED FROM THE PATH, never held in state. It was state,
  // initialised to "decisions" in App.tsx, and the two then disagreed: the
  // canonicalising redirect above wrote /traces/findings while the state still
  // said "decisions", so the documented shareable link — /traces/findings/
  // <session_id>, what every CopyLink on this page emits — opened the Decisions
  // log and its flyout never resolved. Same bug App.tsx fixed at the TAB level
  // when routing went in ("derives the active tab from the URL, not a
  // useState"); this is the sub-view level of it.
  //
  // Off-tab, the last on-tab value is retained rather than recomputed: every
  // tab body stays MOUNTED, so deriving from another page's segs would unmount
  // whichever sub-view you had open and lose its filters while you were
  // somewhere else entirely.
  const lastSection = useRef<TracesSection>(tracesSectionFromPath(currentLoc().segs));
  if (onTraces) lastSection.current = tracesSectionFromPath(tracesLoc.segs);
  const section: TracesSection = lastSection.current;
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

  // POST /api/decisions/refresh runs this demo's governed catalogue
  // server-side and persists every governed result. It is the ONLY write path
  // into this store — the component that used to POST each interactive run was
  // removed with the Runtime tab — and it had no caller anywhere in the UI,
  // which is much of why the store looked structurally empty.
  const [populating, setPopulating] = useState(false);
  const [populateError, setPopulateError] = useState("");
  const populate = useCallback(async () => {
    setPopulating(true);
    setPopulateError("");
    try {
      const res = await fetch("/api/decisions/refresh", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ demo: demo.id, only: demo.populateScenarios }),
      });
      const json = await res.json();
      // A demo with no orchestrator configured 400s naming the env var to set;
      // surface that rather than leaving an unexplained empty log behind.
      if (!res.ok) throw new Error(json?.error || `${res.status} ${res.statusText}`);
      await load();
    } catch (e: any) {
      setPopulateError(String(e?.message || e));
    } finally {
      setPopulating(false);
    }
  }, [demo.id, load]);

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

  // The Decisions view is SELF-ENABLING rather than hardcoded off. It was
  // disabled outright because /api/decisions is structurally empty for an
  // ungoverned demo (the api-server only persists a row carrying a `governed`
  // key), which made it a permanently empty panel. But that is a property of
  // the DATA, not of the tab — a deployment with a governed lane fills it —
  // so it keys off the store instead: shown when there are decisions to show,
  // disabled with a reason when there are not. Findings stays the landing view
  // either way.
  const decisionsAvailable = traces.length > 0;

  const activeFilters = picked.size + (role ? 1 : 0) + (caller ? 1 : 0) + (intent ? 1 : 0) + (policy ? 1 : 0) + (q.trim() ? 1 : 0);
  const clearAll = () => { setPicked(new Set()); setRole(""); setCaller(""); setIntent(""); setPolicy(""); setQ(""); };

  return (
    <main className="pf-tr">
      <div className="pf-oob-views" style={{ marginBottom: 14 }}>
        <button
          className={`pf-oob-view ${section === "decisions" ? "active" : ""}`}
          type="button"
          onClick={() => navTo(decisionsHref())}
        >
          Decisions{decisionsAvailable ? ` (${traces.length})` : ""}
        </button>
        <button
          className={`pf-oob-view ${section === "findings" ? "active" : ""}`}
          type="button"
          onClick={() => navTo(findingsHref())}
        >
          Findings
        </button>
      </div>
      {section === "findings" && <FindingsSection initialEffect={findingsEffect} initialSeverity={findingsSeverity} rules={severityRules} active={active} app={demo.id} project={demo.phoenixProject} appLabel={demo.label} />}
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
            {traces.length === 0 ? (
              <>
                No governed decisions recorded. This log fills only for a demo running a
                governed runtime — Prefront in the request path, deciding before the tool
                runs. For an ungoverned deployment the evidence is out-of-band instead;
                see the Findings tab.
                <div style={{ marginTop: 10 }}>
                  <button className="pf-dash-link" type="button" onClick={populate} disabled={populating}>
                    {populating ? "Running the governed catalogue…" : "Populate from the demo →"}
                  </button>
                  {populateError && <div className="pf-dash-feed-status error" style={{ marginTop: 8 }}>{populateError}</div>}
                </div>
              </>
            ) : "No decisions match these filters."}
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
