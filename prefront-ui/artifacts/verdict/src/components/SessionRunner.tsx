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
import { orchestratorFor, type AppIdentity } from "../demo";
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
/** One rule as the engine evaluated it. `fired` is the load-bearing field:
 *  a rule that was checked and did NOT fire is evidence the control ran, which
 *  is exactly what an auditor asks for and what a "here is why" screen has to
 *  show alongside the ones that did. `source` is the clause it was compiled
 *  from — document, section, and the verbatim sentence. */
type RuleOutcome = {
  rule_key: string; rule_type?: string; decision: string;
  fired: boolean; indeterminate?: boolean;
  reason?: string; restricted_fields?: string[]; approver_role?: string;
  missing?: string[];
  conditions?: { field: string; operator: string; value: unknown }[];
  source?: { document?: string; section?: string; text?: string; evidence?: string } | null;
};

/** eval-engine's single-call-safe checks, run IN-BAND by the runtime rather
 *  than out of band over a finished session. */
type InlineCheck = {
  check_id: string; family?: string; status: string; effect?: string; detail?: string;
};

type Governance = {
  trace_id?: string; matched_intent?: string; decision?: string;
  rules_evaluated?: RuleOutcome[];
  inline_checks?: InlineCheck[];
};

type Run = Omit<Scenario, "turns"> & {
  session_id: string; trace_id: string | null; variant: string; repeat_index: number;
  turns: Turn[]; tools_called: string[]; error: string | null;
  /** Present only for an application governed IN-BAND: the decision Prefront
   *  made on the call. An out-of-band application has no decision to report —
   *  its evidence is the evaluator's findings, not a runtime verdict. */
  governed?: {
    intent: string; outcome: string; status: string;
    reasons: string[]; masked_fields: string[]; approver_roles: string[];
    rows?: Record<string, unknown>[]; row_count?: number; answer?: string | null;
    /** The deterministic decision trace the runtime wrote for this call.
     *  Absent for an application whose orchestrator does not return one. */
    governance?: Governance | null;
  };
  /** The same question with NO policy layer in the path. Present only for an
   *  application that runs both ways, and it is what makes the governed
   *  decision mean something: a BLOCK is only interesting beside the rows the
   *  app would otherwise have handed over. */
  ungoverned?: {
    tool?: string; args?: Record<string, unknown>; sql?: string;
    columns?: string[]; rows?: Record<string, unknown>[]; row_count?: number;
    answer?: string | null; error?: string | null;
  };
};

/** Verdict-chip tone from the runtime's own outcome string. */
function verdictTone(outcome: string): string {
  const o = (outcome || "").toUpperCase();
  if (o.startsWith("BLOCK")) return "v-block";
  if (o.includes("APPROVAL")) return "v-appr";
  if (o.includes("MASK")) return "v-mask";
  return "v-allow";
}

/** The key a run's expand/collapse state is stored under.
 *
 *  It used to be `run.session_id`, falling back to the scenario id. That
 *  breaks for an application with no out-of-band session: `session_id` is the
 *  EMPTY STRING there, so every row on the page shared the single key "" and
 *  expanding one expanded all of them. Falling back per-run keeps each row
 *  independent, and a repeated scenario still separates by session id. */
function runKey(run: { session_id: string }, s: { id: string }): string {
  return run.session_id || s.id;
}

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

/** The before/after Prefront comparison, for an application that runs both
 *  ways. Restored from the Runtime tab's RuntimeDiff, which was deleted when
 *  Verdict was extracted — the standalone app kept the session runner and lost
 *  the contrast, which for an in-band application is the entire point: the
 *  governed decision is only legible next to what the same question did with
 *  nothing in the way. */
const condText = (c: { field: string; operator: string; value: unknown }) =>
  `${c.field} ${c.operator} ${typeof c.value === "string" ? JSON.stringify(c.value) : String(c.value)}`;

/** Why the decision came out the way it did.
 *
 *  Rendered full-width BELOW the two columns rather than inside the governed
 *  one: a policy clause is a sentence, and a sentence in a half-width column
 *  wraps into an unreadable ribbon. The order is fired-first — those produced
 *  the outcome — with the evaluated-but-not-fired rules kept visible after
 *  them rather than filtered out, because "this control ran and did not apply"
 *  is a different and weaker claim than "this control was never checked", and
 *  a demo that showed only the hits could not tell them apart.
 */
