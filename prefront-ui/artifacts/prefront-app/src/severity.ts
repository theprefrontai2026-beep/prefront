/*
 * Finding severity — a DERIVED, customer-tunable rating.
 *
 * Severity is a pure function of two fields already on every /eval/findings row
 * (`family` + `effect`), resolved against an ordered rule-list the customer
 * edits in Settings (persisted server-side, GET /api/settings/severity). Nothing
 * is stored on the finding and eval-engine is never involved — this is display
 * layer only. First match wins; the seeded list always ends in a catch-all, so
 * a resolution failure falls back to "low".
 */

export type SeverityLevel = "critical" | "high" | "medium" | "low";

export interface SeverityRule {
  family: string | null;   // family1 | family2 | family3 | null(any)
  effect: string | null;   // block | approval_required | flag | allow | null(any)
  severity: SeverityLevel;
}

/** Mirror of the api-server seed — a client-side fallback if the fetch fails so
 *  the Findings table never renders without a rating. The server is the source
 *  of truth; this only covers a dead /api/settings/severity. */
export const DEFAULT_SEVERITY_RULES: SeverityRule[] = [
  { family: null, effect: "block", severity: "critical" },
  { family: null, effect: "approval_required", severity: "high" },
  { family: "family2", effect: null, severity: "medium" },
  { family: null, effect: "flag", severity: "low" },
  { family: null, effect: null, severity: "low" },
];

export const SEVERITY_ORDER: SeverityLevel[] = ["critical", "high", "medium", "low"];

/** rank: higher = more severe (for sorting). tone: the `pf-dash-chip` modifier. */
export const SEVERITY_META: Record<SeverityLevel, { label: string; tone: string; rank: number }> = {
  critical: { label: "Critical", tone: "crit", rank: 3 },
  high:     { label: "High",     tone: "amber", rank: 2 },
  medium:   { label: "Medium",   tone: "gold", rank: 1 },
  low:      { label: "Low",      tone: "slate", rank: 0 },
};

/** First-match-wins over the ordered rules. `null` on a rule field means "any". */
export function severityOf(f: { family: string; effect: string }, rules: SeverityRule[]): SeverityLevel {
  for (const r of rules) {
    if (r.family != null && r.family !== f.family) continue;
    if (r.effect != null && r.effect !== f.effect) continue;
    return r.severity;
  }
  return "low";
}

// UI vocabulary for the Settings editor dropdowns.
export const FAMILY_OPTIONS: { value: string; label: string }[] = [
  { value: "", label: "Any family" },
  { value: "family1", label: "Policy (F1)" },
  { value: "family2", label: "Integrity (F2)" },
  { value: "family3", label: "Conformance (F3)" },
];
export const EFFECT_OPTIONS: { value: string; label: string }[] = [
  { value: "", label: "Any effect" },
  { value: "block", label: "block" },
  { value: "approval_required", label: "approval_required" },
  { value: "flag", label: "flag" },
  { value: "allow", label: "allow" },
];
