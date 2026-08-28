/*
 * SessionRunner — Verdict's one screen. Each scenario is one session against
 * the app agent: a caller, a channel, and a list of user turns (or a scripted
 * tool sequence the agent replays). There is no governed side and no verdict
 * rendered by the app itself; the row shows the transcript the agent produced
 * and the checks Prefront's out-of-band engine SHOULD raise for it
 * (`expected_findings`), grouped by check family.
 *
 * The orchestrator is the source of truth for the catalogue (`/api/scenarios`)
 * and runs a session per call (`/api/run?only=ID&repeat=N&variant=V`). Every
 * session lands in ClickHouse with its `session.id`; "Inspect session" below
 * pulls it back via oob-ingest without leaving this page.
 *
 * Copied from the main Prefront app's Runtime tab (same component name, same
 * behavior) with one difference: no `onOpenSession`/`onOpenTrace` callbacks —
 * those exist there to jump to a separate Observability tab, which Verdict
 * doesn't have. The inline session flyout below is self-sufficient.
 */
import { useEffect, useMemo, useState } from "react";
import type { DemoConfig } from "../demo";
import { SessionDetail } from "./SessionDetail";

type Finding = { check: string; evidence: string; policy?: string };
type Scenario = {
  id: string; family: string; family_label: string; title: string; checks: string[];
  caller: string; role: string; user_id: number; channel: string; mode: "llm" | "replay";
  baseline: boolean; hidden: boolean; repeat: number; variant: string;
  turns: (string | null)[]; steps: string[][]; risk: string; expected_findings: Finding[];
};
type Family = { id: string; label: string; scenarios: Scenario[] };
type ToolCall = {
  tool: string; args: Record<string, unknown>;
  result: { columns?: string[]; rows?: Record<string, unknown>[]; row_count?: number; error?: string };
};
type Turn = {
  turn: number; mode: string; user: string | null; answer: string | null; tool_calls: ToolCall[];
  llm_calls: number; error?: string | null; trace_id?: string | null;
};
type Run = Omit<Scenario, "turns"> & {
  session_id: string; trace_id: string | null; variant: string; repeat_index: number;
  turns: Turn[]; tools_called: string[]; error: string | null;
};

const FAMILY_TONE: Record<string, string> = { F1: "f1", F2: "f2", F3: "f3", POP: "pop", BASE: "base" };

function fmtArgs(args: Record<string, unknown>) {
  return Object.entries(args || {}).map(([k, v]) => `${k}=${typeof v === "string" ? JSON.stringify(v) : String(v)}`).join(", ");
}

function RowsTable({ rows, columns, sensitive }: { rows: Record<string, unknown>[]; columns?: string[]; sensitive: Set<string> }) {
  if (!rows?.length) return null;
  const cols = columns?.length ? columns : Object.keys(rows[0]);
  return (
    <table className="pf-diff-rows">
      <thead><tr>{cols.map((c) => <th key={c}>{c}</th>)}</tr></thead>
      <tbody>
        {rows.slice(0, 5).map((r, i) => (
          <tr key={i}>{cols.map((c) => {
            const v = r[c] === null || r[c] === undefined ? "" : String(r[c]);
            return <td key={c} className={sensitive.has(c) ? "sensitive" : ""} title={v}>{v.length > 60 ? v.slice(0, 60) + "…" : v}</td>;
          })}</tr>
        ))}
      </tbody>
    </table>
  );
}

function Transcript({ run, sensitive }: { run: Run; sensitive: Set<string> }) {
  return (
    <div className="pf-sess-transcript">
      {run.turns.map((t) => (
        <div key={t.turn} className="pf-sess-turn">
          <div className="pf-sess-turn-head">
            <span className="pf-sess-turn-n">turn {t.turn}</span>
            <span className={`pf-oob-chip ${t.mode === "replay" ? "amber" : ""}`}>{t.mode === "replay" ? "scripted" : `LLM · ${t.llm_calls} call${t.llm_calls === 1 ? "" : "s"}`}</span>
            {t.error && <span className="pf-oob-chip red">error</span>}
          </div>
          {t.user && <div className="pf-sess-msg user"><span className="lbl">user</span>{t.user}</div>}
          {t.tool_calls.map((c, i) => {
            const r = c.result || {};
            return (
              <div key={i} className={`pf-sess-call ${r.error ? "error" : ""}`}>
                <div className="pf-sess-call-head">
                  <span className="lbl">tool</span>
                  <code>{c.tool}({fmtArgs(c.args)})</code>
                  {typeof r.row_count === "number" && !r.error && <span className="pf-oob-chip">{r.row_count} row{r.row_count === 1 ? "" : "s"}</span>}
                  {r.error && <span className="pf-oob-chip red">ERROR</span>}
                </div>
                {r.error && <div className="pf-diff-err">{r.error}</div>}
                {r.rows && r.rows.length > 0 && <RowsTable rows={r.rows} columns={r.columns} sensitive={sensitive} />}
              </div>
            );
          })}
          {t.answer && <div className="pf-sess-msg agent"><span className="lbl">agent</span>{t.answer}</div>}
          {t.error && <div className="pf-diff-err">{t.error}</div>}
        </div>
      ))}
    </div>
  );
}

