/*
 * Workflows as small multiples — one strip per workflow, not one merged graph.
 *
 * The process map answers "what does this system do". It cannot answer "is
 * THIS workflow acceptable", because every workflow is drawn on top of every
 * other one: the reader has to mentally trace a path out of a shared graph
 * before they can even begin to judge it. A Sankey would make that worse
 * rather than better — it is a merging diagram, and merging is precisely the
 * problem here.
 *
 * So each workflow gets its own strip, and the strips are sorted by volume.
 * Comparison becomes scanning rather than tracing, and each strip is small
 * enough that a reviewer can hold one whole workflow in view while deciding
 * about it. That is the form a decision needs.
 *
 * Pure SVG, no new dependency: these are fixed left-to-right sequences with no
 * layout problem to solve, and reaching for a graph library would add weight
 * for nothing.
 */

import { useMemo } from "react";

export type Shape = {
  steps: string[]; closed_by: string; episodes: number; sessions: number;
  roles: { value: string; episodes: number }[];
  subject_args: string[]; before_effect: string[]; example_sessions: string[];
};

const ROLE_TONES = ["#2563eb", "#0f766e", "#b45309", "#7c3aed", "#be123c"];

function Strip({ s, max, onPick, picked }: {
  s: Shape; max: number; onPick?: () => void; picked?: boolean;
}) {
  // Thickness carries volume, so the eye ranks the workflows before reading a
  // single label. Floored, because a hairline reads as "broken" rather than
  // "rare" and a reviewer still has to be able to see it.
  const h = Math.max(6, Math.round((s.episodes / Math.max(1, max)) * 26));
  const total = s.roles.reduce((a, r) => a + r.episodes, 0) || 1;

  return (
    <div className={`pf-ws-strip${picked ? " picked" : ""}`} onClick={onPick}>
      <div className="pf-ws-head">
        <span className="pf-ws-n">{s.episodes}×</span>
        {s.closed_by && <span className="pf-ws-write">changes data</span>}
        {s.subject_args.length > 0 && <span className="pf-ws-subj">per {s.subject_args[0]}</span>}
        {/* Who ran it, as proportions rather than a list: a workflow one role
            runs almost exclusively is a different thing to approve than one
            three roles share, and that is invisible in a comma-separated set. */}
        <span className="pf-ws-roles">
          {s.roles.map((r, i) => (
            <span key={r.value} title={`${r.value}: ${r.episodes}`}
                  style={{ width: `${Math.round((r.episodes / total) * 100)}%`,
                           background: ROLE_TONES[i % ROLE_TONES.length] }} />
          ))}
        </span>
      </div>
      <div className="pf-ws-flow">
        {s.steps.map((t, i) => {
          const last = i === s.steps.length - 1;
          const isWrite = last && !!s.closed_by;
          return (
            <span key={`${t}-${i}`} className="pf-ws-node">
              <span className={`pf-ws-box${isWrite ? " write" : ""}`}>{t}</span>
              {!last && <span className="pf-ws-band" style={{ height: h }} />}
            </span>
          );
        })}
      </div>
    </div>
  );
}

export default function WorkflowStrips({ shapes, limit = 14, picked, onPick }: {
  shapes: Shape[]; limit?: number; picked?: string;
  onPick?: (s: Shape | null) => void;
}) {
  const rows = useMemo(
    () => [...shapes].sort((a, b) => b.episodes - a.episodes).slice(0, limit),
    [shapes, limit]);
  const max = rows[0]?.episodes || 1;
  const hidden = shapes.length - rows.length;

  if (!rows.length) return <div className="pf-dash-feed-status">No workflows observed yet.</div>;

  return (
    <>
      <div className="pf-ws">
        {rows.map((s) => {
          const key = s.steps.join(">") + "|" + s.closed_by;
          return (
            <Strip key={key} s={s} max={max} picked={picked === key}
                   onPick={() => onPick?.(picked === key ? null : s)} />
          );
        })}
      </div>
      {hidden > 0 && (
        // Never a silent truncation: the reader is told the list has a tail,
        // because a page that shows fourteen of forty-nine workflows without
        // saying so reads as a complete inventory.
        <div className="pf-hint" style={{ marginTop: 8 }}>
          {hidden} rarer workflow{hidden === 1 ? "" : "s"} not shown — raise the minimum below to see fewer, or widen the window to see more.
        </div>
      )}
    </>
  );
}
