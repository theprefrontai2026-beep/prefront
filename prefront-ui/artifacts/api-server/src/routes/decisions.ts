import { Router } from "express";
import { db } from "../lib/db";
import {
  decisionTrace,
  decisionStat,
  decisionAgent,
  decisionPolicy,
  decisionIntent,
  type InsertDecisionTrace,
} from "@workspace/db";
import { and, desc, eq, notInArray, sql } from "drizzle-orm";

const router = Router();

// Where the live governance catalog runs, per bundled demo. Each demo ships its
// own orchestrator; the request's ?demo= (or body.demo) selects which. The bare
// ORCHESTRATOR_URL is the default (securebank) for legacy callers.
const ORCHESTRATOR_DEFAULT = process.env.ORCHESTRATOR_URL ?? "http://securebank-orchestrator:8095";
function orchestratorFor(demo: string): string {
  return process.env[`ORCHESTRATOR_URL_${demo.toUpperCase()}`] || ORCHESTRATOR_DEFAULT;
}

// The demo scope is a short slug (matches the SPA's DemoConfig.id). Everything is
// scoped by it; an absent/invalid value falls back to the original single demo.
// LoanPro is the active demo (SecureBank is profile-disabled in docker-compose).
const DEFAULT_DEMO = "loanpro";
function demoOf(v: unknown): string {
  const s = String(v ?? "").trim().toLowerCase();
  return /^[a-z0-9_-]{1,32}$/.test(s) ? s : DEFAULT_DEMO;
}

// Retention cap: keep only the newest N traces PER DEMO; older ones pruned on
// write. This is a UI-store cap, NOT a compliance retention schedule - the
// schema comment calls decision_trace "append-only", which it is only while
// the cap is off. DECISION_TRACE_CAP=0 disables pruning entirely (the store
// then grows unbounded, which is what an audit-log reading of it requires);
// see compliance_design.md §5.1 / §5.4.
const MAX_TRACES = Math.max(0, Number(process.env.DECISION_TRACE_CAP ?? 100) || 0);

/** Delete everything but the newest MAX_TRACES rows for `demo`. Runs after each insert. */
async function pruneOldTraces(demo: string): Promise<void> {
  if (!MAX_TRACES) return;
  const keep = db
    .select({ id: decisionTrace.id })
    .from(decisionTrace)
    .where(eq(decisionTrace.demo, demo))
    .orderBy(desc(decisionTrace.createdAt), desc(decisionTrace.id))
    .limit(MAX_TRACES);
  await db
    .delete(decisionTrace)
    .where(and(eq(decisionTrace.demo, demo), notInArray(decisionTrace.id, keep)));
}

/**
 * Fold a batch of just-inserted decisions into the FOREVER counters
 * (`decision_stat`) and the distinct-agent set (`decision_agent`), scoped to
 * `demo`. These are never pruned or cleared, so each demo's Agent Activity
 * totals only ever grow.
 */
async function recordStats(rows: InsertDecisionTrace[], demo: string): Promise<void> {
  if (!rows.length) return;

  const delta: Record<string, number> = {
    total: rows.length,
    allowed: 0,
    masked: 0,
    blocked: 0,
    approval: 0,
    masked_fields: 0,
  };
  for (const r of rows) {
    if (r.decision === "ALLOWED") delta.allowed++;
    else if (r.decision === "MASKED") delta.masked++;
    else if (r.decision === "BLOCKED") delta.blocked++;
    else if (r.decision === "APPROVAL") delta.approval++;
    if (Array.isArray(r.maskedFields)) delta.masked_fields += r.maskedFields.length;
  }

  // Atomic increment per (demo, metric key): INSERT … ON CONFLICT DO UPDATE count = count + n.
  const counterRows = Object.entries(delta)
    .filter(([, n]) => n > 0)
    .map(([key, count]) => ({ demo, key, count }));
  if (counterRows.length) {
    await db
      .insert(decisionStat)
      .values(counterRows)
      .onConflictDoUpdate({
        target: [decisionStat.demo, decisionStat.key],
        set: { count: sql`${decisionStat.count} + excluded.count` },
      });
  }

  // Remember every distinct caller identity within this demo (idempotent).
  const agents = [...new Set(rows.map((r) => r.caller).filter(Boolean))].map((agent) => ({ demo, agent }));
  if (agents.length) {
    await db.insert(decisionAgent).values(agents).onConflictDoNothing();
  }

  await recordPolicies(rows, demo);
  await recordIntents(rows, demo);
}

