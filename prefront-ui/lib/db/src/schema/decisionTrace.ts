import { pgTable, serial, varchar, jsonb, timestamp } from "drizzle-orm/pg-core";
import { createInsertSchema } from "drizzle-zod";
import { z } from "zod/v4";

/**
 * Persistent governance decision traces — one row per governed decision the
 * runtime produces (the SecureBank demo orchestrator's `/api/diff`). The
 * Dashboard's Live Decision Trace Feed reads from THIS table only; it never
 * re-runs the LLM catalog. Rows are written by the api-server (POST
 * /api/decisions from the Runtime tab, or POST /api/decisions/refresh which
 * runs the catalog server-side and persists each result).
 *
 * Append-only audit log: repeated runs accumulate as distinct timestamped
 * events; the dashboard shows the most recent N.
 */
export const decisionTrace = pgTable("decision_trace", {
  id:            serial("id").primaryKey(),
  sessionId:     varchar("session_id", { length: 64 }),             // one per Run-all / refresh batch (nullable for legacy rows)
  scenarioId:    varchar("scenario_id", { length: 32 }).notNull(),  // e.g. "B4"
  caller:        varchar("caller", { length: 128 }).notNull(),      // identity the agent acted as
  role:          varchar("role", { length: 64 }).notNull(),
  capability:    varchar("capability", { length: 128 }),
  intent:        varchar("intent", { length: 128 }),                // governed intent (null if none matched)
  args:          jsonb("args"),
  decision:      varchar("decision", { length: 16 }).notNull(),     // BLOCKED | APPROVAL | MASKED | ALLOWED
  outcome:       varchar("outcome", { length: 64 }),                // raw, e.g. "BLOCK (policy)"
  status:        varchar("status", { length: 32 }),                 // raw, e.g. "blocked"
  reasons:       jsonb("reasons"),                                  // string[]
  maskedFields:  jsonb("masked_fields"),                            // string[]
  approverRoles: jsonb("approver_roles"),                           // string[]
  policy:        varchar("policy", { length: 128 }),                // primary triggered policy (for the precedent graph)
  governance:    jsonb("governance"),                               // full deterministic decision trace
  createdAt:     timestamp("created_at", { withTimezone: true }).defaultNow().notNull(),
});

export const insertDecisionTraceSchema = createInsertSchema(decisionTrace).omit({ id: true, createdAt: true });
export type InsertDecisionTrace = z.infer<typeof insertDecisionTraceSchema>;
export type DecisionTraceRow = typeof decisionTrace.$inferSelect;
