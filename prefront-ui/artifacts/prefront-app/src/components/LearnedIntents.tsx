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

import { useCallback, useEffect, useState } from "react";
import type { DemoConfig } from "../demos";
import ProcessMap from "./ProcessMap";
import WorkflowStrips, { shapeKey, STRIP_LIMIT, type Approval, type Policy, type Shape } from "./WorkflowStrips";


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

/** The approved set, and the one control that turns it into a real artifact.
 *
 *  Deliberately a two-step: DRY RUN renders the exact YAML and every problem
 *  without writing anything, and only then is publishing offered. The artifact
 *  this writes is the same file the hand-authored path produces and the same
 *  one Family 3 grades against, so "see precisely what you are about to
 *  install" is not a nicety here.
 */
function PublishBar({ demo, approvals, shapes, state, setState, onPublished }: {
  demo: DemoConfig;
  approvals: Record<string, Approval>;
  shapes: Record<string, Shape>;
  state: { busy: boolean; msg: string; err: string; problems: string[] };
  setState: (s: { busy: boolean; msg: string; err: string; problems: string[] }) => void;
  onPublished: () => void;
}) {
  const [preview, setPreview] = useState("");
  const keys = Object.keys(approvals);
  const noRoles = keys.filter((k) => (approvals[k]?.roles || []).length === 0).length;

  const payload = (overwrite: boolean, dryRun: boolean) => ({
    datasource_id: demo.id,
    overwrite, dry_run: dryRun,
    approved: keys.map((k) => {
      const sh = shapes[k];
      return {
        // The terminal step names the operation: the earlier ones are the
        // evidence gathered for it, not its identity.
        intent: sh?.steps[sh.steps.length - 1] || k,
        steps: sh?.steps || [],
        side_effect: sh?.closed_by ? "write" : "read",
        approved_roles: approvals[k].roles,
        expected_rows_p99: null,
      };
    }),
  });

  const call = async (overwrite: boolean, dryRun: boolean) => {
    setState({ busy: true, msg: "", err: "", problems: [] });
    try {
      const r = await fetch("/design/semantic/intents/publish", {
        method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify(payload(overwrite, dryRun)),
      });
      const j = await r.json();
      if (!r.ok) {
        const d = j?.detail;
        setState({ busy: false, msg: "", problems: d?.problems || [],
                   err: typeof d === "string" ? d : (d?.error || `${r.status} ${r.statusText}`) });
        return;
      }
      if (dryRun) {
        setPreview(j.yaml || "");
        setState({ busy: false, msg: `${j.intents} intent(s) would be written to ${j.path}`,
                   err: "", problems: j.problems || [] });
      } else {
        setPreview("");
        setState({ busy: false, msg: `Published ${j.intents} intent(s) to ${j.path}. ${j.next || ""}`,
                   err: "", problems: j.problems || [] });
        onPublished();
      }
    } catch (e: any) {
      setState({ busy: false, msg: "", err: String(e?.message || e), problems: [] });
    }
  };

  return (
    <div className="pf-pub">
      <div className="pf-pub-row">
        <strong>{keys.length} pattern{keys.length === 1 ? "" : "s"} in the approved set</strong>
        {noRoles > 0 && (
          <span className="pf-pub-warn">{noRoles} with no caller ticked</span>
        )}
        <span className="pf-li-spacer" />
        <button className="pf-btn sm" type="button" disabled={state.busy}
                onClick={() => call(false, true)}>Preview the catalogue</button>
        <button className="pf-btn sm primary" type="button" disabled={state.busy || !preview}
                onClick={() => call(true, false)}
                title={preview ? "Write it to the artifacts volume" : "Preview it first"}>
          {state.busy ? "Working…" : "Publish"}
        </button>
      </div>
      <p className="pf-hint" style={{ margin: "6px 0 0" }}>
        Publishing writes <code>intent_catalog.yaml</code> for {demo.label} — the same artifact a
        hand-authored catalogue produces, and the one Family 3 grades against. Preview first;
        publishing replaces any existing catalogue.
      </p>
      {state.err && <p className="pf-error">{state.err}</p>}
      {state.msg && <p className="pf-pub-ok">{state.msg}</p>}
      {state.problems.map((p, i) => <div key={i} className="pf-li-warn-row">{p}</div>)}
      {preview && <pre className="pf-pub-yaml">{preview}</pre>}
    </div>
  );
}


