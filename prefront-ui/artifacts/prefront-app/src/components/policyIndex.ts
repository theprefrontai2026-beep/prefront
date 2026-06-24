// Shared policy-index helpers used by DataGraph and BusinessGraph.
// Maps approved Policy Studio rules + the published bound bundle onto schema
// objects (tables/columns) by name-matching, layered. The engine stays
// domain-independent: deriveKind is a cosmetic name heuristic only.

export interface AppliedPolicy {
  rule_key: string;
  rule_type?: string;
  decision?: string;            // block | mask | approval_required | allow | escalate
  restricted_fields?: string[];
  intents?: string[];
  approver_role?: string;
  message?: string;
  roles?: string[];             // derived from caller.role conditions
  conditions?: any[];           // raw rule conditions (for synthesizing a description)
  status: string;               // approved | pending | rejected | published
  source: "rule" | "bound";
  columns: string[];            // columns on THIS table the rule touches
}

export const DECISION_LABEL: Record<string, string> = {
  block: "blocked", mask: "masked", approval_required: "approval",
  escalate: "escalate", allow: "allow",
};
export const DECISION_SEV: Record<string, "high" | "medium" | "low"> = {
  block: "high", approval_required: "high", escalate: "high",
  mask: "medium", allow: "low",
};

// Heuristic table category/icon from its name — the catalog carries no domain
// category, so this is cosmetic only (engine stays domain-independent).
export function deriveKind(name: string): { category: string; icon: string } {
  const n = (name || "").toLowerCase();
  if (/audit|log|event|history/.test(n))                 return { category: "audit",       icon: "🔍" };
  if (/transaction|payment|transfer|txn|ledger|order/.test(n)) return { category: "transaction", icon: "💸" };
  if (/loan|credit|mortgage/.test(n))                    return { category: "credit",      icon: "📋" };
  if (/user|customer|member|person|employee|principal/.test(n)) return { category: "identity",    icon: "👤" };
  if (/account|wallet|balance|deposit|fund/.test(n))     return { category: "financial",   icon: "🏦" };
  return { category: "_default", icon: "🗄" };
}

export function deriveRoles(conditions: any[]): string[] {
  const out: string[] = [];
  for (const c of conditions || []) {
    if (typeof c?.field === "string" && c.field.toLowerCase().endsWith("role")) {
      const v = c.value;
      if (Array.isArray(v)) out.push(...v.map(String));
      else if (v != null) out.push(String(v));
    }
  }
  return Array.from(new Set(out));
}

/** Map each table -> applied policies, from in-app rules then the bound bundle. */
export function buildPolicyIndex(catalog: any, rules: any[], bound: any): Map<string, AppliedPolicy[]> {
  const tables: any[] = catalog?.tables || [];
  // column name (lower) -> set of table names that have it
  const colToTables = new Map<string, Set<string>>();
  for (const t of tables) {
    for (const c of t.columns || []) {
      const k = String(c.name).toLowerCase();
      if (!colToTables.has(k)) colToTables.set(k, new Set());
      colToTables.get(k)!.add(t.name);
    }
  }
  const colSet = new Set(colToTables.keys());
  // table -> rule_key -> AppliedPolicy
  const byTable = new Map<string, Map<string, AppliedPolicy>>();
  const put = (table: string, p: AppliedPolicy, override: boolean) => {
    if (!byTable.has(table)) byTable.set(table, new Map());
    const m = byTable.get(table)!;
    const prev = m.get(p.rule_key);
    if (prev && !override) {
      prev.columns = Array.from(new Set([...prev.columns, ...p.columns]));
    } else {
      m.set(p.rule_key, { ...p, columns: Array.from(new Set(p.columns)) });
    }
  };

  // Layer 1: in-app Policy Studio rules (approved + pending), name-matched.
  for (const row of rules || []) {
    const rule = row?.rule || {};
    const key = rule.rule_key;
    if (!key) continue;
    const effect = rule.effect || {};
    const fields = [
      ...(effect.restricted_fields || []),
      ...((rule.conditions || []).map((c: any) => c.field)),
      ...((rule.conditions || []).map((c: any) => c.value)),
    ].filter((f) => typeof f === "string" && colSet.has(f.toLowerCase()));
    const touched = Array.from(new Set(fields.map((f: string) => f.toLowerCase())));
    if (touched.length === 0) continue; // intent-level rule, no column anchor
    const base: Omit<AppliedPolicy, "columns"> = {
      rule_key: key,
      rule_type: rule.rule_type,
      decision: effect.decision,
      restricted_fields: effect.restricted_fields,
      intents: rule.applies_to_intents,
      approver_role: effect.approver_role,
      message: effect.message,
      roles: deriveRoles(rule.conditions),
      conditions: rule.conditions,
      status: row.review_status || "pending",
      source: "rule",
    };
    for (const col of touched) {
      for (const table of colToTables.get(col) || []) {
        const own = (catalog.tables.find((t: any) => t.name === table)?.columns || [])
          .map((c: any) => String(c.name).toLowerCase());
        if (own.includes(col)) put(table, { ...base, columns: [col] }, false);
      }
    }
  }

  // Layer 2: bound bundle — authoritative table.column bindings; overrides L1.
  for (const br of bound?.rules || []) {
    const key = br.rule_key;
    if (!key) continue;
    const effect = br.effect || {};
    const byTbl = new Map<string, string[]>();
    for (const b of Object.values<any>(br.bindings || {})) {
      if (b?.source === "column" && typeof b.column === "string" && b.column.includes(".")) {
        const [tbl, col] = b.column.split(".");
        if (!byTbl.has(tbl)) byTbl.set(tbl, []);
        byTbl.get(tbl)!.push(col);
      }
    }
    const base: Omit<AppliedPolicy, "columns"> = {
      rule_key: key,
      rule_type: br.rule_type,
      decision: effect.decision,
      restricted_fields: effect.restricted_fields,
      intents: br.intents,
      message: effect.message,
      roles: deriveRoles(br.conditions),
      conditions: br.conditions,
      status: "published",
      source: "bound",
    };
    for (const [tbl, cols] of byTbl) put(tbl, { ...base, columns: cols }, true);
  }

  const out = new Map<string, AppliedPolicy[]>();
  for (const [t, m] of byTable) out.set(t, Array.from(m.values()));
  return out;
}