function PolicyTrace({ gov }: { gov: Governance }) {
  const rules = gov.rules_evaluated || [];
  const checks = gov.inline_checks || [];
  if (!rules.length && !checks.length) return null;

  const fired = rules.filter((r) => r.fired);
  const ordered = [...fired, ...rules.filter((r) => !r.fired)];
  const violated = checks.filter((c) => c.status !== "satisfied");
  const satisfied = checks.filter((c) => c.status === "satisfied");

  return (
    <div className="pf-ptrace">
      <div className="pf-ptrace-head">
        Policy trace
        <span className="pf-ptrace-count">
          {rules.length} rule{rules.length === 1 ? "" : "s"} evaluated · {fired.length} fired
        </span>
      </div>

      {ordered.map((r) => (
        <div key={r.rule_key} className={`pf-ptrace-rule${r.fired ? " fired" : ""}${r.indeterminate ? " indet" : ""}`}>
          <div className="pf-ptrace-rule-head">
            <span className={`pf-ptrace-flag${r.fired ? " on" : ""}`}>
              {r.indeterminate ? "INDETERMINATE" : r.fired ? "FIRED" : "checked"}
            </span>
            <code className="pf-ptrace-key">{r.rule_key}</code>
            <span className={`pf-verdict ${verdictTone(r.decision)}`}>{r.decision.toUpperCase()}</span>
            {r.restricted_fields?.length ? (
              <span className="pf-ptrace-fields">{r.restricted_fields.join(", ")}</span>
            ) : null}
            {r.approver_role && <span className="pf-ptrace-fields">→ {r.approver_role}</span>}
          </div>
          {r.reason && <div className="pf-ptrace-reason">{r.reason}</div>}
          {r.conditions?.length ? (
            <div className="pf-ptrace-cond">{r.conditions.map(condText).join(" AND ")}</div>
          ) : null}
          {/* A rule with no clause behind it is not the same as one whose
              clause we simply did not print, so the absence is stated. */}
          {r.source?.text ? (
            <blockquote className="pf-ptrace-quote">
              {r.source.text}
              <cite>
                {[r.source.document, r.source.section && `§${r.source.section}`]
                  .filter(Boolean).join(" · ")}
              </cite>
            </blockquote>
          ) : (
            <div className="pf-ptrace-nosrc">no policy clause recorded for this rule</div>
          )}
          {r.missing?.length ? (
            <div className="pf-ptrace-missing">
              could not be decided — missing {r.missing.join(", ")}; fail-safe to approval
            </div>
          ) : null}
        </div>
      ))}

      {/* eval-engine's checks, running in-band here rather than over a
          finished session. Violations stay visible; the satisfied ones are
          many and uniform, so they collapse. */}
      {checks.length > 0 && (
        <div className="pf-ptrace-checks">
          {violated.map((c, i) => (
            <div key={i} className="pf-ptrace-check bad">
              <code>{c.check_id}</code> · {c.status}{c.detail ? ` — ${c.detail}` : ""}
            </div>
          ))}
          {satisfied.length > 0 && (
            <details>
              <summary>{satisfied.length} inline check{satisfied.length === 1 ? "" : "s"} satisfied</summary>
              {satisfied.map((c, i) => (
                <div key={i} className="pf-ptrace-check" title={c.detail || ""}>
                  <code>{c.check_id}</code>{c.detail ? ` — ${c.detail}` : ""}
                </div>
              ))}
            </details>
          )}
        </div>
      )}
    </div>
  );
}

function PrefrontComparison({ run, sensitive }: { run: Run; sensitive: Set<string> }) {
  const u = run.ungoverned!;
  const g = run.governed;
  return (
    <>
    <div className="pf-diff-cols">
      <div className="pf-diff-side bad">
        <div className="pf-diff-side-head">App layer · typed functions, no policy</div>
        <div className="pf-diff-side-body">
          <span className="pf-verdict v-leak">NO POLICY</span>
          {u.tool && (
            <div className="pf-diff-reason"><span className="lbl">called</span>
              <code>{u.tool}({fmtArgs(u.args || {})})</code>
            </div>
          )}
          {u.error && <div className="pf-diff-err">ERROR {u.error}</div>}
          {u.rows?.length ? (
            <>
              <div className="pf-diff-reason"><span className="lbl">returned</span>{u.row_count} row(s)</div>
              <RowsTable rows={u.rows} columns={u.columns} sensitive={sensitive} />
            </>
          ) : null}
          {u.answer && <div className="pf-diff-reason"><span className="lbl">model</span>{u.answer}</div>}
        </div>
      </div>
      <div className="pf-diff-side good">
        <div className="pf-diff-side-head">With Prefront · governed intents</div>
        <div className="pf-diff-side-body">
          <span className={`pf-verdict ${verdictTone(g?.outcome || "")}`}>{g?.outcome || g?.status || "—"}</span>
          {g?.intent && (
            <div className="pf-diff-reason"><span className="lbl">called</span>
              <code>{g.intent}({fmtArgs((run.turns[0]?.tool_calls?.[0]?.args as any) || {})})</code>
            </div>
          )}
          {(g?.reasons || []).map((r, i) => (
            <div key={i} className="pf-diff-reason"><span className="lbl">reason</span>{r}</div>
          ))}
          {g?.approver_roles?.length ? (
            <div className="pf-diff-reason"><span className="lbl">approver</span>{g.approver_roles.join(", ")}</div>
          ) : null}
          {g?.masked_fields?.length ? (
            <div className="pf-diff-reason"><span className="lbl">masked</span>{g.masked_fields.join(", ")}</div>
          ) : null}
          {g?.rows?.length ? (
            <>
              <div className="pf-diff-reason"><span className="lbl">returned</span>{g.row_count} row(s)</div>
              {/* The SAME sensitive set highlights both sides, so a field the
                  app handed over in the clear and Prefront masked is visibly
                  the same field rather than two unrelated cells. */}
              <RowsTable rows={g.rows} columns={Object.keys(g.rows[0])} sensitive={sensitive} />
            </>
          ) : null}
          {g?.answer && <div className="pf-diff-reason"><span className="lbl">model</span>{g.answer}</div>}
        </div>
      </div>
    </div>
    {g?.governance && <PolicyTrace gov={g.governance} />}
    </>
  );
}

