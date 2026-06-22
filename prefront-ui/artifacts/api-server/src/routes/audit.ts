import { Router } from "express";
import { db } from "../lib/db";
import { ruleAuditLog, insertAuditSchema } from "@workspace/db";
import { eq, desc } from "drizzle-orm";

const router = Router();

/** GET /api/audit?documentId=xxx  — fetch audit log for a document */
router.get("/audit", async (req, res) => {
  const documentId = String(req.query.documentId ?? "");
  if (!documentId) {
    res.status(400).json({ error: "documentId query param required" });
    return;
  }
  try {
    const rows = await db
      .select()
      .from(ruleAuditLog)
      .where(eq(ruleAuditLog.documentId, documentId))
      .orderBy(desc(ruleAuditLog.createdAt))
      .limit(500);
    res.json({ entries: rows });
  } catch (err) {
    req.log.error({ err }, "audit fetch failed");
    res.status(500).json({ error: "Failed to fetch audit log" });
  }
});

/** POST /api/audit  — write one audit entry (internal use + WS hub) */
router.post("/audit", async (req, res) => {
  const parse = insertAuditSchema.safeParse(req.body);
  if (!parse.success) {
    res.status(400).json({ error: parse.error.message });
    return;
  }
  try {
    const [row] = await db.insert(ruleAuditLog).values(parse.data).returning();
    res.status(201).json({ entry: row });
  } catch (err) {
    req.log.error({ err }, "audit insert failed");
    res.status(500).json({ error: "Failed to write audit entry" });
  }
});

export default router;
