/*
 * Live Decision Trace Feed — pulls real governed decisions from the SecureBank
 * demo orchestrator (the same service the Runtime tab points at, default
 * :8095). `GET /api/scenarios` lists the B1–B9 catalog; `GET /api/diff?only=<id>`
 * runs one scenario LIVE through the Prefront pipeline and returns its decision.
 * We run them one at a time and map each governed result into a feed row, so the
 * dashboard fills in like a live stream instead of a single blocking batch.
 */

import { useCallback, useEffect, useState } from "react";

export const DEMO_SERVER = "http://localhost:8095";

export type FeedDecision = "BLOCKED" | "APPROVAL" | "MASKED" | "ALLOWED";

export interface FeedRow {
  id: string; // scenario id — stable React key
  time: string; // HH:MM, stamped when the decision arrives
  decision: FeedDecision;
  agent: string;
  intent: string;
  reason: string;
  source: string;
}

export type FeedStatus = "loading" | "ready" | "error";

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

function decisionOf(g: any): FeedDecision {
  const o = String(g?.outcome || g?.status || "").toUpperCase();
  if (o.startsWith("BLOCK")) return "BLOCKED";
  if (o.startsWith("APPROVAL") || o.includes("APPROVAL_REQUIRED")) return "APPROVAL";
  if (o.includes("MASK")) return "MASKED";
  return "ALLOWED";
}

// A compact, human-readable rendering of the governed intent + its key arg.
function intentLabel(g: any): string {
  const intent = g?.intent || "no approved intent";
  const a = g?.args || {};
  if (typeof a.amount === "number") return `${intent} $${a.amount.toLocaleString()}`;
  if (a.account_id != null) return `${intent} ${a.account_id}`;
  if (a.loan_id != null) return `${intent} ${a.loan_id}`;
  if (a.user_id != null) return `${intent} (user ${a.user_id})`;
  return intent;
}

function nowHHMM(): string {
  const d = new Date();
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

function toFeedRow(d: any): FeedRow | null {
  const g = d?.governed;
  if (!g) return null;
  return {
    id: d.id,
    time: nowHHMM(),
    decision: decisionOf(g),
    agent: `${ROLE_AGENT[d.role] || "Agent"} · ${firstName(d.caller || "")}`,
    intent: intentLabel(g),
    reason: (Array.isArray(g.reasons) && g.reasons[0]) || g.outcome || g.status || "—",
    source: d.capability || "policy.yaml",
  };
}

/** Runs the demo catalog live, one scenario at a time, newest decision first. */
export function useDecisionFeed(server: string = DEMO_SERVER) {
  const [rows, setRows] = useState<FeedRow[]>([]);
  const [status, setStatus] = useState<FeedStatus>("loading");
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setStatus("loading");
    setError("");
    setRows([]);
    try {
      const res = await fetch(`${server}/api/scenarios`);
      const scns = await res.json();
      if (!Array.isArray(scns)) throw new Error(scns?.error || "unexpected scenarios response");
      for (const s of scns) {
        try {
          const r = await fetch(`${server}/api/diff?only=${encodeURIComponent(s.id)}`);
          const j = await r.json();
          const d = Array.isArray(j) ? j[0] : null;
          const row = toFeedRow(d);
          if (row) setRows((prev) => [row, ...prev]);
        } catch {
          // one scenario failing (LLM/transport flake) shouldn't sink the feed
        }
      }
      setStatus("ready");
    } catch (e: any) {
      setError(String(e?.message || e));
      setStatus("error");
    }
  }, [server]);

  useEffect(() => {
    load();
  }, [load]);

  return { rows, status, error, reload: load };
}
