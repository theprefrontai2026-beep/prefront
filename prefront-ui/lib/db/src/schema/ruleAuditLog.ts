import { pgTable, serial, text, timestamp, varchar, jsonb } from "drizzle-orm/pg-core";
import { createInsertSchema } from "drizzle-zod";
import { z } from "zod/v4";

export const ruleAuditLog = pgTable("rule_audit_log", {
  id:           serial("id").primaryKey(),
  documentId:   varchar("document_id", { length: 128 }).notNull(),
  ruleKey:      varchar("rule_key", { length: 256 }).notNull(),
  action:       varchar("action", { length: 32 }).notNull(),   // "approved" | "rejected" | "extracted"
  reviewerName: varchar("reviewer_name", { length: 64 }).notNull(),
  reviewerColor: varchar("reviewer_color", { length: 16 }),
  before:       jsonb("before"),
  after:        jsonb("after"),
  note:         text("note"),
  createdAt:    timestamp("created_at", { withTimezone: true }).defaultNow().notNull(),
});

export const insertAuditSchema = createInsertSchema(ruleAuditLog).omit({ id: true, createdAt: true });
export type InsertAudit = z.infer<typeof insertAuditSchema>;
export type AuditEntry  = typeof ruleAuditLog.$inferSelect;
