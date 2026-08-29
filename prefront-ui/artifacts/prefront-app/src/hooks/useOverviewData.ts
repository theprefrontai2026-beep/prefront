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
