import { Router } from "express";
import { db } from "../lib/db";
import { learnedWorkflowRun } from "@workspace/db";
import { eq } from "drizzle-orm";

const router = Router();

const DEFAULT_DEMO = "loanpro";
function demoOf(v: unknown): string {
  const s = String(v ?? "").trim().toLowerCase();
  return /^[a-z0-9_-]{1,32}$/.test(s) ? s : DEFAULT_DEMO;
}

const isObj = (v: unknown): v is Record<string, unknown> =>
  !!v && typeof v === "object" && !Array.isArray(v);

/** GET /api/learned/workflows?demo=X — the saved run, or `run: null`. */
router.get("/learned/workflows", async (req, res) => {
  const demo = demoOf(req.query.demo);
  try {
    const [row] = await db.select().from(learnedWorkflowRun).where(eq(learnedWorkflowRun.demo, demo));
    res.json({ demo, run: row ?? null });
  } catch (err) {
    req.log.error({ err }, "learned workflows fetch failed");
    res.status(500).json({ error: "Failed to fetch saved workflows" });
  }
});

/** PUT /api/learned/workflows — replace a demo's saved run. The payload is
 *  checked for shape only; its contents are the page's own. */
router.put("/learned/workflows", async (req, res) => {
  const b = req.body as unknown;
  if (!isObj(b) || !isObj(b.params) || !Array.isArray(b.shapes) || !isObj(b.policies)
      || !Array.isArray(b.rejected) || !isObj(b.approvals) || !isObj(b.approvalShapes)) {
    res.status(400).json({ error: "body needs params, shapes, policies, rejected, approvals, approvalShapes" });
    return;
  }
  const demo = demoOf(b.demo);
  const minedAt = typeof b.minedAt === "string" && !Number.isNaN(Date.parse(b.minedAt))
    ? new Date(b.minedAt) : null;
  const set = {
    params: b.params, shapes: b.shapes, policies: b.policies, rejected: b.rejected,
    approvals: b.approvals, approvalShapes: b.approvalShapes, minedAt, updatedAt: new Date(),
  };
  try {
    await db.insert(learnedWorkflowRun).values({ demo, ...set })
      .onConflictDoUpdate({ target: learnedWorkflowRun.demo, set });
    res.json({ demo, updatedAt: set.updatedAt });
  } catch (err) {
    req.log.error({ err }, "learned workflows save failed");
    res.status(500).json({ error: "Failed to save workflows" });
  }
});

export default router;
