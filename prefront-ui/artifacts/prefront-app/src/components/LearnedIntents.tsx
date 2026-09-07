/*
 * Learned intents — what the traces imply, for a deployment with no policy doc.
 *
 * The review surface for intent_learning_design.md's mining path (L3). It sits
 * in its OWN tab rather than inside Policy Studio, and that is structural
 * rather than cosmetic: Policy Studio's sub-views live under a selected
 * document (`/policy/<document_id>?tab=`), and this whole path exists for
 * deployments that have no document to select. Nesting it there would have
 * required picking a policy document in order to reach the feature for people
 * who have none.
 *
 * The page's job is to make a reviewer suspicious in the right places. Mining
 * learns what an agent DID, never what it should have done, so the design is
 * built around showing evidence rather than conclusions: every observed value
 * carries its support, contested candidates are marked before anything else is
 * read, and the LLM's inferred policy is visibly separated from the counted
 * facts. A reviewer approves a NARROWING, not a rubber stamp.
 */

import { useCallback, useState } from "react";
import type { DemoConfig } from "../demos";

type Counted = { value: string; sessions: number; calls: number; share?: number };
type Contested = { check_id: string; sessions: number; findings: number };
type Policy = { statement: string; rationale: string; confidence: string; caveats: string[] };

type Candidate = {
  tool_name: string; intent: string; description: string;
  params: string[]; fields: string[]; side_effect: string;
  observed_roles: Counted[]; observed_channels: Counted[];
  expected_rows_p99: number | null; mandatory_filters: string[]; closing_obligation: string;
  support_sessions: number; support_calls: number; example_sessions: string[];
  contested: Contested[]; warnings: string[];
  inferred_policy: Policy | null; review_status: string;
};

type Variant = { steps: string[]; sessions: number; coverage: number; occurrences: number };
type Grouped = {
  core_steps: string[]; optional_steps: string[]; variants: Variant[];
  sessions: number; observed_roles: Counted[]; contested: Contested[];
  example_sessions: string[]; intent: string; description: string;
  inferred_policy: Policy | null; review_status: string; warnings: string[];
};

type Unused = { tool: string; used_by: string[]; others_use_rate: number;
                silence_by_chance: number; likely_boundary: boolean; this_cohort_sessions: number };
type CohortRow = {
  role: string; sessions: number; calls: number;
  tools: { tool: string; sessions: number; calls: number }[];
  exclusive_tools: string[]; never_used: Unused[];
  field_gaps: { tool: string; withheld: string[]; seen_by: string[] }[];
  has_exposure: boolean; inferred_policy: Policy | null;
  review_status: string; warnings: string[];
};

/** An access boundary, inferred from what this cohort does that others do not.
 *
 *  Absence is the weakest evidence and the easiest to over-read, so it is split
 *  here exactly as it is scored: operations whose silence is unlikely by chance
 *  are candidate boundaries; the rest are shown greyed and explicitly labelled
 *  inconclusive, because a long list of rarely-used tools is not evidence and
 *  its LENGTH is the thing most likely to be mistaken for some. */
