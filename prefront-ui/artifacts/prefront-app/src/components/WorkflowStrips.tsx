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

import { useMemo, useState } from "react";
import PatternGraph from "./PatternGraph";

/** What a reviewer decided about one pattern.
 *
 *  `roles` starts EMPTY rather than pre-filled from the observed set, and that
 *  is the whole safety property of this screen: the observed callers are not
 *  the permitted callers, and pre-ticking them would turn approval into a
 *  rubber stamp on whatever happened to occur. A reviewer names who may do
 *  this; the observed set is offered beside the choice, as evidence. */
export type Approval = { roles: string[] };

export type Policy = { statement: string; rationale: string; confidence: string; caveats: string[] };

const CONF_TONE: Record<string, string> = { high: "green", medium: "amber", low: "slate" };

/** The model's reading of this workflow.
 *
 *  Boxed away from the counted facts and labelled with its confidence, because
 *  the distinction is the whole epistemics of the page: everything else in the
 *  row was counted and is reproducible, this was inferred and is advisory. It
 *  appears here, on the workflow it is about, rather than in a section of its
 *  own — a summary a reviewer has to go and find somewhere else is a summary
 *  of nothing in particular. */
function PolicyBlock({ p }: { p: Policy }) {
  return (
    <div className={`pf-li-policy ${CONF_TONE[p.confidence] || "slate"}`}>
      <div className="pf-li-policy-head">
        What this looks like
        <span className="pf-li-conf">{p.confidence} confidence</span>
        <span className="pf-li-src">read by a model from observed behaviour — not a policy document</span>
      </div>
      <div className="pf-li-stmt">{p.statement}</div>
      {p.rationale && <div className="pf-li-why"><span className="lbl">because</span>{p.rationale}</div>}
      {p.caveats?.map((c, i) => <div key={i} className="pf-li-caveat">{c}</div>)}
    </div>
  );
}

/** What saying yes would actually permit — recomputed as roles are ticked.
 *
 *  Counted, never phrased by a model: this is the sentence the decision rests
 *  on. Written as a GRANT rather than a description of traffic, because that is
 *  what approval means — the observed callers become the permitted ones the
 *  moment someone publishes, and saying so is what gives a reviewer the chance
 *  to notice a caller they did not intend to bless. */
function ApprovalSummary({ steps, roles, writes, sessions }: {
  steps: string[]; roles: string[]; writes: boolean; sessions: number;
}) {
  return (
    <div className="pf-li-approve">
      <div className="pf-li-approve-h">If approved, this would permit</div>
      <ul className="pf-li-approve-l">
        <li>
          {roles.length
            ? <strong>{roles.join(", ")}</strong>
            : <em>nobody explicitly</em>}{" "}
          to run{" "}
          {steps.map((t, i) => (
            <span key={i}><code>{t}</code>{i < steps.length - 1 ? " → " : ""}</span>
          ))}
        </li>
        {writes && <li>…which <strong>changes data</strong></li>}
        <li className="pf-li-approve-w">
          on the evidence of <strong>{sessions}</strong> observed session{sessions === 1 ? "" : "s"} —
          approval turns those observed callers into <em>permitted</em> ones
        </li>
      </ul>
    </div>
  );
}

export type Shape = {
  steps: string[]; closed_by: string; episodes: number; sessions: number;
  roles: { value: string; episodes: number }[];
  subject_args: string[]; before_effect: string[]; example_sessions: string[];
};

const ROLE_TONES = ["#2563eb", "#0f766e", "#b45309", "#7c3aed", "#be123c"];

function Strip({ s, max, approval, policy, onApprove }: {
  s: Shape; max: number;
  approval?: Approval;
  policy?: Policy;
  onApprove: (a: Approval | null) => void;
}) {
  const [open, setOpen] = useState(false);
  const approved = !!approval;
  // Thickness carries volume, so the eye ranks the workflows before reading a
  // single label. Floored, because a hairline reads as "broken" rather than
  // "rare" and a reviewer still has to be able to see it.
  const h = Math.max(6, Math.round((s.episodes / Math.max(1, max)) * 26));
  const total = s.roles.reduce((a, r) => a + r.episodes, 0) || 1;

  const toggleRole = (r: string) => {
    const cur = approval?.roles || [];
    onApprove({ roles: cur.includes(r) ? cur.filter((x) => x !== r) : [...cur, r] });
  };

  return (
    <div className={`pf-ws-strip${approved ? " approved" : ""}${open ? " open" : ""}`}>
      <div className="pf-ws-head" onClick={() => setOpen((v) => !v)}>
        <span className="pf-ws-caret">{open ? "▾" : "▸"}</span>
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
      <div className="pf-ws-flow" onClick={() => setOpen((v) => !v)}>
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

      {open && (
        <div className="pf-ws-detail">
          {/* Its own graph, in its own row. A reviewer deciding about this
              pattern should not have to find it inside a diagram of every
              other one. */}
          <PatternGraph steps={s.steps} closedBy={s.closed_by} episodes={s.episodes}
                        subject={s.subject_args[0]} roles={s.roles} />

          {policy && <PolicyBlock p={policy} />}

          <div className="pf-ws-approve">
            <div className="pf-ws-approve-h">
              Who may do this? <span>Observed callers are shown as evidence — tick the ones you intend to permit.</span>
            </div>
            <div className="pf-ws-roles-pick">
              {s.roles.length === 0 && <span className="muted">no role was recorded on these calls</span>}
              {s.roles.map((r) => (
                <label key={r.value} className={approval?.roles.includes(r.value) ? "on" : ""}>
                  <input type="checkbox" checked={approval?.roles.includes(r.value) || false}
                         onChange={() => toggleRole(r.value)} />
                  {r.value}<span className="pf-ws-obs">observed {r.episodes}×</span>
                </label>
              ))}
            </div>
            <ApprovalSummary steps={s.steps} roles={approval?.roles || []}
                             writes={!!s.closed_by} sessions={s.sessions} />

            <div className="pf-ws-approve-row">
              <button className={`pf-btn sm${approved ? " reject" : " primary"}`} type="button"
                      onClick={() => onApprove(approved ? null : { roles: [] })}>
                {approved ? "Remove from the set" : "Add to the approved set"}
              </button>
              {approved && approval!.roles.length === 0 && (
                // Loud, because an empty role list is the widest possible grant
                // wearing the narrowest look.
                <span className="pf-ws-warn">no caller ticked — this would publish an entry
                  permitting nobody explicitly, which is not the same as denying everybody</span>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

/** How many workflows the list renders. Exported so the caller can ask the
 *  summariser for exactly this many — a lower server cap leaves visible rows
 *  that can never receive a reading, with no symptom but a reader wondering
 *  why some have one and others do not. */
export const STRIP_LIMIT = 14;

export const shapeKey = (s: Shape) => s.steps.join(">") + "|" + s.closed_by;

export default function WorkflowStrips({ shapes, limit = STRIP_LIMIT, approvals, policies, onApprove }: {
  shapes: Shape[]; limit?: number;
  approvals: Record<string, Approval>;
  /** Keyed by the workflow's step chain — absent while learning, by design. */
  policies?: Record<string, Policy>;
  onApprove: (s: Shape, a: Approval | null) => void;
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
          const key = shapeKey(s);
          return (
            <Strip key={key} s={s} max={max} approval={approvals[key]}
                   policy={policies?.[s.steps.join(">")]}
                   onApprove={(a) => onApprove(s, a)} />
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
