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

import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { DemoConfig } from "../demos";
import ProcessMap from "./ProcessMap";
import WorkflowStrips, { groupByGoal, STRIP_LIMIT, type Approval, type Policy, type Shape } from "./WorkflowStrips";

// A goal only ever called on its own is a tool, not a workflow — nothing is
// read first, so there is no dependency to show. Hidden and not summarised;
// the counts still include those runs. Fixed rather than a toggle for now —
// see TODO.md entry 23.
const MIN_STEPS = 2;


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

/** The funnel the page is about, as three numbers: every tool call observed,
 *  the distinct sequences they fall into, and the intents those sequences
 *  reach. Stat tiles rather than a chart — three headline counts are the
 *  whole message, and a bar chart of them would compare magnitudes that are
 *  not the point. `null` renders as a dash, never as 0. */
function InsightRow({ calls, tools, sequences, operations, intents }: {
  calls: number | null; tools: number | null;
  sequences: number | null; operations: number | null;
  intents: number | null;
}) {
  const fmt = (n: number | null) =>
    n == null ? "—" : n >= 10000 ? `${(n / 1000).toFixed(1)}K` : n.toLocaleString();
  const tiles = [
    { label: "Tool calls", value: calls, sub: tools != null ? `across ${tools} tools` : "" },
    { label: "Sequences", value: sequences,
      sub: operations != null ? `distinct, in ${operations.toLocaleString()} operations` : "" },
    { label: "Intents", value: intents, sub: intents != null ? "goals those sequences reach" : "mine to count" },
  ];
  return (
    <div className="pf-li-kpis">
      {tiles.map((t, i) => (
        <Fragment key={t.label}>
          {i > 0 && <span className="pf-li-kpi-arrow" aria-hidden="true">›</span>}
          <div className="pf-li-kpi">
            <div className="lbl">{t.label}</div>
            <div className="val">{fmt(t.value)}</div>
            <div className="sub">{t.sub}</div>
          </div>
        </Fragment>
      ))}
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
  // Callers are not chosen on this page, so the server's "no roles" note would
  // appear on every entry and say nothing the reviewer can act on here.
  const shown = (ps: string[]) => ps.filter((p) => !/no roles approved|no allowed_callers\.roles/.test(p));

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
      {shown(state.problems).map((p, i) => <div key={i} className="pf-li-warn-row">{p}</div>)}
      {preview && <pre className="pf-pub-yaml">{preview}</pre>}
    </div>
  );
}


