import { pgTable, varchar, integer, primaryKey } from "drizzle-orm/pg-core";
import { createInsertSchema } from "drizzle-zod";
import { z } from "zod/v4";

/**
 * Customer-editable severity mapping for findings — one ordered rule-list per
 * demo, evaluated first-match-wins to derive a finding's severity from its
 * `family` + `effect` (both already carried on every /eval/findings row).
 *
 * Domain-neutral by construction: rules key on ENGINE concepts (family1/2/3,
 * block/approval_required/flag/allow) only — never a table, column, role or
 * threshold — so this stays out of engine code entirely (it lives in the
 * customer-facing UI layer). `family`/`effect` NULL means "any". Severity is
 * DERIVED at display time; nothing is stored on the finding itself, so there is
 * no re-evaluation or eval version-key change.
 */
export const severityRule = pgTable("severity_rule", {
  demo:     varchar("demo", { length: 32 }).notNull(),
  ordinal:  integer("ordinal").notNull(),                 // eval order; first match wins
  family:   varchar("family", { length: 16 }),            // null = any (family1|family2|family3)
  effect:   varchar("effect", { length: 24 }),            // null = any (block|approval_required|flag|allow)
  severity: varchar("severity", { length: 16 }).notNull(), // critical|high|medium|low
}, (t) => ({ pk: primaryKey({ columns: [t.demo, t.ordinal] }) }));

export const insertSeverityRuleSchema = createInsertSchema(severityRule);
export type InsertSeverityRule = z.infer<typeof insertSeverityRuleSchema>;
export type SeverityRuleRow = typeof severityRule.$inferSelect;
