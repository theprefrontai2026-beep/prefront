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
      const [pr, gr] = await Promise.all([
        fetch(`/eval/behavior/tools?${q}`),
        fetch(`/eval/behavior/intents?${q}`),
      ]);
      const pj = await pr.json();
      if (!pr.ok) throw new Error(pj?.error || `${pr.status} reading behaviour`);
      const profiles = pj.tools || [];
      const runs = gr.ok ? ((await gr.json()).intents || []) : [];
      if (!profiles.length) {
        setCands([]); setGroups([]); setRejected([]); setStatus("idle");
        return;
      }
      const mr = await fetch("/design/semantic/intents/mine", {
        method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({ profiles, intent_groups: runs, min_sessions: minSessions, infer_policy: withLlm }),
      });
      const mj = await mr.json();
      if (!mr.ok) throw new Error(mj?.detail || mj?.error || `${mr.status} mining`);
      setCands(mj.candidates || []); setGroups(mj.intent_groups || []);
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
          <strong>Frequency is not legitimacy.</strong> Mining learns what the agent <em>did</em>,
          never what it was allowed to do. Observed callers are not permitted callers, and an
          agent that has been leaking for months makes leaking look normal — so every candidate
          carries the integrity violations found on the sessions that support it. You are
          approving a <em>narrowing</em>, not a rubber stamp. Nothing here is published.
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
            Infer the policy behind each pattern (one LLM call per tool)
          </label>
        </div>
        {!withLlm && (
          <p className="pf-hint">
            Off by default: the counted half needs no model, is reproducible, and costs nothing.
            Turn it on and each candidate also gets a name and a plain-language reading of the
            rule its behaviour implies — advisory, and separated from the counted facts on every card.
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
