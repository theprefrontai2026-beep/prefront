import { pgTable, varchar, jsonb, timestamp } from "drizzle-orm/pg-core";

/**
 * The Learned Intents page's last mining result and the review in progress on
 * it — one row per demo, replaced wholesale on every save.
 *
 * Stored rather than recomputed because two of its parts cannot be: the
 * model's readings cost a metered call per workflow, and the approvals are a
 * reviewer's work. The shapes could be re-fetched, but the readings and
 * approvals are keyed by them, so they are kept together as the run they came
 * from — a reading beside a re-counted shape would cite counts it never saw.
 *
 * Domain-neutral: every column is an opaque JSON payload the page produced.
 */
export const learnedWorkflowRun = pgTable("learned_workflow_run", {
  demo:           varchar("demo", { length: 32 }).primaryKey(),
  params:         jsonb("params").notNull(),           // {days, minSessions, withLlm} the run was mined with
  shapes:         jsonb("shapes").notNull(),           // Shape[] as eval-engine returned them
  policies:       jsonb("policies").notNull(),         // model readings keyed by shape — inferred, advisory
  rejected:       jsonb("rejected").notNull(),         // string[]
  approvals:      jsonb("approvals").notNull(),        // Record<shapeKey, {roles}>
  approvalShapes: jsonb("approval_shapes").notNull(),  // Record<shapeKey, Shape>
  minedAt:        timestamp("mined_at", { withTimezone: true }),
  updatedAt:      timestamp("updated_at", { withTimezone: true }).notNull().defaultNow(),
});

export type LearnedWorkflowRunRow = typeof learnedWorkflowRun.$inferSelect;
