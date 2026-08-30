/*
 * Overview page data — every number on the buyer-facing Overview comes from
 * here, and every one of them is LIVE (no fixtures): eval-engine's shadow
 * evaluation (/eval/status, /eval/findings, /eval/conformance), oob-ingest's
 * session data (/oob/overview, /oob/sessions), and — only as a conditional
 * extra — the governed-runtime decision store (/api/stats). Each fetch is
 * independent: a dead service zeroes its own widgets, never the page.
 *
 * The derivations below are pure functions over the fetched rows so the
 * page component stays presentational. They name CHECK vocabulary
 * (eval-engine's check ids, e.g. "entitlement") but never a demo's domain
 * vocabulary — anything domain-specific (sensitive field names) is passed in
 * from demos.ts.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import type { DemoConfig } from "../demos";
import type { AgentStats } from "./useDecisionFeed";
import {
  getJSON, qs, parseSource,
  type ConformanceTag, type EvalStatus, type EvalVerdict, type Overview as OobOverview,
  type SessionRow, type Status as OobStatus,
} from "../components/Observability";
import { severityOf, type SeverityLevel, type SeverityRule } from "../severity";

// One day. The page is a walkthrough, not an ops console — the windowed OOB
// numbers are "recent activity"; the hero totals from /eval/status are exact.
const SINCE = 86400;
const FINDINGS_LIMIT = 500;

export type OverviewData = {
  evalStatus: EvalStatus | null;
  oobStatus: OobStatus | null;
  overview: OobOverview | null;
  findings: EvalVerdict[];
  conformance: ConformanceTag[];
  sessions: SessionRow[];
  governed: AgentStats | null;
  errors: Record<string, string>;
  loading: boolean;
  reload: () => void;
};

export function useOverviewData(demo: DemoConfig, active: boolean): OverviewData {
  const [evalStatus, setEvalStatus] = useState<EvalStatus | null>(null);
  const [oobStatus, setOobStatus] = useState<OobStatus | null>(null);
  const [overview, setOverview] = useState<OobOverview | null>(null);
  const [findings, setFindings] = useState<EvalVerdict[]>([]);
  const [conformance, setConformance] = useState<ConformanceTag[]>([]);
  const [sessions, setSessions] = useState<SessionRow[]>([]);
  const [governed, setGoverned] = useState<AgentStats | null>(null);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true);

  const load = useCallback(() => {
    setLoading(true);
    const errs: Record<string, string> = {};
    const one = <T,>(key: string, url: string, set: (v: T) => void) =>
      getJSON<T>(url).then(set).catch((e) => { errs[key] = String(e?.message || e); });
    Promise.all([
      one<EvalStatus>("eval", "/eval/status", setEvalStatus),
      one<OobStatus>("oob", "/oob/status", setOobStatus),
      one<OobOverview>("overview", `/oob/overview${qs({ since: SINCE })}`, setOverview),
      one<{ findings: EvalVerdict[] }>("findings", `/eval/findings${qs({ limit: FINDINGS_LIMIT })}`, (d) => setFindings(d.findings || [])),
      one<{ conformance_tags: ConformanceTag[] }>("conformance", `/eval/conformance${qs({ limit: 100 })}`, (d) => setConformance(d.conformance_tags || [])),
      one<{ sessions: SessionRow[] }>("sessions", `/oob/sessions${qs({ since: SINCE, limit: 200 })}`, (d) => setSessions(d.sessions || [])),
      one<AgentStats>("governed", `/api/stats${qs({ demo: demo.id })}`, setGoverned),
    ]).finally(() => { setErrors(errs); setLoading(false); });
  }, [demo.id]);

  useEffect(() => { load(); }, [load]);
  // Refetch on tab activation — a scenario run elsewhere shows up on return.
  useEffect(() => { if (active) load(); }, [active, load]);

  return { evalStatus, oobStatus, overview, findings, conformance, sessions, governed, errors, loading, reload: load };
}

/* ── pure derivations ───────────────────────────────────────────────────── */

