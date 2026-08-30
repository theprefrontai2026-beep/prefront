import { pgTable, varchar, bigint, text, timestamp, primaryKey } from "drizzle-orm/pg-core";

/**
 * Cumulative governance counters — persistent FOREVER.
 *
 * The `decision_trace` feed is capped at 100 rows (pruned on write), so the
 * Dashboard's Agent Activity totals cannot be derived from it. These counters
 * are incremented on every governed decision and are NEVER pruned or reset —
 * not even when the trace feed is cleared. One row per (demo, metric key)
 * (total | allowed | masked | blocked | approval | masked_fields), so each
 * bundled demo keeps its own independent running totals.
 */
export const decisionStat = pgTable("decision_stat", {
  demo:  varchar("demo", { length: 32 }).notNull(),
  key:   varchar("key", { length: 64 }).notNull(),
  count: bigint("count", { mode: "number" }).notNull().default(0),
}, (t) => ({ pk: primaryKey({ columns: [t.demo, t.key] }) }));

/**
 * Distinct agent identities ever seen per demo — backs the "Agents Active" tile.
 * Upserted (do-nothing on conflict) per decision; count(*) scoped to the demo.
 */
export const decisionAgent = pgTable("decision_agent", {
  demo:      varchar("demo", { length: 32 }).notNull(),
  agent:     varchar("agent", { length: 128 }).notNull(),
  firstSeen: timestamp("first_seen", { withTimezone: true }).defaultNow().notNull(),
}, (t) => ({ pk: primaryKey({ columns: [t.demo, t.agent] }) }));

/**
 * Cumulative per-policy trigger counts — persistent FOREVER (like decision_stat).
 * One row per (demo, governance rule that has ever fired), so the Dashboard can
 * rank policies by how often they're enforced. `effect` colors the bar
 * (block | mask | approval | allow); `kind` is the rule category; `reason` is a
 * representative human explanation (latest wins).
 */
export const decisionPolicy = pgTable("decision_policy", {
  demo:     varchar("demo", { length: 32 }).notNull(),
  policy:   varchar("policy", { length: 128 }).notNull(),
  count:    bigint("count", { mode: "number" }).notNull().default(0),
  effect:   varchar("effect", { length: 16 }),
  kind:     varchar("kind", { length: 32 }),
  reason:   text("reason"),
  lastSeen: timestamp("last_seen", { withTimezone: true }).defaultNow().notNull(),
}, (t) => ({ pk: primaryKey({ columns: [t.demo, t.policy] }) }));

/**
 * Cumulative per-intent execution counts — persistent FOREVER. Backs the
 * "Most Used Intents" panel; the per-effect buckets let the UI derive a
 * data-driven risk level (what governance actually did to this intent). Scoped
 * per (demo, intent).
 */
export const decisionIntent = pgTable("decision_intent", {
  demo:     varchar("demo", { length: 32 }).notNull(),
  intent:   varchar("intent", { length: 128 }).notNull(),
  count:    bigint("count", { mode: "number" }).notNull().default(0),
  allowed:  bigint("allowed", { mode: "number" }).notNull().default(0),
  masked:   bigint("masked", { mode: "number" }).notNull().default(0),
  blocked:  bigint("blocked", { mode: "number" }).notNull().default(0),
  approval: bigint("approval", { mode: "number" }).notNull().default(0),
  lastSeen: timestamp("last_seen", { withTimezone: true }).defaultNow().notNull(),
}, (t) => ({ pk: primaryKey({ columns: [t.demo, t.intent] }) }));

export type DecisionStatRow = typeof decisionStat.$inferSelect;
export type DecisionAgentRow = typeof decisionAgent.$inferSelect;
export type DecisionPolicyRow = typeof decisionPolicy.$inferSelect;
export type DecisionIntentRow = typeof decisionIntent.$inferSelect;