function CohortCard({ c }: { c: CohortRow }) {
  const strong = c.never_used.filter((u) => u.likely_boundary);
  const weak = c.never_used.filter((u) => !u.likely_boundary);
  return (
    <div className="pf-li-card">
      <div className="pf-li-head">
        <code className="pf-li-name">{c.role}</code>
        <span className="pf-li-support">{c.sessions} sessions · {c.tools.length} operations</span>
        <span className="pf-li-spacer" />
        {!c.has_exposure && <span className="pf-dash-chip gold">too little traffic to conclude</span>}
        <span className="pf-dash-chip slate">{c.review_status}</span>
      </div>

      {c.inferred_policy && <PolicyBlock p={c.inferred_policy} />}

      {c.field_gaps.length > 0 && (
        <div className="pf-li-grid">
          <div><span className="lbl">fields withheld from this cohort</span>
            {c.field_gaps.map((g) => (
              <div key={g.tool} className="pf-li-warn-row">
                <code>{g.tool}</code>: {g.withheld.join(", ")} — seen by {g.seen_by.join(", ")}
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="pf-li-grid">
        {c.exclusive_tools.length > 0 && (
          <div><span className="lbl">only this cohort does</span>
            {c.exclusive_tools.map((t) => <span key={t} className="pf-li-chip">{t}</span>)}
          </div>
        )}
        {strong.length > 0 && (
          <div><span className="lbl">never done — unlikely by chance</span>
            {strong.map((u) => (
              <span key={u.tool} className="pf-li-chip rare"
                    title={`others reach it in ${Math.round(u.others_use_rate * 100)}% of their sessions; silence by chance p=${u.silence_by_chance}`}>
                {u.tool}
              </span>
            ))}
          </div>
        )}
        {weak.length > 0 && (
          <div><span className="lbl">never done — inconclusive, too rare to tell</span>
            {weak.map((u) => <span key={u.tool} className="pf-li-chip opt-dim">{u.tool}</span>)}
          </div>
        )}
      </div>

      {c.warnings.length > 0 && (
        <details className="pf-li-warn">
          <summary>{c.warnings.length} thing{c.warnings.length === 1 ? "" : "s"} to check before approving</summary>
          {c.warnings.map((w, i) => <div key={i} className="pf-li-warn-row">{w}</div>)}
        </details>
      )}
    </div>
  );
}

type OpPath = { before: string[]; episodes: number };
type Operation = {
  operation: string; total_episodes: number; bare_episodes: number;
  paths: OpPath[]; observed_roles: Counted[]; subject_args: string[];
  example_sessions: string[]; intent: string; description: string;
  inferred_policy: Policy | null; review_status: string; warnings: string[];
};

/** One side-effecting operation and every observed way of reaching it.
 *
 *  This is the sharpest evidence on the page, because it is a COMPARISON: the
 *  times evidence was gathered before the act, beside the times it was not.
 *  A rate over co-occurring tools cannot express that. "196 of 241 with
 *  nothing first" is either a missing control or a bypassed one, and which is
 *  a question a reviewer can answer where a miner cannot. */
function OperationCard({ o }: { o: Operation }) {
  const bare = o.total_episodes ? o.bare_episodes / o.total_episodes : 0;
  return (
    <div className={`pf-li-card${bare > 0.5 ? " contested" : ""}`}>
      <div className="pf-li-head">
        <code className="pf-li-name">{o.intent || o.operation}</code>
        <span className="pf-li-tool">closes on {o.operation}</span>
        <span className="pf-li-support">{o.total_episodes} times</span>
        <span className="pf-li-spacer" />
        {o.bare_episodes > 0 && (
          <span className={`pf-dash-chip ${bare > 0.5 ? "red" : "gold"}`}
                title="performed with no preceding calls at all">
            {Math.round(bare * 100)}% with nothing first
          </span>
        )}
        <span className="pf-dash-chip slate">{o.review_status}</span>
      </div>

      {o.description && <div className="pf-li-desc">{o.description}</div>}
      {o.inferred_policy && <PolicyBlock p={o.inferred_policy} />}

      <div className="pf-li-corelbl">observed paths to it</div>
      {o.paths.map((pa, i) => {
        const share = o.total_episodes ? Math.round(pa.episodes / o.total_episodes * 100) : 0;
        return (
          <div key={i} className="pf-li-path">
            <span className="pf-li-vmeta">{pa.episodes}× · {share}%</span>
            {pa.before.length
              ? <Steps steps={[...pa.before, o.operation]} />
              : <div className="pf-li-flow"><span className="pf-li-bare">nothing preceded it →</span>
                  <span className="pf-li-step"><code>{o.operation}</code></span></div>}
          </div>
        );
      })}

      <div className="pf-li-grid">
        <div><span className="lbl">performed by</span><Chips items={o.observed_roles} /></div>
        {o.subject_args.length > 0 && (
          <div><span className="lbl">subject identified by</span>
            {o.subject_args.map((a) => <span key={a} className="pf-li-chip">{a}</span>)}
          </div>
        )}
      </div>

      {o.warnings.length > 0 && (
        <details className="pf-li-warn" open={bare > 0.5}>
          <summary>{o.warnings.length} thing{o.warnings.length === 1 ? "" : "s"} to check before approving</summary>
          {o.warnings.map((w, i) => <div key={i} className="pf-li-warn-row">{w}</div>)}
        </details>
      )}
    </div>
  );
}

type Baseline = {
  observed_episodes: number; distinct_shapes: number; ready: boolean; status: string;
  recent_coverage: number; recent_novelty: number; recommendation: string;
  buckets: { label: string; episodes: number; new_shapes: number; explained_by_prior: number }[];
};

/** Where the deployment is in the learning phase.
 *
 *  Leads the page because it determines what everything below MEANS. While a
 *  baseline is still forming, the patterns are an observation of how tools are
 *  being called; they are not findings, and nothing here is wrong. Presenting
 *  them as issues before there is anything to compare against manufactures
 *  problems out of the absence of a baseline. */
function BaselineBanner({ b }: { b: Baseline }) {
  const pct = (n: number) => `${Math.round(n * 100)}%`;
  return (
    <div className={`pf-lb ${b.ready ? "ready" : ""}`}>
      <div className="pf-lb-head">
        <span className={`pf-dash-chip ${b.ready ? "green" : "slate"}`}>
          {b.ready ? "baseline settled" : "still learning"}
        </span>
        <strong>{b.observed_episodes} operations observed · {b.distinct_shapes} distinct patterns</strong>
      </div>
      <p className="pf-hint" style={{ margin: "6px 0 0" }}>{b.recommendation}</p>
      {/* Prior-coverage, not whole-window coverage: the patterns learned up to
          each period are scored on traffic they had never seen. Measuring over
          the whole window would be circular — the patterns came from it. */}
      <div className="pf-lb-buckets">
        {b.buckets.map((x) => (
          <div key={x.label} className="pf-lb-bucket"
               title={`${x.episodes} operations · ${x.new_shapes} patterns seen for the first time · ${pct(x.explained_by_prior)} already known`}>
            <div className="pf-lb-bar"><div className="pf-lb-fill" style={{ height: `${Math.round(x.explained_by_prior * 100)}%` }} /></div>
            <span className="pf-lb-lbl">{x.new_shapes > 0 ? `+${x.new_shapes}` : "—"}</span>
          </div>
        ))}
      </div>
      <div className="pf-lb-legend">
        bar = share of each period already explained by patterns learned before it;
        <span className="pf-lb-new"> +n</span> = patterns seen for the first time
      </div>
    </div>
  );
}

const CONF_TONE: Record<string, string> = { high: "green", medium: "amber", low: "slate" };

function Steps({ steps, tone = "" }: { steps: string[]; tone?: string }) {
  return (
    <div className="pf-li-flow">
      {steps.map((t, i) => (
        <span key={`${t}-${i}`} className="pf-li-step">
          <code className={tone}>{t}</code>
          {i < steps.length - 1 && <span className="pf-li-arrow">→</span>}
        </span>
      ))}
    </div>
  );
}

/** One intent, summarised from several observed shapes.
 *
 *  A business intent rarely has one shape — the agent sometimes already held
 *  part of the data, sometimes went further — so the same operation shows up
 *  as several runs. Rendering each separately gave a reviewer the same policy
 *  three times without ever saying they were one thing.
 *
 *  The CORE / OPTIONAL split is counted, not the model's reading, and is drawn
 *  that way: core steps are the backbone a reviewer would turn into a
 *  precondition, optional ones are extensions that must never be presented as
 *  requirements. */
function GroupCard({ g }: { g: Grouped }) {
  const [open, setOpen] = useState(false);
  return (
    <div className={`pf-li-card${g.contested.length ? " contested" : ""}`}>
      <div className="pf-li-head">
        <code className="pf-li-name">{g.intent || g.core_steps.join(" → ")}</code>
        <span className="pf-li-support">{g.sessions} sessions · {g.variants.length} observed shape{g.variants.length === 1 ? "" : "s"}</span>
        <span className="pf-li-spacer" />
        {g.contested.length > 0 && (
          <span className="pf-dash-chip red">contested by {g.contested.length} integrity check{g.contested.length === 1 ? "" : "s"}</span>
        )}
        <span className="pf-dash-chip slate">{g.review_status}</span>
      </div>

      <div className="pf-li-corelbl">always</div>
      <Steps steps={g.core_steps} />
      {g.optional_steps.length > 0 && (
        <>
          <div className="pf-li-corelbl">sometimes also</div>
          <div className="pf-li-flow">
            {g.optional_steps.map((t) => <span key={t} className="pf-li-step"><code className="opt">{t}</code></span>)}
          </div>
        </>
      )}

      {g.description && <div className="pf-li-desc">{g.description}</div>}
      {g.inferred_policy && <PolicyBlock p={g.inferred_policy} />}

      <div className="pf-li-grid">
        <div><span className="lbl">who ran it</span><Chips items={g.observed_roles} /></div>
      </div>

      {g.warnings.length > 0 && (
        <details className="pf-li-warn" open={g.contested.length > 0}>
          <summary>{g.warnings.length} thing{g.warnings.length === 1 ? "" : "s"} to check before approving</summary>
          {g.warnings.map((x, i) => <div key={i} className="pf-li-warn-row">{x}</div>)}
        </details>
      )}

      <button className="pf-dash-link" type="button" onClick={() => setOpen((v) => !v)}>
        {open ? "Hide the observed shapes ▴" : `${g.variants.length} observed shape${g.variants.length === 1 ? "" : "s"} ▾`}
      </button>
      {open && (
        <div className="pf-li-variants">
          {g.variants.map((v, i) => (
            <div key={i} className="pf-li-variant">
              <span className="pf-li-vmeta">{v.sessions} sessions · {Math.round(v.coverage * 100)}% coverage</span>
              <Steps steps={v.steps} />
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function PolicyBlock({ p }: { p: Policy }) {
  return (
    <div className={`pf-li-policy ${CONF_TONE[p.confidence] || "slate"}`}>
      <div className="pf-li-policy-head">
        Inferred policy
        <span className="pf-li-conf">{p.confidence} confidence</span>
        <span className="pf-li-src">from observed behaviour — not a policy document</span>
      </div>
      <div className="pf-li-stmt">{p.statement}</div>
      {p.rationale && <div className="pf-li-why"><span className="lbl">because</span>{p.rationale}</div>}
      {p.caveats?.map((cv, i) => <div key={i} className="pf-li-caveat">{cv}</div>)}
    </div>
  );
}

function Chips({ items, tone = "" }: { items: Counted[]; tone?: string }) {
  if (!items.length) return <span className="muted">none observed</span>;
  return (
    <>
      {items.map((i) => (
        // The count travels WITH the value everywhere. "Which roles called
        // this" is not the reviewer's question; "which roles, how often, and
        // is the tail an accident" is — and a bare list cannot answer it.
        <span key={i.value} className={`pf-li-chip ${tone} ${i.share !== undefined && i.share < 0.05 ? "rare" : ""}`}
              title={`${i.sessions} session(s), ${i.calls} call(s)`}>
          {i.value}<span className="pf-li-n">{i.sessions}</span>
        </span>
      ))}
    </>
  );
}

function CandidateCard({ c }: { c: Candidate }) {
  const [open, setOpen] = useState(false);
  const p = c.inferred_policy;
  return (
    <div className={`pf-li-card${c.contested.length ? " contested" : ""}`}>
      <div className="pf-li-head">
        <code className="pf-li-name">{c.intent || c.tool_name}</code>
        <span className="pf-li-tool">tool {c.tool_name}</span>
        <span className={`pf-dash-chip ${c.side_effect === "read" ? "slate" : "gold"}`}>{c.side_effect}</span>
        <span className="pf-li-support">{c.support_sessions} sessions · {c.support_calls} calls</span>
        <span className="pf-li-spacer" />
        {/* Contested is the first thing read, not a footnote: it is the
            difference between observed practice and observed misbehaviour. */}
        {c.contested.length > 0 && (
          <span className="pf-dash-chip red" title={c.contested.map((x) => `${x.check_id}: ${x.sessions} sessions`).join("\n")}>
            contested by {c.contested.length} integrity check{c.contested.length === 1 ? "" : "s"}
          </span>
        )}
        <span className="pf-dash-chip slate">{c.review_status}</span>
      </div>

      {c.description && <div className="pf-li-desc">{c.description}</div>}

      {/* The inferred half is boxed away from the counted half on every card:
          a mined rule cites observed practice, never a clause someone wrote. */}
      {p && <PolicyBlock p={p} />}

      <div className="pf-li-grid">
        <div><span className="lbl">callers observed</span><Chips items={c.observed_roles} /></div>
        <div><span className="lbl">channels</span><Chips items={c.observed_channels} /></div>
        <div><span className="lbl">arguments</span>
          {c.params.length ? c.params.map((x) => <span key={x} className="pf-li-chip">{x}</span>) : <span className="muted">none</span>}
        </div>
        <div><span className="lbl">fields returned</span>
          {c.fields.length ? c.fields.map((x) => <span key={x} className="pf-li-chip">{x}</span>) : <span className="muted">none declared</span>}
        </div>
      </div>

      <div className="pf-li-facts">
        {c.expected_rows_p99 !== null && <span>rows p99 <strong>{c.expected_rows_p99}</strong></span>}
        {c.mandatory_filters.map((f) => <span key={f}>always <code>{f}</code></span>)}
        {c.closing_obligation && <span>almost always followed by <code>{c.closing_obligation}</code></span>}
      </div>

      {c.warnings.length > 0 && (
        <details className="pf-li-warn" open={c.contested.length > 0}>
          <summary>{c.warnings.length} thing{c.warnings.length === 1 ? "" : "s"} to check before approving</summary>
          {c.warnings.map((w, i) => <div key={i} className="pf-li-warn-row">{w}</div>)}
        </details>
      )}

      <button className="pf-dash-link" type="button" onClick={() => setOpen((v) => !v)}>
        {open ? "Hide evidence ▴" : `Evidence — ${c.example_sessions.length} example session${c.example_sessions.length === 1 ? "" : "s"} ▾`}
      </button>
      {open && (
        <div className="pf-li-evidence">
          {c.example_sessions.map((s) => <code key={s}>{s}</code>)}
          {c.contested.map((x) => (
            <div key={x.check_id} className="pf-li-warn-row">
              {x.check_id}: {x.findings} finding(s) across {x.sessions} supporting session(s)
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export default function LearnedIntents({ demo, active }: { demo: DemoConfig; active?: boolean }) {
  const [days, setDays] = useState(30);
  const [minSessions, setMinSessions] = useState(3);
  const [withLlm, setWithLlm] = useState(false);
  const [cands, setCands] = useState<Candidate[] | null>(null);
  const [groups, setGroups] = useState<Grouped[]>([]);
  const [cohorts, setCohorts] = useState<CohortRow[]>([]);
  const [ops, setOps] = useState<Operation[]>([]);
  const [explained, setExplained] = useState<{episodes:number;fraction:number;shapes:number}|null>(null);
  const [baseline, setBaseline] = useState<Baseline | null>(null);
  const [rejected, setRejected] = useState<string[]>([]);
  const [status, setStatus] = useState<"idle" | "mining" | "error">("idle");
  const [error, setError] = useState("");

  const mine = useCallback(async () => {
    setStatus("mining"); setError("");
    try {
      // Two hops on purpose, mirroring the service split: eval-engine owns the
      // only ClickHouse reader and returns AGGREGATES; semantic-layer owns the
      // catalog schema and the candidate/approve pattern and turns them into
      // candidates. Aggregates cross the boundary, raw spans never do.
      const q = `since=${days * 86400}&app=${encodeURIComponent(demo.id)}&min_sessions=${minSessions}`;
      // Grouped runs are preferred: N shapes of one operation become one
      // candidate and one model call, rather than N near-identical policies.
      const [pr, gr, cr, er, br] = await Promise.all([
        fetch(`/eval/behavior/tools?${q}`),
        fetch(`/eval/behavior/intents?${q}`),
        fetch(`/eval/behavior/cohorts?${q}`),
        fetch(`/eval/behavior/episodes?since=${days * 86400}&app=${encodeURIComponent(demo.id)}&min_episodes=${minSessions}`),
        fetch(`/eval/behavior/baseline?since=${days * 86400}&app=${encodeURIComponent(demo.id)}`),
      ]);
      const pj = await pr.json();
      if (!pr.ok) throw new Error(pj?.error || `${pr.status} reading behaviour`);
      const profiles = pj.tools || [];
      const runs = gr.ok ? ((await gr.json()).intents || []) : [];
      const cos = cr.ok ? ((await cr.json()).cohorts || []) : [];
      const ej = er.ok ? await er.json() : {};
      const shapes = ej.shapes || [];
      setExplained(ej.explained || null);
      setBaseline(br.ok ? await br.json() : null);
      if (!profiles.length) {
        setCands([]); setGroups([]); setCohorts([]); setOps([]); setExplained(null);
        setRejected([]); setStatus("idle");
        return;
      }
      const mr = await fetch("/design/semantic/intents/mine", {
        method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({ profiles, intent_groups: runs, cohorts: cos,
                               episode_shapes: shapes, min_sessions: minSessions,
                               infer_policy: withLlm }),
      });
      const mj = await mr.json();
      if (!mr.ok) throw new Error(mj?.detail || mj?.error || `${mr.status} mining`);
      setCands(mj.candidates || []); setGroups(mj.intent_groups || []);
      setCohorts(mj.cohorts || []); setOps(mj.operations || []);
      setRejected(mj.rejected || []);
      setStatus("idle");
    } catch (e: any) {
      setError(String(e?.message || e)); setStatus("error");
    }
  }, [days, minSessions, withLlm, demo.id]);

  const contested = (cands || []).filter((c) => c.contested.length).length;

  return (
    <main>
      <section className="pf-panel">
        <div className="pf-dash-panel-head">
          <h2>Learned intents</h2>
          <div className="pf-dash-panel-actions">
            <button className="pf-btn primary" onClick={mine} disabled={status === "mining"}>
              {status === "mining" ? "Mining…" : "Mine from behaviour"}
            </button>
          </div>
        </div>
        <p className="pf-hint" style={{ marginTop: 0 }}>
          The onboarding path for a deployment with <strong>no policy document</strong>. Every
          session already records who called which tool, with what arguments, what came back and
          in what order — most of an intent catalog, sitting in the trace store. This reads those
          traces and proposes candidates.
        </p>
        <p className="pf-hint">
          <strong>This is observation, not assessment.</strong> While a baseline is forming, the
          job is to learn how tools are actually called and in what patterns — nothing below is a
          finding, and nothing here is wrong. Judging traffic before there is an approved shape to
          compare against manufactures problems out of the absence of one.
          <br /><br />
          When you do come to approve: <strong>frequency is not legitimacy.</strong> Observed
          callers are not permitted callers, and an agent that has been leaking for months makes
          leaking look normal — so every pattern carries the integrity violations found on the
          sessions supporting it. You approve a <em>narrowing</em>. Nothing here is published.
        </p>

        <div className="pf-fields">
          <label>Window
            <select value={days} onChange={(e) => setDays(Number(e.target.value))}>
              {[1, 7, 30, 90].map((d) => <option key={d} value={d}>last {d} day{d === 1 ? "" : "s"}</option>)}
            </select>
          </label>
          <label>Minimum sessions
            <input type="number" min={1} value={minSessions}
                   onChange={(e) => setMinSessions(Math.max(1, Number(e.target.value)))} />
          </label>
          <label className="pf-li-toggle">
            <input type="checkbox" checked={withLlm} onChange={(e) => setWithLlm(e.target.checked)} />
            Also ask a model what rule each pattern implies (optional, one call per pattern)
          </label>
        </div>
        {!withLlm && (
          <p className="pf-hint">
            Off by default, and secondary by design: learning the patterns is counting, and
            counting is reproducible, auditable and free. Naming them and reading a rule out of
            them is a later step — useful when you come to approve, not part of establishing what
            normal looks like.
          </p>
        )}
        {error && <p className="pf-error">{error}</p>}
      </section>

      {cands !== null && (
        <section className="pf-panel" style={{ marginTop: 14 }}>
          <div className="pf-tr-summary">
            <span className="pf-tr-count">{cands.length} candidate{cands.length === 1 ? "" : "s"}</span>
            {contested > 0 && <span className="pf-dash-chip red">{contested} contested</span>}
            {rejected.length > 0 && (
              <span className="pf-dash-chip slate" title={rejected.join("\n")}>{rejected.length} rejected</span>
            )}
          </div>
          {cands.length === 0 && (
            <div className="pf-dash-feed-status">
              No tool calls in this window for {demo.label} — run some sessions first, or widen the window.
            </div>
          )}
          {cands.map((c) => <CandidateCard key={c.tool_name} c={c} />)}
        </section>
      )}

      {baseline && baseline.observed_episodes > 0 && (
        <section className="pf-panel" style={{ marginTop: 14 }}>
          <BaselineBanner b={baseline} />
        </section>
      )}

      {ops.length > 0 && (
        <section className="pf-panel" style={{ marginTop: 14 }}>
          <div className="pf-dash-panel-head">
            <h2>Call patterns — how each action is reached</h2>
          </div>
          <p className="pf-hint" style={{ marginTop: 0 }}>
How tools are actually called. Sessions are cut into <em>episodes</em> — one operation on one subject, bounded by the
            subject changing or by a side effect — and grouped by the act that closed them. Each
            row is therefore every observed way of reaching one write, side by side. That
            comparison is the evidence: a step present in most paths is a candidate
            <em> precondition</em>, and a write frequently performed with <strong>nothing first</strong>
            is either a control that does not exist or one being bypassed — a distinction the
            traces cannot settle and a reviewer can.
            {explained && (
              <> These shapes account for <strong>{Math.round(explained.fraction * 100)}%</strong> of
              the {explained.episodes} episodes observed.</>
            )}
          </p>
          {ops.map((o) => <OperationCard key={o.operation} o={o} />)}
        </section>
      )}

      {cohorts.length > 0 && (
        <section className="pf-panel" style={{ marginTop: 14 }}>
          <div className="pf-dash-panel-head"><h2>Access boundaries — what differs between cohorts</h2></div>
          <p className="pf-hint" style={{ marginTop: 0 }}>
            An access policy is precisely what makes one group of callers behave differently from
            another, so the <em>differences</em> are where it is visible — and they appear in no
            single cohort's profile. Strongest first: a field the same tool returned to others but
            never to this cohort cannot be explained by what they happened to need. Weakest, and
            easiest to over-read: never having done something. That is scored per operation against
            how often other cohorts reach it, so a rarely-used tool is marked
            <em> inconclusive</em> rather than counted as a restriction.
          </p>
          {cohorts.map((c) => <CohortCard key={c.role} c={c} />)}
        </section>
      )}

      {groups.length > 0 && (
        <section className="pf-panel" style={{ marginTop: 14 }}>
          <div className="pf-dash-panel-head"><h2>Processes — intents that span several calls</h2></div>
          <p className="pf-hint" style={{ marginTop: 0 }}>
            Not every intent is one call, and one intent rarely has one shape — the agent
            sometimes already held part of the data, sometimes went further. Runs that share
            most of their steps are grouped, so each row below is <em>one operation</em> with
            every shape it was observed in. <strong>Always</strong> is the backbone present in
            every shape; <strong>sometimes also</strong> are extensions, never requirements.
            That split is counted, not inferred. Order is the evidence: a step that consistently
            precedes another is a candidate <em>precondition</em>.
          </p>
          {groups.map((g) => <GroupCard key={g.core_steps.join(">") + g.intent} g={g} />)}
        </section>
      )}

      {cands !== null && cands.length > 0 && (
        <section className="pf-panel" style={{ marginTop: 14 }}>
          <p className="pf-hint" style={{ margin: 0 }}>
            {/* No approve button, deliberately. The publish path (approved
                candidates → build_intent_catalog → the artifacts volume) is
                not built yet, and a control that looks like it approves
                something while doing nothing is worse than its absence. */}
            <strong>Review only.</strong> Approving and publishing a mined catalog is not wired
            yet — these candidates are a proposal to read, not a change to accept. A learned
            catalog also cannot express prohibition: absence of evidence is not evidence that
            something is forbidden, so it complements a policy document rather than replacing one.
          </p>
        </section>
      )}
    </main>
  );
}
