import { Router } from "express";
import { db } from "../lib/db";
import { severityRule, type SeverityRuleRow } from "@workspace/db";
import { eq, asc } from "drizzle-orm";

const router = Router();

const DEFAULT_DEMO = "loanpro";
function demoOf(v: unknown): string {
  const s = String(v ?? "").trim().toLowerCase();
  return /^[a-z0-9_-]{1,32}$/.test(s) ? s : DEFAULT_DEMO;
}

/**
 * The seeded severity mapping — the single source of default truth. Evaluated
 * first-match-wins ("effect wins": a blocking finding is Critical regardless of
 * family; Integrity is the medium fallback for its non-block/non-approval
 * findings). Keys on engine concepts only (family/effect), never domain nouns.
 * `null` matches any. A customer can reorder/edit these via PUT.
 */
type SeverityLevel = "critical" | "high" | "medium" | "low";
interface SeverityRule {
  family: string | null;   // family1 | family2 | family3 | null(any)
  effect: string | null;   // block | approval_required | flag | allow | null(any)
  severity: SeverityLevel;
}
const DEFAULT_SEVERITY_RULES: SeverityRule[] = [
  { family: null, effect: "block", severity: "critical" },
  { family: null, effect: "approval_required", severity: "high" },
  { family: "family2", effect: null, severity: "medium" },
  { family: null, effect: "flag", severity: "low" },
  { family: null, effect: null, severity: "low" },
];

const FAMILIES = new Set(["family1", "family2", "family3"]);
const EFFECTS = new Set(["block", "approval_required", "flag", "allow"]);
const SEVERITIES = new Set<SeverityLevel>(["critical", "high", "medium", "low"]);

/** Validate the PUT payload by hand (api-server carries no direct zod dep). */
function parseRules(body: unknown): { ok: true; rules: SeverityRule[] } | { ok: false; error: string } {
  if (!body || typeof body !== "object") return { ok: false, error: "body must be an object" };
  const raw = (body as { rules?: unknown }).rules;
  if (!Array.isArray(raw) || raw.length < 1 || raw.length > 50) {
    return { ok: false, error: "rules must be an array of 1..50 items" };
  }
  const rules: SeverityRule[] = [];
  for (const [i, r] of raw.entries()) {
    if (!r || typeof r !== "object") return { ok: false, error: `rule ${i} is not an object` };
    const { family, effect, severity } = r as Record<string, unknown>;
    if (family != null && !(typeof family === "string" && FAMILIES.has(family))) {
      return { ok: false, error: `rule ${i}: invalid family` };
    }
    if (effect != null && !(typeof effect === "string" && EFFECTS.has(effect))) {
      return { ok: false, error: `rule ${i}: invalid effect` };
    }
    if (typeof severity !== "string" || !SEVERITIES.has(severity as SeverityLevel)) {
      return { ok: false, error: `rule ${i}: invalid severity` };
    }
    rules.push({
      family: (family as string) ?? null,
      effect: (effect as string) ?? null,
      severity: severity as SeverityLevel,
    });
  }
  return { ok: true, rules };
}

function toRule(r: Pick<SeverityRuleRow, "family" | "effect" | "severity">): SeverityRule {
  return {
    family: r.family ?? null,
    effect: r.effect ?? null,
    severity: (r.severity as SeverityLevel),
  };
}

/** GET /api/settings/severity?demo=X — the demo's effective ordered rules
 *  (stored rows, or the built-in defaults when none are stored). */
router.get("/settings/severity", async (req, res) => {
  const demo = demoOf(req.query.demo);
  try {
    const rows = await db
      .select()
      .from(severityRule)
      .where(eq(severityRule.demo, demo))
      .orderBy(asc(severityRule.ordinal));
    const rules = rows.length ? rows.map(toRule) : DEFAULT_SEVERITY_RULES;
    res.json({ demo, rules, isDefault: rows.length === 0 });
  } catch (err) {
    req.log.error({ err }, "severity rules fetch failed");
    res.status(500).json({ error: "Failed to fetch severity rules" });
  }
});

/** PUT /api/settings/severity — replace a demo's whole ordered rule-list. */
router.put("/settings/severity", async (req, res) => {
  const parse = parseRules(req.body);
  if (!parse.ok) {
    res.status(400).json({ error: parse.error });
    return;
  }
  const demo = demoOf((req.body as { demo?: unknown })?.demo);
  const rows = parse.rules.map((r, i) => ({ demo, ordinal: i, ...r }));
  try {
    await db.transaction(async (tx) => {
      await tx.delete(severityRule).where(eq(severityRule.demo, demo));
      await tx.insert(severityRule).values(rows);
    });
    res.json({ demo, rules: parse.rules, isDefault: false });
  } catch (err) {
    req.log.error({ err }, "severity rules save failed");
    res.status(500).json({ error: "Failed to save severity rules" });
  }
});

/** DELETE /api/settings/severity?demo=X — reset to defaults (drop stored rows). */
router.delete("/settings/severity", async (req, res) => {
  const demo = demoOf(req.query.demo);
  try {
    await db.delete(severityRule).where(eq(severityRule.demo, demo));
    res.json({ demo, rules: DEFAULT_SEVERITY_RULES, isDefault: true });
  } catch (err) {
    req.log.error({ err }, "severity rules reset failed");
    res.status(500).json({ error: "Failed to reset severity rules" });
  }
});

export default router;