function Findings({ s }: { s: Scenario }) {
  if (!s.expected_findings.length) {
    return <div className="pf-sess-findings clean"><span className="pf-verdict v-allow">CLEAN</span><div className="pf-diff-reason">No finding expected — this is a control session for the population checks.</div></div>;
  }
  return (
    <div className="pf-sess-findings">
      <div className="pf-sess-findings-head">Prefront should find</div>
      {s.expected_findings.map((f, i) => (
        <div key={i} className="pf-sess-finding">
          <span className={`pf-sess-check ${FAMILY_TONE[s.family] || ""}`}>{f.check}</span>
          {f.policy && <span className="pf-sess-policy" title="loan_underwriting_policy.md section">§{f.policy}</span>}
          <span className="pf-sess-evidence">{f.evidence}</span>
        </div>
      ))}
    </div>
  );
}

/** Slide-out panel showing the OOB view of one session without leaving the
 *  page. Polls while open: the OTLP tap batches spans for a few seconds after
 *  a run, so the first fetch right after "Run" is usually a 404. */
function SessionFlyout({ sessionId, scenario, onClose }: {
  sessionId: string; scenario: Scenario; onClose: () => void;
}) {
  const [tick, setTick] = useState(0);
  useEffect(() => {
    const t = window.setInterval(() => setTick((n) => n + 1), 3000);
    return () => window.clearInterval(t);
  }, [sessionId]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <>
      <div className="pf-flyout-backdrop" onClick={onClose} />
      <aside className="pf-flyout" role="dialog" aria-label={`Session ${sessionId}`}>
        <div className="pf-flyout-head">
          <div>
            <div className="pf-flyout-title"><span className="pf-diff-id">{scenario.id}</span> {scenario.title}</div>
            <div className="pf-oob-subtle">as ingested out of band · refreshes every 3s</div>
          </div>
          <div className="pf-oob-actions">
            <button className="pf-btn sm" onClick={onClose}>Close ✕</button>
          </div>
        </div>
        <div className="pf-flyout-body">
          <SessionDetail sessionId={sessionId} refreshKey={tick} onClose={onClose} />
          {scenario.expected_findings.length > 0 && (
            <div className="pf-flyout-findings"><Findings s={scenario} /></div>
          )}
        </div>
      </aside>
    </>
  );
}