export default function LearnedIntents({ demo, active }: { demo: DemoConfig; active?: boolean }) {
  const [days, setDays] = useState(30);
  const [minSessions, setMinSessions] = useState(3);
  // Always on, with no control on the page: the rows lead with the model's
  // title and one-liner. mine() still asks only once the baseline has settled
  // — the server refuses otherwise (409) — and falls back to counts before.
  const withLlm = true;
  // The workflow under review; focuses the map and nothing else.
  const [focus, setFocus] = useState<{ label: string; tools: string[] } | null>(null);
  const [shapes, setShapes] = useState<Shape[]>([]);
  // Counted the same way the list groups them, so the tile and the rows agree.
  const intentCount = useMemo(
    () => (shapes.length ? groupByGoal(shapes, MIN_STEPS).length : null), [shapes]);
  const [toolStats, setToolStats] = useState<{ calls: number; tools: number } | null>(null);
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
  // When the shown run was mined, and with what settings — the controls can
  // be changed afterwards without re-mining, so they cannot be trusted to say.
  const [minedAt, setMinedAt] = useState<string | null>(null);
  const [minedWith, setMinedWith] = useState<{ days: number; minSessions: number; withLlm: boolean } | null>(null);

  // The run is saved server-side and restored on arrival: the model's readings
  // cost a metered call each and the approvals are a reviewer's work, and both
  // used to vanish on reload. Saving is enabled only once the saved run for
  // THIS demo has loaded, or the empty initial state would overwrite it.
  const [hydratedFor, setHydratedFor] = useState<string | null>(null);
  const lastSaved = useRef<string | null>(null);
  useEffect(() => {
    let alive = true;
    lastSaved.current = null;
    setHydratedFor(null);
    const done = (run: any) => {
      if (!alive) return;
      const p = run?.params || {};
      setShapes(run?.shapes || []);
      setPolicies(run?.policies || {});
      setRejected(run?.rejected || []);
      // Approvals are keyed by goal now. One saved under the old per-shape key
      // matches no row, so it could not be seen or removed — yet it would
      // still publish. Dropped rather than carried.
      const byGoal = <T,>(m: Record<string, T> | undefined) =>
        Object.fromEntries(Object.entries(m || {}).filter(([k]) => k.startsWith("goal:")));
      setApprovals(byGoal<Approval>(run?.approvals));
      setShapeByKey(byGoal<Shape>(run?.approvalShapes));
      setMinedAt(run?.minedAt ?? null);
      setMinedWith(run ? p : null);
      if (p.days) setDays(p.days);
      if (p.minSessions) setMinSessions(p.minSessions);
      setHydratedFor(demo.id);
    };
    fetch(`/api/learned/workflows?demo=${encodeURIComponent(demo.id)}`)
      .then((r) => (r.ok ? r.json() : null))
      .then((j) => done(j?.run ?? null))
      .catch(() => done(null));
    return () => { alive = false; };
  }, [demo.id]);

  useEffect(() => {
    if (hydratedFor !== demo.id) return;
    const body = JSON.stringify({
      demo: demo.id, params: minedWith ?? { days, minSessions, withLlm },
      shapes, policies, rejected, approvals, approvalShapes: shapeByKey, minedAt,
    });
    // The first pass after loading is the loaded run itself; writing it back
    // would only bump its timestamp.
    if (lastSaved.current === null) { lastSaved.current = body; return; }
    if (body === lastSaved.current) return;
    const t = setTimeout(() => {
      fetch("/api/learned/workflows", { method: "PUT", headers: { "content-type": "application/json" }, body })
        .then((r) => { if (r.ok) lastSaved.current = body; })
        .catch(() => {});
    }, 400);
    return () => clearTimeout(t);
    // days/minSessions/withLlm are deliberately absent: changing a control is
    // not a new result, and minedWith already records what this run used.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hydratedFor, demo.id, shapes, policies, rejected, approvals, shapeByKey, minedAt, minedWith]);

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

  // Tool-call totals for the first tile, over the same window as the
  // baseline. Per-tool profiles are the one aggregate that counts calls.
  useEffect(() => {
    if (!active) return;
    let alive = true;
    fetch(`/eval/behavior/tools?since=${days * 86400}&app=${encodeURIComponent(demo.id)}`)
      .then((r) => (r.ok ? r.json() : null))
      .then((j) => {
        if (!alive) return;
        const ts: { calls?: number }[] = j?.tools || [];
        setToolStats(j ? { calls: ts.reduce((a, t) => a + (t.calls || 0), 0), tools: ts.length } : null);
      })
      .catch(() => { if (alive) setToolStats(null); });
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
      setMinedAt(new Date().toISOString());
      setMinedWith({ days, minSessions, withLlm });
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
            closed_by: x.closed_by,
          })),
          min_sessions: minSessions,
          min_steps: MIN_STEPS,
          // One summary per GOAL, matching the rows: every way of reaching
          // the same call is one intent, and one model call.
          group_by_goal: true,
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
      for (const g of (mj.goals || [])) {
        // The model's name and one-liner ride along with its reading: they
        // lead the row, and the tool calls move behind a click.
        if (g.inferred_policy) byKey[g.goal] = {
          ...g.inferred_policy, title: g.title || "", description: g.description || "",
        };
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

        <div className="pf-fields">
          <label>Window
            <select value={days} onChange={(e) => setDays(Number(e.target.value))}>
              {[1, 7, 30, 90].map((d) => <option key={d} value={d}>last {d} day{d === 1 ? "" : "s"}</option>)}
            </select>
          </label>
        </div>
        <InsightRow
          calls={toolStats?.calls ?? null} tools={toolStats?.tools ?? null}
          sequences={baseline?.distinct_shapes ?? null} operations={baseline?.observed_episodes ?? null}
          intents={intentCount} />
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
            One intent per row — every observed way of reaching the same goal, together — ordered
            by how often it happens. Expand one to see what is read before it and each way it was
            reached.
          </p>
          {minedAt && minedWith && (
            <p className="pf-hint">
              Saved run: mined {new Date(minedAt).toLocaleString()} over the last {minedWith.days} day
              {minedWith.days === 1 ? "" : "s"}, minimum {minedWith.minSessions} sessions
              {minedWith.withLlm ? ", with model summaries" : ""}. It stays across reloads, approvals
              included — mine again to refresh it.
              {(minedWith.days !== days || minedWith.minSessions !== minSessions) &&
                " The settings above have changed since; mine again to apply them."}
            </p>
          )}
          <WorkflowStrips
            shapes={shapes}
            minSteps={MIN_STEPS}
            policies={policies}
            approvals={approvals}
            onApprove={(k, sh, a) => {
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
