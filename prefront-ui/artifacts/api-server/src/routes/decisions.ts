import { Router } from "express";
import { db } from "../lib/db";
import { decisionTrace, decisionStat, decisionAgent, type InsertDecisionTrace } from "@workspace/db";
import { desc, notInArray, sql } from "drizzle-orm";

const router = Router();

// Where the live governance catalog runs (the SecureBank demo orchestrator).
// Same default hostname as the compose service; override via env.
const ORCHESTRATOR_URL = process.env.ORCHESTRATOR_URL ?? "http://securebank-orchestrator:8095";

// Retention cap: keep only the newest N traces; older ones are pruned on write.
const MAX_TRACES = 100;

/** Delete everything but the newest MAX_TRACES rows. Runs after each insert. */
async function pruneOldTraces(): Promise<void> {
  const keep = db
    .select({ id: decisionTrace.id })
    .from(decisionTrace)
    .orderBy(desc(decisionTrace.createdAt), desc(decisionTrace.id))
    .limit(MAX_TRACES);
  await db.delete(decisionTrace).where(notInArray(decisionTrace.id, keep));
}

/**
 * Fold a batch of just-inserted decisions into the FOREVER counters
 * (`decision_stat`) and the distinct-agent set (`decision_agent`). These are
 * never pruned or cleared, so the Agent Activity totals only ever grow.
 */
async function recordStats(rows: InsertDecisionTrace[]): Promise<void> {
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

  // Atomic increment per metric key: INSERT … ON CONFLICT DO UPDATE count = count + n.
  const counterRows = Object.entries(delta)
    .filter(([, n]) => n > 0)
    .map(([key, count]) => ({ key, count }));
  if (counterRows.length) {
    await db
      .insert(decisionStat)
      .values(counterRows)
      .onConflictDoUpdate({
        target: decisionStat.key,
        set: { count: sql`${decisionStat.count} + excluded.count` },
      });
  }

  // Remember every distinct caller identity (idempotent).
  const agents = [...new Set(rows.map((r) => r.caller).filter(Boolean))].map((agent) => ({ agent }));
  if (agents.length) {
    await db.insert(decisionAgent).values(agents).onConflictDoNothing();
  }
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

/** Turn one `/api/diff` element into an insertable trace row, or null if unusable. */
function toInsert(d: any): InsertDecisionTrace | null {
  if (!d || d.id == null || !d.governed) return null;
  const g = d.governed;
  return {
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
    governance: g.governance ?? null,
  };
}

/** GET /api/decisions?limit=50 — newest-first governance traces for the dashboard. */
router.get("/decisions", async (req, res) => {
  const limit = Math.min(Math.max(Number(req.query.limit) || 50, 1), 200);
  try {
    const rows = await db
      .select()
      .from(decisionTrace)
      .orderBy(desc(decisionTrace.createdAt))
      .limit(limit);
    res.json({ traces: rows });
  } catch (err) {
    req.log.error({ err }, "decisions fetch failed");
    res.status(500).json({ error: "Failed to fetch decision traces" });
  }
});

/** GET /api/stats — cumulative, never-reset governance totals (Agent Activity). */
router.get("/stats", async (req, res) => {
  try {
    const counters = await db.select().from(decisionStat);
    const m = Object.fromEntries(counters.map((c) => [c.key, Number(c.count)]));
    const [{ agents }] = await db
      .select({ agents: sql<number>`count(*)::int` })
      .from(decisionAgent);
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

/** DELETE /api/decisions — wipe every persisted trace (the dashboard Clear control). */
router.delete("/decisions", async (req, res) => {
  try {
    await db.delete(decisionTrace);
    res.json({ ok: true });
  } catch (err) {
    req.log.error({ err }, "decisions clear failed");
    res.status(500).json({ error: "Failed to clear decision traces" });
  }
});

/** POST /api/decisions — persist one governed decision (a `/api/diff` element). */
router.post("/decisions", async (req, res) => {
  const row = toInsert(req.body);
  if (!row) {
    res.status(400).json({ error: "invalid decision payload (need id + governed)" });
    return;
  }
  try {
    const [entry] = await db.insert(decisionTrace).values(row).returning();
    await recordStats([row]);
    await pruneOldTraces();
    res.status(201).json({ trace: entry });
  } catch (err) {
    req.log.error({ err }, "decision insert failed");
    res.status(500).json({ error: "Failed to persist decision trace" });
  }
});

/**
 * POST /api/decisions/refresh — run the live catalog server-side and persist it.
 * Calls the orchestrator's `/api/diff` (LLM + Prefront pipeline), stores each
 * governed result, and returns the count. This is the populate path; the
 * dashboard itself only ever GETs.
 */
router.post("/decisions/refresh", async (_req, res) => {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 115_000);
  try {
    const r = await fetch(`${ORCHESTRATOR_URL}/api/diff`, { signal: controller.signal });
    const diff: any = await r.json();
    if (!Array.isArray(diff)) {
      throw new Error(diff?.error || "orchestrator did not return a diff array");
    }
    const rows = diff.map(toInsert).filter((x): x is InsertDecisionTrace => x !== null);
    if (rows.length) {
      await db.insert(decisionTrace).values(rows);
      await recordStats(rows);
      await pruneOldTraces();
    }
    res.json({ ok: true, count: rows.length });
  } catch (err) {
    _req.log.error({ err }, "decisions refresh failed");
    const msg = err instanceof Error ? err.message : String(err);
    res.status(502).json({ error: `refresh failed: ${msg}` });
  } finally {
    clearTimeout(timer);
  }
});

export default router;
