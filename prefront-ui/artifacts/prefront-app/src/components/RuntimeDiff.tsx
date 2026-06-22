import { useEffect, useMemo, useState } from "react";

const DEFAULT_SERVER = "http://localhost:8095";

function verdictClass(outcome = "") {
  const o = outcome.toUpperCase();
  if (o.startsWith("BLOCK")) return "v-block";
  if (o.startsWith("APPROVAL")) return "v-appr";
  if (o.includes("MASK")) return "v-mask";
  return "v-allow";
}

function RowsTable({ rows, columns, sensitive, maskedFields = [] }: any) {
  if (!rows || !rows.length) return null;
  const cols = columns && columns.length ? columns : Object.keys(rows[0]);
  const masked = new Set(maskedFields);
  return (
    <table className="pf-diff-rows">
      <thead><tr>{cols.map((c: string) => <th key={c}>{c}</th>)}</tr></thead>
      <tbody>
        {rows.slice(0, 5).map((r: any, i: number) => (
          <tr key={i}>
            {cols.map((c: string) => {
              const v = r[c] === null || r[c] === undefined ? "" : String(r[c]);
              const cls = masked.has(c) && v === "***" ? "masked"
                : sensitive.has(c) ? "sensitive" : "";
              return <td key={c} className={cls}>{v}</td>;
            })}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function Diff({ d, sensitive }: { d: any; sensitive: Set<string> }) {
  const u = d.ungoverned || {};
  const g = d.governed || {};
  const hasRows = u.rows && u.rows.length;
  return (
    <div className="pf-diff-cols" style={{ marginTop: 10 }}>
      <div className="pf-diff-side bad">
        <div className="pf-diff-side-head">Without Prefront · LLM + raw SQL</div>
        <div className="pf-diff-side-body">
          <span className="pf-verdict v-leak">UNGOVERNED</span>
          {u.sql && <pre className="pf-sql" style={{ fontSize: 11 }}>{u.sql}</pre>}
          {u.error && <div className="pf-diff-err">ERROR {u.error}</div>}
          {hasRows && (
            <>
              <div className="pf-diff-reason"><span className="lbl">returned</span>{u.row_count} row(s)</div>
              <RowsTable rows={u.rows} columns={u.columns} sensitive={sensitive} />
            </>
          )}
          {u.answer && <div className="pf-diff-reason"><span className="lbl">model</span>{u.answer}</div>}
        </div>
      </div>
      <div className="pf-diff-side good">
        <div className="pf-diff-side-head">With Prefront · governed intents</div>
        <div className="pf-diff-side-body">
          <span className={`pf-verdict ${verdictClass(g.outcome)}`}>{g.outcome || g.status || "—"}</span>
          {g.intent && (
            <div className="pf-diff-reason"><span className="lbl">called</span>
              <code>{g.intent}({Object.entries(g.args || {}).map(([k, v]) => `${k}=${v}`).join(", ")})</code>
            </div>
          )}
          {(g.reasons || []).map((r: string, i: number) => (
            <div key={i} className="pf-diff-reason"><span className="lbl">reason</span>{r}</div>
          ))}
          {g.approver_roles?.length > 0 && (
            <div className="pf-diff-reason"><span className="lbl">approver</span>{g.approver_roles.join(", ")}</div>
          )}
          {g.masked_fields?.length > 0 && (
            <div className="pf-diff-reason"><span className="lbl">masked</span>{g.masked_fields.join(", ")}</div>
          )}
          {g.status === "allowed" && g.rows?.length > 0 && (
            <RowsTable rows={g.rows} columns={Object.keys(g.rows[0])} sensitive={sensitive}
              maskedFields={g.masked_fields || []} />
          )}
          {g.status === "allowed" && g.row_count === 0 && (
            <div className="pf-diff-reason">0 rows — nothing in the caller's scope</div>
          )}
        </div>
      </div>
    </div>
  );
}

export default function RuntimeDiff() {
  const [server, setServer] = useState(DEFAULT_SERVER);
  const [scenarios, setScenarios] = useState<any[] | null>(null);
  const [results, setResults] = useState<Record<string, any>>({});
  const [running, setRunning] = useState<Record<string, boolean>>({});
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function loadCatalog() {
    setError(""); setLoading(true); setResults({});
    try {
      const res = await fetch(`${server}/api/scenarios`);
      const json = await res.json();
      if (json.error) throw new Error(json.error);
      if (!Array.isArray(json)) throw new Error("not a scenario list");
      setScenarios(json);
    } catch (e: any) {
      setError(String(e.message || e)); setScenarios(null);
    } finally {
      setLoading(false);
    }
  }

  async function runOne(id: string) {
    setRunning((r) => ({ ...r, [id]: true }));
    try {
      const res = await fetch(`${server}/api/diff?only=${encodeURIComponent(id)}`);
      const json = await res.json();
      if (json.error) throw new Error(json.error);
      if (json[0]) setResults((m) => ({ ...m, [id]: json[0] }));
    } catch (e: any) {
      setResults((m) => ({ ...m, [id]: { _error: String(e.message || e) } }));
    } finally {
      setRunning((r) => ({ ...r, [id]: false }));
    }
  }

  async function runAll() {
    if (scenarios) await Promise.all(scenarios.map((s) => runOne(s.id)));
  }

  useEffect(() => { loadCatalog(); }, []); // eslint-disable-line

  const sensitive = useMemo(() => {
    const s = new Set<string>();
    for (const d of Object.values(results)) for (const f of (d as any).governed?.masked_fields || []) s.add(f as string);
    return s;
  }, [results]);

  const tally = useMemo(() => {
    const t = { run: 0, block: 0, appr: 0, mask: 0, allow: 0 };
    for (const d of Object.values(results) as any[]) {
      if (!d.governed) continue;
      t.run++;
      const o = (d.governed.outcome || "").toUpperCase();
      if (o.startsWith("BLOCK")) t.block++;
      else if (o.startsWith("APPROVAL")) t.appr++;
      else if (o.includes("MASK")) t.mask++;
      else t.allow++;
    }
    return t;
  }, [results]);

  return (
    <main>
      <div className="pf-panel">
        <h2>
          <span className="pf-step-badge">1</span>
          Run the test cases
        </h2>
        <p className="pf-hint">
          Each row is one request. Click <strong>Run</strong> to evaluate it two ways — an ungoverned
          agent wired straight at the database (raw SQL, no policy) versus the same request through the
          Prefront runtime (identity injected, policy enforced).
        </p>

        <div className="pf-fields">
          <label style={{ gridColumn: "1 / -1" }}>
            Demo server URL
            <input value={server} onChange={(e) => setServer(e.target.value)} />
          </label>
        </div>

        <div className="pf-publish-row">
          <button className="pf-btn" onClick={loadCatalog} disabled={loading}>
            {loading ? "Loading…" : "Reload test cases"}
          </button>
          <button className="pf-btn primary" onClick={runAll} disabled={!scenarios}>
            Run all
          </button>
          {tally.run > 0 && (
            <span className="pf-summary" style={{ margin: 0 }}>
              <span className="pf-pill">{tally.run} run</span>
              <span className="pf-pill rejected">{tally.block} blocked</span>
              <span className="pf-pill pending">{tally.appr} approval</span>
              <span className="pf-pill">{tally.mask} masked</span>
              <span className="pf-pill approved">{tally.allow} allowed</span>
            </span>
          )}
        </div>

        {error && (
          <p className="pf-error">
            {error}
            <span style={{ color: "var(--muted)", marginLeft: 8 }}>
              — is the demo server running?
            </span>
          </p>
        )}
      </div>

      {scenarios && (
        <div className="pf-panel">
          {scenarios.map((s) => {
            const r = results[s.id];
            const busy = running[s.id];
            const outcome = r?.governed?.outcome;
            return (
              <div key={s.id} className="pf-diff-scn">
                <div className="pf-diff-scn-head">
                  <span className="pf-diff-id">{s.id}</span>
                  <span className="pf-diff-cap">{s.capability}</span>
                  <span className="pf-diff-caller">{s.caller} · {s.role}</span>
                  <span style={{ marginLeft: "auto", display: "flex", gap: 8, alignItems: "center" }}>
                    {outcome && <span className={`pf-verdict ${verdictClass(outcome)}`} style={{ margin: 0 }}>{outcome}</span>}
                    <button className="pf-btn sm" onClick={() => runOne(s.id)} disabled={busy}>
                      {busy ? "Running…" : r ? "Re-run" : "Run ▶"}
                    </button>
                  </span>
                </div>
                <div className="pf-diff-q">{s.question}</div>
                {r?._error && <p className="pf-error">{r._error}</p>}
                {r && !r._error && <Diff d={r} sensitive={sensitive} />}
                {r && !r._error && <div className="pf-diff-expected">expected: {s.expected}</div>}
              </div>
            );
          })}
        </div>
      )}
    </main>
  );
}
