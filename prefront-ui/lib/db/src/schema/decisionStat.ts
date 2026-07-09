import { pgTable, varchar, bigint, text, timestamp } from "drizzle-orm/pg-core";

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

/**
 * Cumulative per-policy trigger counts — persistent FOREVER (like decision_stat).
 * One row per governance rule that has ever fired (rule_key), so the Dashboard
 * can rank policies by how often they're enforced. `effect` colors the bar
 * (block | mask | approval | allow); `kind` is the rule category; `reason` is a
 * representative human explanation (latest wins).
 */
export const decisionPolicy = pgTable("decision_policy", {
  policy:   varchar("policy", { length: 128 }).primaryKey(),
  count:    bigint("count", { mode: "number" }).notNull().default(0),
  effect:   varchar("effect", { length: 16 }),
  kind:     varchar("kind", { length: 32 }),
  reason:   text("reason"),
  lastSeen: timestamp("last_seen", { withTimezone: true }).defaultNow().notNull(),
});

export type DecisionStatRow = typeof decisionStat.$inferSelect;
export type DecisionAgentRow = typeof decisionAgent.$inferSelect;
export type DecisionPolicyRow = typeof decisionPolicy.$inferSelect;
