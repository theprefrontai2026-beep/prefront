/*
 * Intents, one row per GOAL — every observed way of reaching the same call,
 * together.
 *
 * Rows used to be one per workflow shape, and five ways of reaching the risk
 * profile read as five "Assess applicant risk" rows. Publishing names an intent
 * by its terminal call, so it kept one of them and dropped the rest: the page
 * and the catalogue disagreed about what a unit was. Grouped by the goal — a
 * counted fact, never the model's title — they agree.
 *
 * A row leads with what the intent is FOR, and its volume as a bar split by
 * the ways it is reached. Expanded: what is read before the goal, as meters
 * over every run that reached it, and each way it was reached.
 */

import { useMemo, useState } from "react";

/** What a reviewer decided about one intent.
 *
 *  `roles` is always EMPTY from this page: callers are not chosen here, so the
 *  review stays on the workflow rather than on identity. It is never
 *  pre-filled from the observed set — observed callers are not permitted
 *  callers — and an empty list publishes an entry Family 3 does not
 *  role-restrict. Kept in the shape so saved runs and the publish payload are
 *  unchanged. */
export type Approval = { roles: string[] };

/** The model's reading of one intent. `title`/`description` are optional: a
 *  run saved before they were kept has neither, and falls back to names
 *  derived from the goal. */
export type Policy = {
  statement: string; rationale: string; confidence: string; caveats: string[];
  title?: string; description?: string;
};

export type Shape = {
  steps: string[]; closed_by: string; episodes: number; sessions: number;
  roles: { value: string; episodes: number }[];
  subject_args: string[]; before_effect: string[]; example_sessions: string[];
};

/** Per goal: how many runs reached it, and how many of those read each other
 *  call first. Mirrors semanticlayer.intent_mining.goal_support. Must be given
 *  EVERY shape, hidden ones included — a goal called on its own is still a way
 *  of reaching it. */
export type GoalSupport = Record<string, { runs: number; before: Record<string, number> }>;

export function goalSupport(shapes: Shape[]): GoalSupport {
  const out: GoalSupport = {};
  const goals = new Set(shapes.filter((s) => s.steps.length).map((s) => s.steps[s.steps.length - 1]));
  for (const goal of goals) {
    let runs = 0;
    const before: Record<string, number> = {};
    for (const s of shapes) {
      const i = s.steps.indexOf(goal);
      if (i < 0) continue;
      runs += s.episodes;
      for (const t of new Set(s.steps.slice(0, i))) {
        if (t !== goal) before[t] = (before[t] || 0) + s.episodes;
      }
    }
    out[goal] = { runs, before };
  }
  return out;
}

/** One goal and the shapes that end on it. Mirrors
 *  semanticlayer.intent_mining.goals_from_workflows: kept if ANY variant has
 *  at least `minSteps` calls, and then its direct-call variant stays in. */
export type Goal = { key: string; goal: string; variants: Shape[]; runs: number; writes: boolean };

export const goalKey = (goal: string) => `goal:${goal}`;

export function groupByGoal(shapes: Shape[], minSteps: number): Goal[] {
  const by = new Map<string, Shape[]>();
  for (const s of shapes) {
    if (!s.steps.length) continue;
    const g = s.steps[s.steps.length - 1];
    by.set(g, [...(by.get(g) || []), s]);
  }
  const out: Goal[] = [];
  for (const [goal, vs] of by) {
    if (!vs.some((v) => v.steps.length >= minSteps)) continue;
    const variants = [...vs].sort((a, b) => b.episodes - a.episodes);
    out.push({
      key: goalKey(goal), goal, variants,
      runs: variants.reduce((a, v) => a + v.episodes, 0),
      writes: variants.some((v) => !!v.closed_by),
    });
  }
  return out.sort((a, b) => b.runs - a.runs);
}

const CONF_TONE: Record<string, string> = { high: "green", medium: "amber", low: "slate" };
const humanize = (t: string) => t.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());