export const EFFECTS = ["block", "approval_required", "flag"] as const;
export type Effect = typeof EFFECTS[number];

export function byEffect(findings: EvalVerdict[]): Record<Effect, number> {
  const out: Record<Effect, number> = { block: 0, approval_required: 0, flag: 0 };
  for (const f of findings) if (f.effect in out) out[f.effect as Effect] += 1;
  return out;
}

export type RuleRow = { key: string; count: number; effect: Effect; section: string };

// One row per rule (Family 1) or check (Family 2/3), most-fired first, with
// the effect that fired most often for it and its policy section when cited.
export function byRule(findings: EvalVerdict[], top = 8): RuleRow[] {
  const acc = new Map<string, { count: number; effects: Record<string, number>; section: string }>();
  for (const f of findings) {
    const key = f.rule_id || f.check_id;
    const row = acc.get(key) ?? { count: 0, effects: {}, section: "" };
    row.count += 1;
    row.effects[f.effect] = (row.effects[f.effect] ?? 0) + 1;
    if (!row.section) row.section = parseSource(f.source)?.section ?? "";
    acc.set(key, row);
  }
  return [...acc.entries()]
    .map(([key, r]) => ({
      key, count: r.count, section: r.section,
      effect: (Object.entries(r.effects).sort((a, b) => b[1] - a[1])[0]?.[0] ?? "flag") as Effect,
    }))
    .sort((a, b) => b.count - a.count)
    .slice(0, top);
}

// Keyed by eval-engine's family display name (Policy / Integrity /
// Conformance), falling back to the raw family1|2|3 for a row served before
// the label existed - this feeds a user-facing breakdown, so it groups on
// what the reader sees.
export function byFamily(findings: EvalVerdict[]): Record<string, number> {
  const out: Record<string, number> = {};
  for (const f of findings) {
    const key = f.family_label || f.family;
    out[key] = (out[key] ?? 0) + 1;
  }
  return out;
}

// Findings grouped by derived severity, using the customer's severity rules
// (from useSeverityRules). Domain-neutral: severity keys on family/effect only.
export function bySeverity(findings: EvalVerdict[], rules: SeverityRule[]): Record<SeverityLevel, number> {
  const out: Record<SeverityLevel, number> = { critical: 0, high: 0, medium: 0, low: 0 };
  for (const f of findings) out[severityOf({ family: f.family, effect: f.effect }, rules)] += 1;
  return out;
}

// Per-severity triage row for the Overview's severity panel: count, share of all
// findings, and the check that drives that band most (with its policy § when
// the finding cites one). Ordered critical → low.
export type SeverityRow = {
  level: SeverityLevel; count: number; share: number; topDriver: string; topSection: string;
};
export function severityBreakdown(findings: EvalVerdict[], rules: SeverityRule[]): SeverityRow[] {
  const total = findings.length || 1;
  const buckets = new Map<SeverityLevel, EvalVerdict[]>();
  for (const f of findings) {
    const lvl = severityOf({ family: f.family, effect: f.effect }, rules);
    (buckets.get(lvl) ?? buckets.set(lvl, []).get(lvl)!).push(f);
  }
  const order: SeverityLevel[] = ["critical", "high", "medium", "low"];
  return order.map((level) => {
    const rows = buckets.get(level) ?? [];
    const drivers = new Map<string, number>();
    for (const f of rows) { const k = f.rule_id || f.check_id; drivers.set(k, (drivers.get(k) ?? 0) + 1); }
    const top = [...drivers.entries()].sort((a, b) => b[1] - a[1])[0]?.[0] ?? "";
    const topSection = rows.find((f) => (f.rule_id || f.check_id) === top && parseSource(f.source)?.section)
      ? parseSource(rows.find((f) => (f.rule_id || f.check_id) === top)!.source)?.section ?? "" : "";
    return { level, count: rows.length, share: rows.length / total, topDriver: top, topSection };
  });
}