/** Fold executed intents into the FOREVER per-(demo, intent) counters + effect buckets. */
async function recordIntents(rows: InsertDecisionTrace[], demo: string): Promise<void> {
  const agg = new Map<
    string,
    { count: number; allowed: number; masked: number; blocked: number; approval: number }
  >();
  for (const r of rows) {
    if (!r.intent) continue;
    const cur = agg.get(r.intent) ?? { count: 0, allowed: 0, masked: 0, blocked: 0, approval: 0 };
    cur.count++;
    if (r.decision === "ALLOWED") cur.allowed++;
    else if (r.decision === "MASKED") cur.masked++;
    else if (r.decision === "BLOCKED") cur.blocked++;
    else if (r.decision === "APPROVAL") cur.approval++;
    agg.set(r.intent, cur);
  }
  if (!agg.size) return;

  const values = [...agg.entries()].map(([intent, v]) => ({ demo, intent, ...v }));
  await db
    .insert(decisionIntent)
    .values(values)
    .onConflictDoUpdate({
      target: [decisionIntent.demo, decisionIntent.intent],
      set: {
        count: sql`${decisionIntent.count} + excluded.count`,
        allowed: sql`${decisionIntent.allowed} + excluded.allowed`,
        masked: sql`${decisionIntent.masked} + excluded.masked`,
        blocked: sql`${decisionIntent.blocked} + excluded.blocked`,
        approval: sql`${decisionIntent.approval} + excluded.approval`,
        lastSeen: sql`now()`,
      },
    });
}

type PolicyEffect = "block" | "mask" | "approval" | "allow";

/** Map a rule's raw decision (or a row's normalized label) to a bar color. */
function toEffect(raw: string): PolicyEffect {
  const s = raw.toLowerCase();
  if (s.startsWith("block")) return "block";
  if (s.startsWith("mask")) return "mask";
  if (s.startsWith("approval")) return "approval";
  return "allow";
}

interface TriggeredPolicy {
  policy: string;
  effect: PolicyEffect;
  kind: string;
  reason: string;
}

/**
 * The distinct governance policies a single decision triggered:
 *   1. every rule in `rules_evaluated` that actually fired (authoritative),
 *   2. plus engine authz/scoping reasons formatted "<rule_key>: <text>"
 *      (e.g. role_not_permitted) that don't surface as declarative rules.
 */
function triggeredPolicies(row: InsertDecisionTrace): TriggeredPolicy[] {
  const g: any = row.governance || {};
  const out = new Map<string, TriggeredPolicy>();

  for (const r of Array.isArray(g.rules_evaluated) ? g.rules_evaluated : []) {
    if (r?.fired && r.rule_key) {
      out.set(r.rule_key, {
        policy: r.rule_key,
        effect: toEffect(String(r.decision ?? row.decision)),
        kind: String(r.rule_type ?? "policy"),
        reason: String(r.reason ?? ""),
      });
    }
  }

  const reasons: string[] = Array.isArray(g.reasons)
    ? g.reasons
    : Array.isArray(row.reasons)
      ? (row.reasons as string[])
      : [];
  for (const rs of reasons) {
    const m = /^([a-z][a-z0-9_]+):\s*(.*)$/.exec(String(rs));
    if (m && !out.has(m[1])) {
      out.set(m[1], {
        policy: m[1],
        effect: toEffect(row.decision),
        kind: "authorization",
        reason: m[2],
      });
    }
  }
  return [...out.values()];
}

