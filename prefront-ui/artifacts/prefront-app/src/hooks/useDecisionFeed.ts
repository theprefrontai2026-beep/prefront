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

import { useCallback, useEffect, useState } from "react";

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

// One persisted trace row as returned by GET /api/decisions.
interface DecisionTrace {
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
function intentLabel(t: DecisionTrace): string {
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

function toFeedRow(t: DecisionTrace): FeedRow {
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
  const [rows, setRows] = useState<FeedRow[]>([]);
  const [status, setStatus] = useState<FeedStatus>("loading");
  const [error, setError] = useState("");
  const [populating, setPopulating] = useState(false);

  const load = useCallback(async () => {
    setStatus("loading");
    setError("");
    try {
      const res = await fetch(`/api/decisions?limit=${limit}`);
      const json = await res.json();
      if (!res.ok) throw new Error(json?.error || `${res.status} ${res.statusText}`);
      setRows((json.traces || []).map(toFeedRow));
      setStatus("ready");
    } catch (e: any) {
      setError(String(e?.message || e));
      setStatus("error");
    }
  }, [limit]);

  const clear = useCallback(async () => {
    setError("");
    try {
      const res = await fetch(`/api/decisions`, { method: "DELETE" });
      const json = await res.json();
      if (!res.ok) throw new Error(json?.error || `${res.status} ${res.statusText}`);
      setRows([]);
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

  return { rows, status, error, reload: load, populate, populating, clear };
}