/** Volume, split by the ways the goal is reached — one bar says both how much
 *  and whether one way dominates. */
function SplitBar({ g, max }: { g: Goal; max: number }) {
  return (
    <span className="pf-ws-vol" title={`${g.runs} runs, ${g.variants.length} way${g.variants.length === 1 ? "" : "s"} of reaching it`}>
      <span className="bar">
        <span className="fill" style={{ width: `${Math.max(4, (g.runs / Math.max(1, max)) * 100)}%` }}>
          {g.variants.map((v, i) => (
            <i key={i} style={{ width: `${(v.episodes / g.runs) * 100}%`, opacity: i % 2 ? 0.55 : 1 }} />
          ))}
        </span>
      </span>
      <span className="n">{g.runs}</span>
    </span>
  );
}

function GoalRow({ g, max, sup, approval, policy, onApprove }: {
  g: Goal; max: number;
  sup?: { runs: number; before: Record<string, number> };
  approval?: Approval;
  policy?: Policy;
  onApprove: (a: Approval | null) => void;
}) {
  const [open, setOpen] = useState(false);
  const approved = !!approval;
  const n = g.variants.length;
  const title = policy?.title || humanize(g.goal);
  const desc = policy?.description || `${n} way${n === 1 ? "" : "s"} of reaching ${humanize(g.goal).toLowerCase()}`;
  // What is read before the goal, strongest first. Under 5% is noise at a
  // glance; the variant list below still shows every step.
  const reads = sup?.runs
    ? Object.entries(sup.before)
        .map(([t, k]) => ({ t, pct: Math.round((k / sup.runs) * 100) }))
        .filter((r) => r.pct >= 5)
        .sort((a, b) => b.pct - a.pct)
        .slice(0, 6)
    : [];

  return (
    <div className={`pf-ws-strip${approved ? " approved" : ""}${open ? " open" : ""}`}>
      <div className="pf-ws-head" onClick={() => setOpen((v) => !v)}>
        <span className="pf-ws-caret">{open ? "▾" : "▸"}</span>
        <div className="pf-ws-name">
          <div className="pf-ws-title">
            {title}
            {policy && (
              <span className={`pf-dash-chip ${CONF_TONE[policy.confidence] || "slate"}`}
                    title="Read by a model from observed behaviour — not a policy document">
                {policy.confidence}
              </span>
            )}
            {n > 1 && <span className="pf-dash-chip slate">{n} ways</span>}
            {g.writes && <span className="pf-ws-write">changes data</span>}
            {approved && <span className="pf-dash-chip green">approved</span>}
          </div>
          <div className="pf-ws-desc">{desc}</div>
        </div>
        <div className="pf-ws-stats"><SplitBar g={g} max={max} /></div>
      </div>

      {open && (
        <div className="pf-ws-detail">
          {policy?.statement && <div className="pf-ws-stmt">{policy.statement}</div>}

          <div className="pf-ws-sub">Read before it</div>
          <div className="pf-ws-steps">
            {reads.length ? reads.map((r) => (
              <span key={r.t} className="pf-ws-stepwrap">
                <span className="pf-ws-step">
                  <code>{r.t}</code>
                  <span className="pf-ws-meter" title={`read before ${g.goal} in ${r.pct}% of all ${sup!.runs} runs that reached it`}>
                    <span className="bar"><i style={{ width: `${r.pct}%` }} /></span>
                    <span className="pct">{r.pct}%</span>
                  </span>
                </span>
              </span>
            )) : <span className="pf-ws-muted">nothing, in any run</span>}
            <span className="pf-ws-arrow">→</span>
            <span className={`pf-ws-step goal${g.writes ? " write" : ""}`}>
              <code>{g.goal}</code>
              <span className="pf-ws-goal-tag">goal</span>
            </span>
          </div>
          {sup && reads.length > 0 && (
            <div className="pf-ws-legend">
              bar = share of all {sup.runs} runs reaching <code>{g.goal}</code> that read it first,
              including runs that went on to something else
            </div>
          )}

          <div className="pf-ws-sub">{n} way{n === 1 ? "" : "s"} it was reached</div>
          <div className="pf-ws-variants">
            {g.variants.map((v) => (
              <div key={shapeKey(v)} className="pf-ws-var">
                <span className="bar"><i style={{ width: `${(v.episodes / g.runs) * 100}%` }} /></span>
                <span className="n">{v.episodes}</span>
                <span className="chain">
                  {v.steps.length > 1 ? `${v.steps.slice(0, -1).join(" → ")} → goal` : <em>directly, nothing read first</em>}
                </span>
              </div>
            ))}
          </div>

          {policy && (policy.rationale || policy.caveats?.length > 0) && (
            <details className="pf-ws-why">
              <summary>Why the model reads it this way</summary>
              {policy.rationale && <p>{policy.rationale}</p>}
              {policy.caveats?.map((c, i) => <p key={i} className="caveat">{c}</p>)}
            </details>
          )}

          <div className="pf-ws-approve-row">
            <button className={`pf-btn sm${approved ? " reject" : " primary"}`} type="button"
                    onClick={() => onApprove(approved ? null : { roles: [] })}>
              {approved ? "Remove from the set" : "Add to the approved set"}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

/** How many intents the list renders. Exported so the caller can ask the
 *  summariser for exactly this many — a lower server cap leaves visible rows
 *  that can never receive a reading, with no symptom but a reader wondering
 *  why some have one and others do not. */
export const STRIP_LIMIT = 14;

export const shapeKey = (s: Shape) => s.steps.join(">") + "|" + s.closed_by;

export default function WorkflowStrips({ shapes, minSteps = 1, limit = STRIP_LIMIT, approvals, policies, onApprove }: {
  /** EVERY observed shape: the meters count all of them, and grouping hides
   *  goals by `minSteps` itself. */
  shapes: Shape[]; minSteps?: number; limit?: number;
  approvals: Record<string, Approval>;
  /** Keyed by goal — absent while learning, by design. */
  policies?: Record<string, Policy>;
  /** `rep` stands for the goal in the publish payload: its terminal step names
   *  the intent, and `closed_by` is set if ANY way of reaching it writes. */
  onApprove: (key: string, rep: Shape, a: Approval | null) => void;
}) {
  const support = useMemo(() => goalSupport(shapes), [shapes]);
  const all = useMemo(() => groupByGoal(shapes, minSteps), [shapes, minSteps]);
  const rows = all.slice(0, limit);
  const max = rows[0]?.runs || 1;
  const hidden = all.length - rows.length;

  if (!rows.length) return <div className="pf-dash-feed-status">No workflows observed yet.</div>;

  return (
    <>
      <div className="pf-ws-key">
        <span className="pf-ws-vol"><span className="bar"><span className="fill" style={{ width: "100%" }}>
          <i style={{ width: "65%" }} /><i style={{ width: "35%", opacity: 0.55 }} />
        </span></span></span>
        how often it runs, split by the different ways it is reached
      </div>
      <div className="pf-ws">
        {rows.map((g) => (
          <GoalRow key={g.key} g={g} max={max} sup={support[g.goal]} approval={approvals[g.key]}
                   policy={policies?.[g.goal]}
                   onApprove={(a) => onApprove(g.key, { ...g.variants[0], closed_by: g.writes ? g.goal : "" }, a)} />
        ))}
      </div>
      {hidden > 0 && (
        // Never a silent truncation: the reader is told the list has a tail.
        <div className="pf-hint" style={{ marginTop: 8 }}>
          {hidden} rarer intent{hidden === 1 ? "" : "s"} not shown — raise the minimum below to see fewer, or widen the window to see more.
        </div>
      )}
    </>
  );
}
