/*
 * Live Decision Trace Feed — reads PERSISTED governance decisions from the DB.
 *
 * The dashboard fetches from the api-server only (`GET /api/decisions`, proxied
 * by nginx to :8080 → Postgres `decision_trace`). It never re-runs the LLM
 * catalog on load. Traces are written elsewhere: the Runtime tab persists each
 * run, and `POST /api/decisions/refresh` runs the SecureBank catalog
 * server-side and stores every governed result. `populate()` triggers that
 * refresh and then re-reads — the only write path the dashboard exposes, kept
 * as an explicit user action.
 */

import { useCallback, useEffect, useMemo, useState } from "react";

export type FeedDecision = "BLOCKED" | "APPROVAL" | "MASKED" | "ALLOWED";

export interface FeedRow {
  id: string; // trace row id — stable React key
  time: string; // HH:MM, from the stored createdAt
  decision: FeedDecision;
  agent: string;
  intent: string;
  reason: string;
  source: string;
}

export type FeedStatus = "loading" | "ready" | "error";

// Cumulative, never-reset governance totals backing the Agent Activity panel.
export interface AgentStats {
  agentsActive: number;
  total: number;
  allowed: number;
  masked: number;
  blocked: number;
  approval: number;
  maskedFields: number;
}

// Cumulative per-policy trigger counts backing the Policies Enforced panel.
export interface PolicyStat {
  policy: string;
  count: number;
  effect: "block" | "mask" | "approval" | "allow" | null;
  kind: string | null;
  reason: string | null;
}

// Cumulative per-intent executions backing the Most Used Intents panel.
export interface IntentStat {
  intent: string;
  executions: number;
  risk: "Critical" | "High" | "Medium" | "Low";
}

// One persisted trace row as returned by GET /api/decisions.
export interface Trace {
  id: number;
  scenarioId: string;
  caller: string;
  role: string;
  capability: string | null;
  intent: string | null;
  args: Record<string, any> | null;
  decision: FeedDecision;
  outcome: string | null;
  status: string | null;
  reasons: string[] | null;
  approverRoles: string[] | null;
  policy: string | null;
  createdAt: string;
}

// Each role fronts a different agent surface in the SecureBank story; the caller
// is the human identity Prefront resolves per connection.
const ROLE_AGENT: Record<string, string> = {
  "Account Holder": "Customer Assistant",
  "Bank Teller": "Teller Copilot",
  "Bank Manager": "Manager Console",
};

function firstName(name: string): string {
  return (name.split(" ")[0] || name).toLowerCase();
}

// A compact, human-readable rendering of the governed intent + its key arg.
function intentLabel(t: Trace): string {
  const intent = t.intent || "no approved intent";
  const a = t.args || {};
  if (typeof a.amount === "number") return `${intent} $${a.amount.toLocaleString()}`;
  if (a.account_id != null) return `${intent} ${a.account_id}`;
  if (a.loan_id != null) return `${intent} ${a.loan_id}`;
  if (a.user_id != null) return `${intent} (user ${a.user_id})`;
  return intent;
}

function hhmm(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

function toFeedRow(t: Trace): FeedRow {
  return {
    id: String(t.id),
    time: hhmm(t.createdAt),
    decision: t.decision,
    agent: `${ROLE_AGENT[t.role] || "Agent"} · ${firstName(t.caller || "")}`,
    intent: intentLabel(t),
    reason: (Array.isArray(t.reasons) && t.reasons[0]) || t.outcome || t.status || "—",
    source: t.capability || "policy.yaml",
  };
}

/** Reads persisted governance traces newest-first; `populate()` seeds from the demo. */
export function useDecisionFeed(limit = 50) {
  const [traces, setTraces] = useState<Trace[]>([]);
  const [stats, setStats] = useState<AgentStats | null>(null);
  const [policies, setPolicies] = useState<PolicyStat[]>([]);
  const [intents, setIntents] = useState<IntentStat[]>([]);
  const [status, setStatus] = useState<FeedStatus>("loading");
  const [error, setError] = useState("");
  const [populating, setPopulating] = useState(false);

  const rows = useMemo(() => traces.map(toFeedRow), [traces]);

  const loadStats = useCallback(async () => {
    try {
      const [sRes, pRes, iRes] = await Promise.all([
        fetch(`/api/stats`),
        fetch(`/api/policies`),
        fetch(`/api/intents`),
      ]);
      if (sRes.ok) setStats(await sRes.json());
      if (pRes.ok) setPolicies((await pRes.json()).policies || []);
      if (iRes.ok) setIntents((await iRes.json()).intents || []);
    } catch {
      // aggregates are non-critical to the feed; leave prior values in place
    }
  }, []);

  const load = useCallback(async () => {
    setStatus("loading");
    setError("");
    try {
      const [res] = await Promise.all([fetch(`/api/decisions?limit=${limit}`), loadStats()]);
      const json = await res.json();
      if (!res.ok) throw new Error(json?.error || `${res.status} ${res.statusText}`);
      setTraces(json.traces || []);
      setStatus("ready");
    } catch (e: any) {
      setError(String(e?.message || e));
      setStatus("error");
    }
  }, [limit, loadStats]);

  const clear = useCallback(async () => {
    setError("");
    try {
      const res = await fetch(`/api/decisions`, { method: "DELETE" });
      const json = await res.json();
      if (!res.ok) throw new Error(json?.error || `${res.status} ${res.statusText}`);
      setTraces([]);
    } catch (e: any) {
      setError(String(e?.message || e));
      await load();
    }
  }, [load]);

  const populate = useCallback(async () => {
    setPopulating(true);
    setError("");
    try {
      const res = await fetch(`/api/decisions/refresh`, { method: "POST" });
      const json = await res.json();
      if (!res.ok) throw new Error(json?.error || `${res.status} ${res.statusText}`);
    } catch (e: any) {
      setError(String(e?.message || e));
    } finally {
      setPopulating(false);
      await load();
    }
  }, [load]);

  useEffect(() => {
    load();
  }, [load]);

  return { rows, traces, stats, policies, intents, status, error, reload: load, populate, populating, clear };
}
