import { pgTable, varchar, bigint, timestamp } from "drizzle-orm/pg-core";

/**
 * Cumulative governance counters — persistent FOREVER.
 *
 * The `decision_trace` feed is capped at 100 rows (pruned on write), so the
 * Dashboard's Agent Activity totals cannot be derived from it. These counters
 * are incremented on every governed decision and are NEVER pruned or reset —
 * not even when the trace feed is cleared. One row per metric key
 * (total | allowed | masked | blocked | approval | masked_fields).
 */
export const decisionStat = pgTable("decision_stat", {
  key:   varchar("key", { length: 64 }).primaryKey(),
  count: bigint("count", { mode: "number" }).notNull().default(0),
});

/**
 * Distinct agent identities ever seen — backs the "Agents Active" tile.
 * Upserted (do-nothing on conflict) per decision; count(*) is the metric.
 */
export const decisionAgent = pgTable("decision_agent", {
  agent:     varchar("agent", { length: 128 }).primaryKey(),
  firstSeen: timestamp("first_seen", { withTimezone: true }).defaultNow().notNull(),
});

export type DecisionStatRow = typeof decisionStat.$inferSelect;
export type DecisionAgentRow = typeof decisionAgent.$inferSelect;