export default function LearnedIntents({ demo, active }: { demo: DemoConfig; active?: boolean }) {
  const [days, setDays] = useState(30);
  const [minSessions, setMinSessions] = useState(3);
  const [withLlm, setWithLlm] = useState(false);
  // The workflow under review; focuses the map and nothing else.
  const [focus, setFocus] = useState<{ label: string; tools: string[] } | null>(null);
  const [shapes, setShapes] = useState<Shape[]>([]);
  // The model's reading of each workflow, keyed by shape. Kept beside the
  // shapes rather than inside them: one is counted and always present, the
  // other is inferred, optional, and gated on the baseline having settled.
  const [policies, setPolicies] = useState<Record<string, Policy>>({});
  // Approvals live here rather than in the strip list so they survive a
  // re-mine: a reviewer part-way through a set should not lose it because the
  // window changed. Keyed by the pattern's shape, which is stable.
  const [approvals, setApprovals] = useState<Record<string, Approval>>({});
  const [shapeByKey, setShapeByKey] = useState<Record<string, Shape>>({});
  const [pub, setPub] = useState<{ busy: boolean; msg: string; err: string; problems: string[] }>(
    { busy: false, msg: "", err: "", problems: [] });
  const [baseline, setBaseline] = useState<Baseline | null>(null);
  const [rejected, setRejected] = useState<string[]>([]);
  const [status, setStatus] = useState<"idle" | "mining" | "error">("idle");
  const [error, setError] = useState("");

  // The learning status is fetched on arrival, not only as a side effect of
  // mining. Otherwise the model control is disabled on a page you have just
  // opened for no reason the reader can see, and the only way to discover the
  // deployment is ready is to run a mine you did not want.
  useEffect(() => {
    if (!active) return;
    let alive = true;
    fetch(`/eval/behavior/baseline?since=${days * 86400}&app=${encodeURIComponent(demo.id)}`)
      .then((r) => (r.ok ? r.json() : null))
      .then((j) => { if (alive) setBaseline(j); })
      .catch(() => {});
    return () => { alive = false; };
  }, [active, days, demo.id]);

  const mine = useCallback(async () => {
    setStatus("mining"); setError("");
    try {
      // Two hops on purpose, mirroring the service split: eval-engine owns the
      // only ClickHouse reader and returns AGGREGATES; semantic-layer owns the
      // catalog schema and the candidate/approve pattern and turns them into
      // candidates. Aggregates cross the boundary, raw spans never do.
      const since = days * 86400;
      const app = encodeURIComponent(demo.id);
      const [er, br] = await Promise.all([
        fetch(`/eval/behavior/episodes?since=${since}&app=${app}&min_episodes=${minSessions}`),
        fetch(`/eval/behavior/baseline?since=${since}&app=${app}`),
      ]);
      const ej = er.ok ? await er.json() : {};
      const sh: Shape[] = ej.shapes || [];
      setShapes(sh);
      const bj = br.ok ? await br.json() : null;
      setBaseline(bj);
      if (!sh.length) { setPolicies({}); setRejected([]); setStatus("idle"); return; }

      // The model's reading is asked for ONLY when the reviewer wants it and
      // the baseline has settled — the server refuses otherwise (409), and
      // sending the request anyway would just be a round trip to be told no.
      if (!(withLlm && bj?.ready)) { setPolicies({}); setRejected([]); setStatus("idle"); return; }

      const mr = await fetch("/design/semantic/intents/mine", {
        method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({
          profiles: [],
          // One summary per WORKFLOW, matching what the rows show. The
          // per-tool, per-cohort and grouped variants remain on the API for a
          // caller who wants them; this page asks the one question it renders.
          workflows: sh.map((x) => ({
            steps: x.steps, sessions: x.sessions, occurrences: x.episodes,
            // Deliberately absent, not 0: an episode shape carries no coverage
            // figure, and sending 0 told the model almost nobody who started
            // this run finished it — which it then reported as a process that
            // is not the norm.
            roles: x.roles.map((r) => ({ value: r.value, sessions: r.episodes })),
            contested: [], example_sessions: x.example_sessions,
          })),
          min_sessions: minSessions,
          // Matched to what the list renders, so no visible row is left
          // without a reading for a reason the reader cannot see.
          limit: STRIP_LIMIT,
          // The server enforces the same rule and needs the verdict to do it;
          // it holds no trace store.
          baseline: bj, infer_policy: true,
        }),
      });
      const mj = await mr.json();
      if (!mr.ok) throw new Error(mj?.detail?.error || mj?.detail || mj?.error || `${mr.status} mining`);
      const byKey: Record<string, Policy> = {};
      for (const w of (mj.workflows || [])) {
        if (w.inferred_policy) byKey[(w.steps || []).join(">")] = w.inferred_policy;
      }
      setPolicies(byKey);
      setRejected(mj.rejected || []);
      setStatus("idle");
    } catch (e: any) {
      setError(String(e?.message || e)); setStatus("error");
    }
  }, [days, minSessions, withLlm, demo.id]);

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
          {/* Disabled, not merely defaulted off. The server refuses it too
              (409) — the UI is not the only caller, and "never run the model
              while learning" is a rule about the system, not a preference. */}
          <label className={`pf-li-toggle${baseline?.ready ? "" : " off"}`}>
            <input type="checkbox" checked={withLlm && !!baseline?.ready}
                   disabled={!baseline?.ready}
                   onChange={(e) => setWithLlm(e.target.checked)} />
            Summarise the patterns with a model
            {!baseline?.ready && <span className="pf-li-locked">available once the baseline settles</span>}
          </label>
        </div>
        <p className="pf-hint">
          {baseline?.ready
            ? "The pattern set has settled, so summarising is now meaningful: a model can name each pattern and read the rule it implies, for you to approve."
            : "No model runs while learning. Learning the patterns is counting — reproducible, auditable and free. Summarising is what you do once the pattern set has settled and you are naming things to approve; asking a model to read a rule out of traffic that is still surprising us produces a confident statement about a pattern that may not be the pattern."}
        </p>
        {error && <p className="pf-error">{error}</p>}
      </section>

      {/* The map sits directly under the controls: it is the orientation,
          and orientation comes before inspection. Below it the workflows are
          separated one per row, which is where every decision is made. */}
      <section className="pf-panel" style={{ marginTop: 14 }}>
        <div className="pf-dash-panel-head"><h2>Observed process map</h2></div>
        <p className="pf-hint" style={{ marginTop: 0 }}>
          Every tool called, and every transition observed inside a single operation — sized by
          how often. Nothing here is inferred or arranged: an edge exists because that hop
          happened, and its thickness is the count.
        </p>
        <ProcessMap demo={demo} days={days} active={active} focus={focus?.tools} />
      </section>

      {baseline && baseline.observed_episodes > 0 && (
        <section className="pf-panel" style={{ marginTop: 14 }}>
          <BaselineBanner b={baseline} />
        </section>
      )}

      {shapes.length > 0 && (
        <section className="pf-panel" style={{ marginTop: 14 }}>
          <div className="pf-dash-panel-head"><h2>Observed workflows</h2></div>
          <p className="pf-hint" style={{ marginTop: 0 }}>
            One workflow per row, ordered by how often it happens. Expand one to see its own
            diagram, what a model makes of it, and the decision — separated rather than merged,
            because on a single graph every workflow is drawn over every other and judging any
            one of them means tracing it out of the tangle first.
          </p>
          <WorkflowStrips
            shapes={shapes}
            policies={policies}
            approvals={approvals}
            onApprove={(sh, a) => {
              const k = shapeKey(sh);
              setShapeByKey((m) => ({ ...m, [k]: sh }));
              setApprovals((m) => {
                const next = { ...m };
                if (a === null) delete next[k]; else next[k] = a;
                return next;
              });
              setPub({ busy: false, msg: "", err: "", problems: [] });
            }} />
          {Object.keys(approvals).length > 0 && (
            <PublishBar demo={demo} approvals={approvals} shapes={shapeByKey}
                        state={pub} setState={setPub} onPublished={() => setApprovals({})} />
          )}
        </section>
      )}
    </main>
  );
}
