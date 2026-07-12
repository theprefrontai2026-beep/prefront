/*
 * Decision Traces — the full, filterable governance decision log.
 *
 * Reads persisted traces from the DB (GET /api/decisions) and lets you slice
 * them every way the runtime records: decision, caller, role, intent, the
 * policy that fired, and free text. This is the "learning" surface — every
 * governed decision accumulates here as precedent, and filtering reveals the
 * patterns: what a given role keeps getting blocked on, everything one policy
 * has ever governed, how a single intent resolves across callers.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import type { FeedDecision, Trace } from "../hooks/useDecisionFeed";

const DECISIONS: FeedDecision[] = ["ALLOWED", "MASKED", "APPROVAL", "BLOCKED"];

function chipTone(d: FeedDecision): string {
  return d === "BLOCKED" ? "red" : d === "APPROVAL" ? "amber" : d === "MASKED" ? "teal" : "green";
}

const ROLE_AGENT: Record<string, string> = {
  "Account Holder": "Customer Assistant",
  "Bank Teller": "Teller Copilot",
  "Bank Manager": "Manager Console",
};

function fmtWhen(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString(undefined, {
    month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit",
  });
}

function argSummary(args: Record<string, any> | null): string {
  if (!args) return "";
  const parts = Object.entries(args).map(([k, v]) => `${k}=${v}`);
  return parts.length ? `(${parts.join(", ")})` : "";
}

function uniqueSorted(vals: (string | null | undefined)[]): string[] {
  return Array.from(new Set(vals.filter((v): v is string => !!v))).sort();
}

function Select({ label, value, options, onChange }: {
  label: string; value: string; options: string[]; onChange: (v: string) => void;
}) {
  return (
    <label className="pf-tr-select">
      <span>{label}</span>
      <select value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">All</option>
        {options.map((o) => <option key={o} value={o}>{o}</option>)}
      </select>
    </label>
  );
}

export default function DecisionTraces({ active = true }: { active?: boolean }) {
  const [traces, setTraces] = useState<Trace[]>([]);
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");
  const [error, setError] = useState("");

  // ── Filters ──
  const [picked, setPicked] = useState<Set<FeedDecision>>(new Set());
  const [role, setRole] = useState("");
  const [caller, setCaller] = useState("");
  const [intent, setIntent] = useState("");
  const [policy, setPolicy] = useState("");
  const [q, setQ] = useState("");

  const load = useCallback(async () => {
    setStatus("loading");
    setError("");
    try {
      const res = await fetch(`/api/decisions?limit=30`);
      const json = await res.json();
      if (!res.ok) throw new Error(json?.error || `${res.status} ${res.statusText}`);
      setTraces(json.traces || []);
      setStatus("ready");
    } catch (e: any) {
      setError(String(e?.message || e));
      setStatus("error");
    }
  }, []);

  useEffect(() => { load(); }, [load]);
  // Refetch when the tab becomes visible again — newly-run scenarios show up.
  useEffect(() => { if (active) load(); }, [active, load]);

  const roles = useMemo(() => uniqueSorted(traces.map((t) => t.role)), [traces]);
  const callers = useMemo(() => uniqueSorted(traces.map((t) => t.caller)), [traces]);
  const intents = useMemo(() => uniqueSorted(traces.map((t) => t.intent)), [traces]);
  const policies = useMemo(() => uniqueSorted(traces.map((t) => t.policy)), [traces]);

  const filtered = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return traces.filter((t) => {
      if (picked.size && !picked.has(t.decision)) return false;
      if (role && t.role !== role) return false;
      if (caller && t.caller !== caller) return false;
      if (intent && t.intent !== intent) return false;
      if (policy && t.policy !== policy) return false;
      if (needle) {
        const hay = [
          t.caller, t.role, t.intent, t.policy, t.capability, t.outcome,
          ...(t.reasons || []), argSummary(t.args),
        ].join(" ").toLowerCase();
        if (!hay.includes(needle)) return false;
      }
      return true;
    });
  }, [traces, picked, role, caller, intent, policy, q]);

  // Live per-decision distribution of the *filtered* set — the pattern the
  // current slice reveals.
  const tally = useMemo(() => {
    const t: Record<FeedDecision, number> = { ALLOWED: 0, MASKED: 0, APPROVAL: 0, BLOCKED: 0 };
    for (const row of filtered) t[row.decision] = (t[row.decision] ?? 0) + 1;
    return t;
  }, [filtered]);

  const toggleDecision = (d: FeedDecision) =>
    setPicked((prev) => {
      const next = new Set(prev);
      next.has(d) ? next.delete(d) : next.add(d);
      return next;
    });

  const activeFilters = picked.size + (role ? 1 : 0) + (caller ? 1 : 0) + (intent ? 1 : 0) + (policy ? 1 : 0) + (q.trim() ? 1 : 0);
  const clearAll = () => { setPicked(new Set()); setRole(""); setCaller(""); setIntent(""); setPolicy(""); setQ(""); };

  return (
    <main className="pf-tr">
      <section className="pf-panel">
        <div className="pf-dash-panel-head">
          <h2>Decision Trace Log</h2>
          <button className="pf-dash-link" type="button" onClick={load} disabled={status === "loading"}>
            {status === "loading" ? "Loading…" : "Refresh ↻"}
          </button>
        </div>
        <p className="pf-hint" style={{ marginTop: 0 }}>
          Every governed decision the runtime makes is recorded here as precedent. Filter the log to
          see the patterns — what a role keeps getting blocked on, everything a policy has governed,
          how one intent resolves across callers.
        </p>

        {/* ── Filter bar ── */}
        <div className="pf-tr-filters">
          <div className="pf-tr-chips">
            {DECISIONS.map((d) => (
              <button
                key={d}
                type="button"
                className={`pf-tr-chip ${chipTone(d)} ${picked.has(d) ? "on" : ""}`}
                onClick={() => toggleDecision(d)}
                aria-pressed={picked.has(d)}
              >
                {d}
              </button>
            ))}
          </div>
          <div className="pf-tr-selects">
            <Select label="Role" value={role} options={roles} onChange={setRole} />
            <Select label="Caller" value={caller} options={callers} onChange={setCaller} />
            <Select label="Intent" value={intent} options={intents} onChange={setIntent} />
            <Select label="Policy" value={policy} options={policies} onChange={setPolicy} />
          </div>
          <input
            className="pf-tr-search"
            placeholder="Search reason, args, capability…"
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />
        </div>

        {/* ── Result summary + live distribution ── */}
        <div className="pf-tr-summary">
          <span className="pf-tr-count">
            {filtered.length}<span className="muted"> of {traces.length}</span> decisions
          </span>
          <span className="pf-tr-tally">
            {DECISIONS.map((d) => (
              <span key={d} className={`pf-dash-chip ${chipTone(d)}`} style={{ opacity: tally[d] ? 1 : 0.35 }}>
                {tally[d]} {d.toLowerCase()}
              </span>
            ))}
          </span>
          {activeFilters > 0 && (
            <button className="pf-dash-link" type="button" onClick={clearAll}>
              Clear filters ✕ ({activeFilters})
            </button>
          )}
        </div>
      </section>

      <section className="pf-panel" style={{ marginTop: 14 }}>
        {status === "error" && (
          <div className="pf-dash-feed-status error">Couldn’t load traces ({error}).</div>
        )}
        {status !== "error" && filtered.length === 0 && (
          <div className="pf-dash-feed-status">
            {traces.length === 0 ? "No decisions recorded yet — run scenarios in the Runtime tab." : "No decisions match these filters."}
          </div>
        )}
        {filtered.length > 0 && (
          <table className="pf-dash-table pf-tr-table">
            <thead>
              <tr>
                <th>When</th><th>Decision</th><th>Agent · Caller</th><th>Intent</th><th>Policy</th><th>Reason</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((t) => (
                <tr key={t.id}>
                  <td className="pf-tr-when">{fmtWhen(t.createdAt)}</td>
                  <td><span className={`pf-dash-chip ${chipTone(t.decision)}`}>{t.decision}</span></td>
                  <td>
                    <div className="pf-tr-agent">{ROLE_AGENT[t.role] || "Agent"}</div>
                    <div className="muted">{t.caller} · {t.role}</div>
                  </td>
                  <td>
                    <code className="pf-tr-intent">{t.intent || "—"}</code>
                    <span className="muted pf-tr-args">{argSummary(t.args)}</span>
                  </td>
                  <td>{t.policy ? <code className="pf-tr-policy">{t.policy}</code> : <span className="muted">—</span>}</td>
                  <td className="pf-tr-reason">{(t.reasons && t.reasons[0]) || t.outcome || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </main>
  );
}