/** Fold triggered policies into the FOREVER per-(demo, policy) counters. */
async function recordPolicies(rows: InsertDecisionTrace[], demo: string): Promise<void> {
  const agg = new Map<string, { count: number; effect: string; kind: string; reason: string }>();
  for (const row of rows) {
    for (const p of triggeredPolicies(row)) {
      const cur = agg.get(p.policy);
      if (cur) cur.count++;
      else agg.set(p.policy, { count: 1, effect: p.effect, kind: p.kind, reason: p.reason });
    }
  }
  if (!agg.size) return;

  const values = [...agg.entries()].map(([policy, v]) => ({
    demo,
    policy,
    count: v.count,
    effect: v.effect,
    kind: v.kind,
    reason: v.reason,
  }));
  await db
    .insert(decisionPolicy)
    .values(values)
    .onConflictDoUpdate({
      target: [decisionPolicy.demo, decisionPolicy.policy],
      set: {
        count: sql`${decisionPolicy.count} + excluded.count`,
        effect: sql`excluded.effect`,
        kind: sql`excluded.kind`,
        reason: sql`excluded.reason`,
        lastSeen: sql`now()`,
      },
    });
}

type DecisionLabel = "BLOCKED" | "APPROVAL" | "MASKED" | "ALLOWED";

/** Normalize the runtime's raw outcome/status into the dashboard's chip label. */
function normalizeDecision(g: any): DecisionLabel {
  const o = String(g?.outcome || g?.status || "").toUpperCase();
  if (o.startsWith("BLOCK")) return "BLOCKED";
  if (o.startsWith("APPROVAL") || o.includes("APPROVAL_REQUIRED")) return "APPROVAL";
  if (o.includes("MASK")) return "MASKED";
  return "ALLOWED";
}

// A session groups the decisions from one Run-all / refresh batch, so the
// Intent Flows page can reconstruct ordered per-user intent sequences.
function newSessionId(): string {
  return "sess_" + Math.random().toString(36).slice(2, 10);
}

/** Turn one `/api/diff` element into an insertable trace row, or null if unusable. */
function toInsert(d: any, sessionId: string | undefined, demo: string): InsertDecisionTrace | null {
  if (!d || d.id == null || !d.governed) return null;
  const g = d.governed;
  const row: InsertDecisionTrace = {
    demo,
    sessionId: sessionId ?? d.sessionId ?? null,
    scenarioId: String(d.id),
    caller: String(d.caller ?? ""),
    role: String(d.role ?? ""),
    capability: d.capability ?? null,
    intent: g.intent ?? null,
    args: g.args ?? null,
    decision: normalizeDecision(g),
    outcome: g.outcome ?? null,
    status: g.status ?? null,
    reasons: g.reasons ?? null,
    maskedFields: g.masked_fields ?? null,
    approverRoles: g.approver_roles ?? null,
    policy: null,
    governance: g.governance ?? null,
  };
  // Stamp the primary triggered policy so the precedent graph can group by it.
  row.policy = triggeredPolicies(row)[0]?.policy ?? null;
  return row;
}

/** GET /api/decisions?limit=50&demo=securebank — newest-first traces for the demo. */
router.get("/decisions", async (req, res) => {
  const limit = Math.min(Math.max(Number(req.query.limit) || 50, 1), 200);
  const demo = demoOf(req.query.demo);
  try {
    const rows = await db
      .select()
      .from(decisionTrace)
      .where(eq(decisionTrace.demo, demo))
      .orderBy(desc(decisionTrace.createdAt))
      .limit(limit);
    res.json({ traces: rows });
  } catch (err) {
    req.log.error({ err }, "decisions fetch failed");
    res.status(500).json({ error: "Failed to fetch decision traces" });
  }
});

/** GET /api/stats?demo= — cumulative, never-reset governance totals for the demo. */
router.get("/stats", async (req, res) => {
  const demo = demoOf(req.query.demo);
  try {
    const counters = await db.select().from(decisionStat).where(eq(decisionStat.demo, demo));
    const m = Object.fromEntries(counters.map((c) => [c.key, Number(c.count)]));
    const [{ agents }] = await db
      .select({ agents: sql<number>`count(*)::int` })
      .from(decisionAgent)
      .where(eq(decisionAgent.demo, demo));
    res.json({
      agentsActive: agents ?? 0,
      total: m.total ?? 0,
      allowed: m.allowed ?? 0,
      masked: m.masked ?? 0,
      blocked: m.blocked ?? 0,
      approval: m.approval ?? 0,
      maskedFields: m.masked_fields ?? 0,
    });
  } catch (err) {
    req.log.error({ err }, "stats fetch failed");
    res.status(500).json({ error: "Failed to fetch stats" });
  }
});