export default function SessionRunner({ app }: { app: AppIdentity }) {
  const [flyout, setFlyout] = useState<{ sessionId: string; scenario: Scenario } | null>(null);
  // Host from the page, port from the registry — see orchestratorFor(). The
  // registry's "localhost" is the developer's machine, not necessarily the
  // viewer's, and a wrong host makes every call fail while the page loads
  // fine: exactly the shape of "the button does nothing".
  const [server, setServer] = useState(() => orchestratorFor(app));
  const [families, setFamilies] = useState<Family[] | null>(null);
  const [results, setResults] = useState<Record<string, Run[]>>({});
  const [running, setRunning] = useState<Record<string, boolean>>({});
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [variant, setVariant] = useState("");
  const [repeat, setRepeat] = useState(0);
  const [open, setOpen] = useState<Record<string, boolean>>({});

  // Whether Verdict can DRIVE this application's orchestrator, which is not the
  // same as whether it has one. The address is always populated (the field
  // should show the known server, not look unconfigured); the capability is
  // declared separately, because SecureBank's orchestrator is real and running
  // but serves a governed-vs-ungoverned diff rather than the session catalogue
  // — /api/scenarios returns a bare list and there is no /api/run.
  //
  // Naming that state is the point: without it, selecting such an application
  // produced a bare fetch failure reading as "the server is down" rather than
  // "this server does not serve a catalogue".
  const runnable = Boolean(app.orchestratorUrl) && app.scenarioCatalogue;

  const [loadedAt, setLoadedAt] = useState("");

  async function loadCatalog() {
    if (!runnable) return;
    // Deliberately does NOT clear results here. It used to: `setResults({})`
    // ran BEFORE the fetch, so every run you had was destroyed the moment you
    // clicked — including when the reload then FAILED and the catalogue was
    // never replaced at all. Reloading a catalogue is a read; a read should
    // not be able to lose work, least of all work it turns out it did not
    // need to touch. Results are reconciled against the NEW catalogue below,
    // once there is one.
    setError(""); setLoading(true); setLoadedAt("");
    try {
      const res = await fetch(`${server}/api/scenarios`);
      const json = await res.json();
      if (json.error) throw new Error(json.error);
      if (!Array.isArray(json.families)) throw new Error("not a scenario catalogue");
      const fams = json.families as Family[];
      setFamilies(fams);

      // Keep the runs whose scenario still exists, drop the rest. Re-running
      // the whole catalogue because one scenario was renamed is exactly the
      // cost this avoids; a result for a scenario that is GONE is genuinely
      // stale and would render against nothing.
      const ids = new Set(fams.flatMap((f) => f.scenarios.map((sc) => sc.id)));
      let dropped = 0;
      setResults((prev) => {
        const kept: typeof prev = {};
        for (const [id, runs] of Object.entries(prev)) {
          if (ids.has(id)) kept[id] = runs; else dropped++;
        }
        return kept;
      });

      // Collapse every expanded row. This is the VISIBLE effect a reload has
      // always had, and it is worth keeping deliberately rather than as a side
      // effect of wiping results: the page returns to the catalogue you just
      // loaded, at the top level, which is what "reload" should look like.
      // Collapsing is not the same as DISCARDING — the runs above survive, so
      // re-expanding a row still shows its transcript.
      setOpen({});

      const n = fams.reduce((a, f) => a + f.scenarios.length, 0);
      // Say it worked, and say what it cost. Reloading an unchanged catalogue
      // leaves the page looking identical, so a successful reload was
      // indistinguishable from a dead button — which is how it gets reported.
      setLoadedAt(`${n} scenarios loaded at ${new Date().toLocaleTimeString()}`
                  + (dropped ? ` — ${dropped} stale run${dropped === 1 ? "" : "s"} dropped` : ""));
    } catch (e: any) {
      // Keep the catalogue and the results. A failed reload should leave the
      // page as it was, not blank it: `setFamilies(null)` used to wipe every
      // scenario off the screen because a fetch failed, which looks like the
      // catalogue itself disappeared.
      setError(String(e.message || e));
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
      setOpen((o) => ({ ...o, [s.id]: true }));   // keyed like runKey's fallback
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

  const sensitive = useMemo(() => new Set<string>(app.sensitiveFields), [app.sensitiveFields]);
  const all = useMemo(() => (families ?? []).flatMap((f) => f.scenarios), [families]);
  const done = Object.values(results).filter((r) => r.length && !(r[0] as any).error).length;
  const checks = useMemo(() => new Set(all.flatMap((s) => s.checks)).size, [all]);

  return (
    <main>
      {flyout && <SessionFlyout sessionId={flyout.sessionId} scenario={flyout.scenario} onClose={() => setFlyout(null)} />}
      <div className="pf-panel">
        <h2><span className="pf-step-badge">1</span>Run the sessions</h2>
        <p className="pf-hint">
          <strong>{app.label}</strong> — {app.tagline} Each row below is a <strong>session</strong>: a
          signed-in caller, one or more user turns. What a run REPORTS depends on where Prefront sits
          for this application — a decision it made in-band, or the findings the out-of-band evaluator
          raised about a session it only observed. Nothing on this page enforces or judges anything;
          it runs the catalogue and shows what came back.
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
          <button className="pf-btn" onClick={loadCatalog} disabled={loading || !runnable}>{loading ? "Loading…" : "Reload catalogue"}</button>
          <button className="pf-btn primary" onClick={() => runAll()} disabled={!families || !runnable}>Run all</button>
          {families && (
            <span className="pf-summary" style={{ margin: 0 }}>
              <span className="pf-pill">{all.length} sessions</span>
              <span className="pf-pill">{checks} checks covered</span>
              {done > 0 && <span className="pf-pill approved">{done} run</span>}
            </span>
          )}
        </div>
        {!runnable && (
          <p className="pf-hint" style={{ marginTop: 10 }}>
            <strong>{app.label}</strong>'s orchestrator (shown above) does not serve a session
            catalogue — it runs a governed-vs-ungoverned diff, so there is no <code>/api/run</code>
            for Verdict to drive. Its evidence is in-band (governed decisions), not out-of-band
            sessions. Pick an application with a catalogue, or point the field above at a
            compatible orchestrator.
          </p>
        )}
        {loadedAt && !error && (
          <p className="pf-hint" style={{ marginTop: 8 }}>{loadedAt} from <code>{server}</code></p>
        )}
        {error && (
          <p className="pf-error">
            {error}
            <span style={{ color: "var(--muted)", marginLeft: 8 }}>
              — tried <code>{server}/api/scenarios</code>; is that server reachable from this browser?
            </span>
          </p>
        )}
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
                            <button className="pf-link" style={{ marginLeft: 8 }} onClick={() => setOpen((o) => ({ ...o, [runKey(run, s)]: !o[runKey(run, s)] }))}>
                              {open[runKey(run, s)] ? "collapse" : "expand"}
                            </button>
                          </div>
                          <div className="pf-diff-side-body">
                            {/* An application that runs BOTH ways gets the
                                before/after comparison; one that only runs one
                                way gets a single verdict line, because a
                                two-column layout with an empty column states a
                                contrast that was never measured. */}
                            {run.ungoverned ? (
                              <PrefrontComparison run={run} sensitive={sensitive} />
                            ) : (
                              <>
                                <span className="pf-verdict v-leak">UNGOVERNED</span>
                                <div className="pf-diff-reason"><span className="lbl">tools</span>{run.tools_called.length ? run.tools_called.map((t, i) => <code key={i} style={{ marginRight: 6 }}>{t}</code>) : "none"}</div>
                              </>
                            )}
                            {run.error && <div className="pf-diff-err">{run.error}</div>}
                            {open[runKey(run, s)] && <Transcript run={run} sensitive={sensitive} />}
                            {/* Only when there IS an out-of-band session. An
                                in-band application has no trace to pull back,
                                and the flyout would sit on "not ingested yet"
                                forever — a wait that never ends reads as a bug. */}
                            {run.session_id
                              ? <button className="pf-btn sm" style={{ marginTop: 8 }} onClick={() => setFlyout({ sessionId: run.session_id, scenario: s })}>Inspect session ▸</button>
                              : <div className="pf-hint" style={{ marginTop: 8 }}>Governed in-band — no out-of-band trace to inspect.</div>}
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