// Findings bucketed by calendar day for the hero sparkline — the last `days`
// day-slots ending today, counted from each finding's evaluated_at. Real data
// over whatever window the fetched findings span (nothing synthesized).
export type DayBar = { label: string; count: number; today: boolean };
export function findingsPerDay(findings: EvalVerdict[], days = 7): { bars: DayBar[]; peak: number; today: number } {
  const dayKey = (d: Date) => `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;
  const counts = new Map<string, number>();
  for (const f of findings) {
    const d = new Date(f.evaluated_at);
    if (!Number.isNaN(d.getTime())) counts.set(dayKey(d), (counts.get(dayKey(d)) ?? 0) + 1);
  }
  const bars: DayBar[] = [];
  const now = new Date();
  for (let i = days - 1; i >= 0; i--) {
    const d = new Date(now); d.setDate(now.getDate() - i); d.setHours(0, 0, 0, 0);
    bars.push({
      label: d.toLocaleDateString(undefined, { month: "short", day: "numeric" }),
      count: counts.get(dayKey(d)) ?? 0,
      today: i === 0,
    });
  }
  const peak = bars.reduce((m, b) => Math.max(m, b.count), 0);
  return { bars, peak, today: bars[bars.length - 1]?.count ?? 0 };
}

// Findings distribution across the three rule families (Policy / Integrity /
// Conformance), each with the count and how many distinct rules/checks produced
// them. Keyed on the display label, fixed order.
export type FamilyRow = { key: string; label: string; count: number; rules: number };
export function familySpread(findings: EvalVerdict[]): FamilyRow[] {
  const spec: [string, string][] = [["family1", "Policy"], ["family2", "Integrity"], ["family3", "Conformance"]];
  return spec.map(([key, label]) => {
    const rows = findings.filter((f) => f.family === key);
    const rules = new Set(rows.map((f) => f.rule_id || f.check_id)).size;
    return { key, label, count: rows.length, rules };
  });
}

// Share of findings the top-N checks account for — concentration, not a total.
export function topRulesShare(rows: RuleRow[], allFindings: number, n = 3): number {
  if (!allFindings) return 0;
  const top = rows.slice(0, n).reduce((s, r) => s + r.count, 0);
  return Math.round((top / allFindings) * 100);
}

// The CISO split. "entitlement" is the ONE check that asks a pure who-may-
// call-what question an IAM system could also answer; every other check
// (precondition, sequencing, field_restriction, entity_consistency, param_*,
// workflow_integrity, toxic_combination, …) needs business context IAM has
// no concept of. This is check-vocabulary coupling, same class as
// KNOWN_CHECKS in semantic-layer's preflight.py — keep it to this one string.
const IAM_SHAPED_CHECKS = new Set(["entitlement"]);

export function iamVsContext(findings: EvalVerdict[]): { roleOnly: number; context: number } {
  let roleOnly = 0, context = 0;
  for (const f of findings) (IAM_SHAPED_CHECKS.has(f.check_id) ? roleOnly++ : context++);
  return { roleOnly, context };
}

// Findings whose one-liner names one of the deployment's sensitive fields —
// the field list comes from demos.ts, never from this file.
export function sensitiveFindings(findings: EvalVerdict[], fields: string[]): number {
  if (!fields.length) return 0;
  const needles = fields.map((f) => f.toLowerCase());
  return findings.filter((f) => {
    const hay = `${f.detail} ${f.evidence_excerpt}`.toLowerCase();
    return needles.some((n) => hay.includes(n));
  }).length;
}

export function distinctIntents(sessions: SessionRow[]): number {
  const s = new Set<string>();
  for (const row of sessions) for (const i of row.intents || []) if (i) s.add(i);
  return s.size;
}

export function offCatalogCalls(sessions: SessionRow[]): number {
  return sessions.reduce((n, s) => n + (s.off_catalog_calls || 0), 0);
}

// Conformance tags that actually cite a policy clause — the ones worth
// showing as "rule applied, satisfied, and here is the clause". Family 2 tags
// never carry one (no policy tie, by design), so they're skipped here.
export function citedTags(tags: ConformanceTag[], top = 5): ConformanceTag[] {
  return tags.filter((t) => t.section || t.clause_text).slice(0, top);
}