/** GET /api/policies?demo= — cumulative per-policy trigger counts, most-enforced first. */
router.get("/policies", async (req, res) => {
  const demo = demoOf(req.query.demo);
  try {
    const rows = await db
      .select()
      .from(decisionPolicy)
      .where(eq(decisionPolicy.demo, demo))
      .orderBy(desc(decisionPolicy.count), desc(decisionPolicy.lastSeen));
    res.json({ policies: rows });
  } catch (err) {
    req.log.error({ err }, "policies fetch failed");
    res.status(500).json({ error: "Failed to fetch policies" });
  }
});

/**
 * GET /api/intents?demo= — cumulative per-intent executions, busiest first, with a
 * data-driven risk level derived from what governance actually did to it:
 *   blocked & masked → Critical · blocked or approval → High · masked → Medium · else Low.
 */
router.get("/intents", async (req, res) => {
  const demo = demoOf(req.query.demo);
  try {
    const rows = await db
      .select()
      .from(decisionIntent)
      .where(eq(decisionIntent.demo, demo))
      .orderBy(desc(decisionIntent.count));
    const intents = rows.map((r) => {
      let risk: "Critical" | "High" | "Medium" | "Low";
      if (r.blocked > 0 && r.masked > 0) risk = "Critical";
      else if (r.blocked > 0 || r.approval > 0) risk = "High";
      else if (r.masked > 0) risk = "Medium";
      else risk = "Low";
      return { intent: r.intent, executions: r.count, risk };
    });
    res.json({ intents });
  } catch (err) {
    req.log.error({ err }, "intents fetch failed");
    res.status(500).json({ error: "Failed to fetch intents" });
  }
});

/** DELETE /api/decisions?demo= — wipe the persisted traces for one demo (the Clear control). */
router.delete("/decisions", async (req, res) => {
  const demo = demoOf(req.query.demo);
  try {
    await db.delete(decisionTrace).where(eq(decisionTrace.demo, demo));
    res.json({ ok: true });
  } catch (err) {
    req.log.error({ err }, "decisions clear failed");
    res.status(500).json({ error: "Failed to clear decision traces" });
  }
});

/** POST /api/decisions — persist one governed decision (a `/api/diff` element).
 *  `demo` scopes it; `sessionId` ties runs from one Run-all together, else a fresh one. */
router.post("/decisions", async (req, res) => {
  const demo = demoOf(req.body?.demo);
  const row = toInsert(req.body, req.body?.sessionId || newSessionId(), demo);
  if (!row) {
    res.status(400).json({ error: "invalid decision payload (need id + governed)" });
    return;
  }
  try {
    const [entry] = await db.insert(decisionTrace).values(row).returning();
    await recordStats([row], demo);
    await pruneOldTraces(demo);
    res.status(201).json({ trace: entry });
  } catch (err) {
    req.log.error({ err }, "decision insert failed");
    res.status(500).json({ error: "Failed to persist decision trace" });
  }
});

/**
 * POST /api/decisions/refresh — run the live catalog server-side and persist it.
 * Calls the selected demo's orchestrator `/api/diff` (LLM + Prefront pipeline),
 * stores each governed result under that demo, and returns the count. This is the
 * populate path; the dashboard itself only ever GETs. `demo` in the body selects
 * which orchestrator to run and how to scope the results.
 */
router.post("/decisions/refresh", async (req, res) => {
  const demo = demoOf(req.body?.demo);
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 115_000);
  try {
    const r = await fetch(`${orchestratorFor(demo)}/api/diff`, { signal: controller.signal });
    const diff: any = await r.json();
    if (!Array.isArray(diff)) {
      throw new Error(diff?.error || "orchestrator did not return a diff array");
    }
    const sid = newSessionId(); // the whole refreshed catalog is one session
    const rows = diff.map((d) => toInsert(d, sid, demo)).filter((x): x is InsertDecisionTrace => x !== null);
    if (rows.length) {
      await db.insert(decisionTrace).values(rows);
      await recordStats(rows, demo);
      await pruneOldTraces(demo);
    }
    res.json({ ok: true, count: rows.length });
  } catch (err) {
    req.log.error({ err }, "decisions refresh failed");
    const msg = err instanceof Error ? err.message : String(err);
    res.status(502).json({ error: `refresh failed: ${msg}` });
  } finally {
    clearTimeout(timer);
  }
});

export default router;