export default function SessionRunner({ demo }: { demo: DemoConfig }) {
  const [flyout, setFlyout] = useState<{ sessionId: string; scenario: Scenario } | null>(null);
  const [server, setServer] = useState(demo.orchestratorUrl);
  const [families, setFamilies] = useState<Family[] | null>(null);
  const [results, setResults] = useState<Record<string, Run[]>>({});
  const [running, setRunning] = useState<Record<string, boolean>>({});
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [variant, setVariant] = useState("");
  const [repeat, setRepeat] = useState(0);
  const [open, setOpen] = useState<Record<string, boolean>>({});

  async function loadCatalog() {
    setError(""); setLoading(true); setResults({});
    try {
      const res = await fetch(`${server}/api/scenarios`);
      const json = await res.json();
      if (json.error) throw new Error(json.error);
      if (!Array.isArray(json.families)) throw new Error("not a scenario catalogue");
      setFamilies(json.families);
    } catch (e: any) {
      setError(String(e.message || e)); setFamilies(null);
    } finally { setLoading(false); }
  }

  async function runOne(s: Scenario) {
    setRunning((r) => ({ ...r, [s.id]: true }));
    const qs = new URLSearchParams({ only: s.id });
    if (variant) qs.set("variant", variant);
    if (repeat > 0) qs.set("repeat", String(repeat));
    try {
      const res = await fetch(`${server}/api/run?${qs}`);
      const json = await res.json();
      if (json.error) throw new Error(json.error);
      setResults((m) => ({ ...m, [s.id]: json }));
      setOpen((o) => ({ ...o, [s.id]: true }));
    } catch (e: any) {
      setResults((m) => ({ ...m, [s.id]: [{ error: String(e.message || e) } as Run] }));
    } finally { setRunning((r) => ({ ...r, [s.id]: false })); }
  }

  // Bounded concurrency: every session opens MCP connections and LLM calls;
  // a few at a time keeps the agent responsive.
  async function runAll(only?: Scenario[]) {
    const queue = [...(only ?? (families ?? []).flatMap((f) => f.scenarios))];
    const worker = async () => { while (queue.length) { const s = queue.shift(); if (s) await runOne(s); } };
    await Promise.all(Array.from({ length: Math.min(3, queue.length) }, worker));
  }

  useEffect(() => { loadCatalog(); }, []); // eslint-disable-line

  const sensitive = useMemo(() => new Set<string>(demo.sensitiveFields), [demo.sensitiveFields]);
  const all = useMemo(() => (families ?? []).flatMap((f) => f.scenarios), [families]);
  const done = Object.values(results).filter((r) => r.length && !(r[0] as any).error).length;
  const checks = useMemo(() => new Set(all.flatMap((s) => s.checks)).size, [all]);

  return (
    <main>
      {flyout && <SessionFlyout sessionId={flyout.sessionId} scenario={flyout.scenario} onClose={() => setFlyout(null)} />}
      <div className="pf-panel">
        <h2><span className="pf-step-badge">1</span>Run the sessions</h2>
        <p className="pf-hint">
          <strong>{demo.label}</strong> is an ungoverned deployment: one LLM agent calling the shop's own
          API over MCP, no policy layer. Each row below is a <strong>session</strong> — a signed-in
          caller on a channel, one or more user turns — designed so that its trace exhibits one of the
          failure modes Prefront's out-of-band checks detect. <em>LLM</em> sessions let the model pick
          the tools; <em>scripted</em> sessions replay an exact tool sequence through the same MCP path
          so the finding is guaranteed. Nothing here is enforced or judged: the verdicts belong to the
          evaluator — click <strong>Inspect session ▸</strong> on any run to see the ingested trace.
        </p>
        <div className="pf-fields">
          <label style={{ gridColumn: "1 / -1" }}>Demo server URL
            <input value={server} onChange={(e) => setServer(e.target.value)} />
          </label>
          <label>Agent variant
            <select value={variant} onChange={(e) => setVariant(e.target.value)}>
              <option value="">scenario default</option>
              <option value="v1">v1 — deployed prompt, temperature 0</option>
              <option value="v2">v2 — "proactive" prompt edit, temperature 0.9</option>
            </select>
          </label>
          <label>Repeat (population checks)
            <select value={repeat} onChange={(e) => setRepeat(Number(e.target.value))}>
              <option value={0}>scenario default</option>
              {[1, 3, 5, 10].map((n) => <option key={n} value={n}>{n}×</option>)}
            </select>
          </label>
        </div>
        <div className="pf-publish-row">
          <button className="pf-btn" onClick={loadCatalog} disabled={loading}>{loading ? "Loading…" : "Reload catalogue"}</button>
          <button className="pf-btn primary" onClick={() => runAll()} disabled={!families}>Run all</button>
          {families && (
            <span className="pf-summary" style={{ margin: 0 }}>
              <span className="pf-pill">{all.length} sessions</span>
              <span className="pf-pill">{checks} checks covered</span>
              {done > 0 && <span className="pf-pill approved">{done} run</span>}
            </span>
          )}
        </div>
        {error && <p className="pf-error">{error}<span style={{ color: "var(--muted)", marginLeft: 8 }}>— is the demo server running?</span></p>}
      </div>

      {families?.map((fam) => (
        <div key={fam.id} className="pf-panel">
          <div className="pf-sess-family-head">
            <span className={`pf-sess-family ${FAMILY_TONE[fam.id] || ""}`}>{fam.id}</span>
            <h3>{fam.label}</h3>
            <button className="pf-btn sm" style={{ marginLeft: "auto" }} onClick={() => runAll(fam.scenarios)}>Run family ▶</button>
          </div>
          {fam.scenarios.map((s) => {
            const runs = results[s.id];
            const busy = running[s.id];
            const failed = runs?.length && (runs[0] as any).error && !runs[0].session_id;
            return (
              <div key={s.id} className="pf-diff-scn">
                <div className="pf-diff-scn-head">
                  <span className="pf-diff-id">{s.id}</span>
                  <span className="pf-diff-cap">{s.title}</span>
                  <span className="pf-diff-caller">{s.caller} · {s.role} · <code>{s.channel}</code></span>
                  <span className={`pf-oob-chip ${s.mode === "replay" ? "amber" : "teal"}`}>{s.mode === "replay" ? "scripted" : "LLM"}</span>
                  {s.repeat > 1 && <span className="pf-oob-chip">×{s.repeat}</span>}
                  {s.variant !== "v1" && <span className="pf-oob-chip">{s.variant}</span>}
                  <span style={{ marginLeft: "auto", display: "flex", gap: 8, alignItems: "center" }}>
                    {s.checks.map((c) => <span key={c} className={`pf-sess-check ${FAMILY_TONE[s.family] || ""}`}>{c}</span>)}
                    <button className="pf-btn sm" onClick={() => runOne(s)} disabled={busy}>{busy ? "Running…" : runs ? "Re-run" : "Run ▶"}</button>
                  </span>
                </div>
                <div className="pf-sess-block pf-sess-block-query">
                  <div className="pf-sess-block-head">Query to agent</div>
                  <div className="pf-sess-turns">
                    {s.turns.map((t, i) => (
                      <div key={i} className="pf-sess-turn-preview">
                        {t && <div className="pf-diff-q">{t}</div>}
                        {s.steps[i]?.length > 0 && (
                          <div className="pf-sess-steps">
                            <span className="pf-sess-steps-lbl">scripted steps</span>
                            {s.steps[i].map((st, j) => <code key={j}>{st}</code>)}
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                  <div className="pf-diff-reason"><span className="lbl">why it's risky</span>{s.risk}</div>
                </div>
                {failed && <p className="pf-error">{(runs![0] as any).error}</p>}
                {runs && !failed && (
                  <div className="pf-sess-results">
                    {runs.map((run) => (
                      <div key={run.session_id} className="pf-sess-run" style={{ marginTop: 10 }}>
                        <div className="pf-diff-side bad">
                          <div className="pf-diff-side-head">
                            What the agent did · session <code>{run.session_id}</code>
                            {runs.length > 1 && <span className="pf-oob-chip" style={{ marginLeft: 6 }}>run {run.repeat_index + 1}/{runs.length} · {run.variant}</span>}
                            <button className="pf-link" style={{ marginLeft: 8 }} onClick={() => setOpen((o) => ({ ...o, [run.session_id]: !(o[run.session_id] ?? o[s.id]) }))}>
                              {(open[run.session_id] ?? open[s.id]) ? "collapse" : "expand"}
                            </button>
                          </div>
                          <div className="pf-diff-side-body">
                            <span className="pf-verdict v-leak">UNGOVERNED</span>
                            <div className="pf-diff-reason"><span className="lbl">tools</span>{run.tools_called.length ? run.tools_called.map((t, i) => <code key={i} style={{ marginRight: 6 }}>{t}</code>) : "none"}</div>
                            {run.error && <div className="pf-diff-err">{run.error}</div>}
                            {(open[run.session_id] ?? open[s.id]) && <Transcript run={run} sensitive={sensitive} />}
                            <button className="pf-btn sm" style={{ marginTop: 8 }} onClick={() => setFlyout({ sessionId: run.session_id, scenario: s })}>Inspect session ▸</button>
                          </div>
                        </div>
                        <div className="pf-diff-side">
                          <div className="pf-diff-side-head">What Prefront should report</div>
                          <div className="pf-diff-side-body"><Findings s={s} /></div>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
                {!runs && (
                  <div className="pf-sess-block pf-sess-block-response pending">
                    <div className="pf-sess-block-head muted">Agent response</div>
                    <div className="pf-sess-placeholder">not run yet — click <strong>Run ▶</strong> to see the transcript</div>
                  </div>
                )}
                {!runs && <div className="pf-sess-findings-inline"><Findings s={s} /></div>}
              </div>
            );
          })}
        </div>
      ))}
    </main>
  );
}
