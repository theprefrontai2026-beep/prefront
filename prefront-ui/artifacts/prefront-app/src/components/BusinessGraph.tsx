import { useEffect, useMemo, useState } from "react";
import ReactFlow, {
  Background,
  Controls,
  Handle,
  Position,
  MarkerType,
  useNodesState,
  useEdgesState,
  type NodeProps,
} from "reactflow";
import { getPolicy } from "../api";
import { buildPolicyIndex, deriveKind, deriveRoles, type AppliedPolicy } from "./policyIndex";

// ── Business Graph: live domain model derived from the connected catalog +
// approved policy. A *join view* — entities/processes come from the schema and
// intents; the policies attached to each node come from Policy Studio rules (and
// the published bound bundle when one exists). Engine stays domain-independent:
// every label/heuristic is cosmetic, no table/role/policy is hardcoded.

// ── Domain palette ────────────────────────────────────────────────────────────
const DOMAIN: Record<string, { bg: string; border: string; text: string; badge: string }> = {
  identity:   { bg: "#ede9fe", border: "#4f46e5", text: "#3730a3", badge: "#4f46e5" },
  financial:  { bg: "#e0f2fe", border: "#0891b2", text: "#0e7490", badge: "#0891b2" },
  credit:     { bg: "#fef3c7", border: "#d97706", text: "#92400e", badge: "#d97706" },
  compliance: { bg: "#fce7f3", border: "#db2777", text: "#9d174d", badge: "#db2777" },
  operations: { bg: "#dcfce7", border: "#16a34a", text: "#15803d", badge: "#16a34a" },
  governance: { bg: "#f1f5f9", border: "#64748b", text: "#334155", badge: "#64748b" },
};

const DOMAIN_ICON: Record<string, string> = {
  identity: "👤", financial: "💸", credit: "📈",
  compliance: "✅", operations: "📂", governance: "⚙️",
};

interface BizEntity {
  id: string;
  kind: "entity" | "process" | "role" | "governance";
  label: string;
  icon: string;
  domain: string;
  description: string;
  tables: string[];
  policies: string[];   // attached rule_keys
  roles: string[];
  triggers?: string;
  output?: string;
}

interface BizEdge {
  id: string;
  source: string;
  target: string;
  label: string;
  style?: "flow" | "permission" | "governance";
}

// ── Cosmetic name heuristics (domain-independent — labels only) ───────────────

function humanize(s: string): string {
  return String(s || "").replace(/[_\-]+/g, " ").replace(/\b\w/g, (c) => c.toUpperCase()).trim();
}

// Map deriveKind's table category onto the business-domain palette.
const CAT_TO_DOMAIN: Record<string, string> = {
  identity: "identity", financial: "financial", transaction: "financial",
  credit: "credit", audit: "compliance", _default: "operations",
};

function intentDomain(name: string): string {
  const n = (name || "").toLowerCase();
  if (/kyc|aml|complian|screen|sar|audit/.test(n)) return "compliance";
  if (/loan|credit|mortgage|risk|assess|score/.test(n)) return "credit";
  if (/transfer|payment|transact|balance|deposit|withdraw|account|fund|wire/.test(n)) return "financial";
  if (/user|customer|profile|identity|onboard|register/.test(n)) return "identity";
  return "operations";
}

function roleDomain(name: string): string {
  const n = (name || "").toLowerCase();
  if (/compli|audit|legal|kyc|aml/.test(n)) return "compliance";
  if (/risk|credit|loan|analyst|underwrit/.test(n)) return "credit";
  if (/teller|manager|ops|operation|clerk|officer|staff|rep|agent|support/.test(n)) return "operations";
  if (/admin|system|engine|service/.test(n)) return "governance";
  return "identity";
}

// ── Build the domain model from catalog + rules + intents + policy index ──────

interface BuiltGraph { nodes: BizEntity[]; edges: BizEdge[]; }

function buildGraph(
  catalog: any,
  rules: any[],
  intentsStr: string,
  policyIndex: Map<string, AppliedPolicy[]>,
  bound: any,
): BuiltGraph {
  const tables: any[] = catalog?.tables || [];
  const tableToEntId = new Map<string, string>(tables.map((t: any) => [t.name, `ent-${t.name}`] as [string, string]));

  // ── Entities (one per table) ──────────────────────────────────────────────
  const entities: BizEntity[] = tables.map((t: any) => {
    const pols = policyIndex.get(t.name) || [];
    const { category, icon } = deriveKind(t.name);
    const roleSet = new Set<string>();
    pols.forEach((p) => (p.roles || []).forEach((r) => roleSet.add(r)));
    const colCount = (t.columns || []).length;
    return {
      id: `ent-${t.name}`,
      kind: "entity",
      label: humanize(t.name),
      icon,
      domain: CAT_TO_DOMAIN[category] || "operations",
      description: `Business entity backed by table \`${t.name}\` (${colCount} column${colCount !== 1 ? "s" : ""}). ${pols.length} governance ${pols.length === 1 ? "rule" : "rules"} attached.`,
      tables: [t.name],
      policies: pols.map((p) => p.rule_key),
      roles: Array.from(roleSet).map(humanize),
    };
  });
  const entById = new Map<string, BizEntity>(entities.map((e) => [e.id, e] as [string, BizEntity]));

  // ── Normalize rules from both sources (in-app + bound bundle) ─────────────
  const allRules = [
    ...(rules || []).map((row: any) => ({
      key: row?.rule?.rule_key,
      intents: (row?.rule?.applies_to_intents || []) as string[],
      roles: deriveRoles(row?.rule?.conditions),
      approver: row?.rule?.effect?.approver_role as string | undefined,
    })),
    ...((bound?.rules || []) as any[]).map((br: any) => ({
      key: br.rule_key,
      intents: (br.intents || []) as string[],
      roles: deriveRoles(br.conditions),
      approver: br.effect?.approver_role as string | undefined,
    })),
  ].filter((r) => r.key);

  // intent -> tables it touches (from attached policies)
  const intentToTables = new Map<string, Set<string>>();
  for (const [tbl, pols] of policyIndex) {
    for (const p of pols) for (const intent of p.intents || []) {
      if (!intentToTables.has(intent)) intentToTables.set(intent, new Set());
      intentToTables.get(intent)!.add(tbl);
    }
  }
  // intent -> { rule_keys, roles }
  const intentToRules = new Map<string, { keys: Set<string>; roles: Set<string> }>();
  for (const r of allRules) {
    for (const intent of r.intents) {
      if (!intent) continue;
      if (!intentToRules.has(intent)) intentToRules.set(intent, { keys: new Set(), roles: new Set() });
      const e = intentToRules.get(intent)!;
      e.keys.add(r.key);
      r.roles.forEach((x) => e.roles.add(x));
      if (r.approver) e.roles.add(r.approver);
    }
  }

  // ── Processes (one per intent) ────────────────────────────────────────────
  const intentSet = new Set<string>();
  String(intentsStr || "").split(/[,\n]/).map((s) => s.trim()).filter(Boolean).forEach((i) => intentSet.add(i));
  (catalog?.suggestedIntents || catalog?.suggested_intents || []).forEach((i: string) => i && intentSet.add(i));
  allRules.forEach((r) => r.intents.forEach((i) => i && intentSet.add(i)));

  const processes: BizEntity[] = Array.from(intentSet).map((intent) => {
    const info = intentToRules.get(intent);
    const tbls = Array.from(intentToTables.get(intent) || []);
    const keys = info ? Array.from(info.keys) : [];
    const dom = intentDomain(intent);
    return {
      id: `proc-${intent}`,
      kind: "process",
      label: humanize(intent),
      icon: DOMAIN_ICON[dom] || "⚙️",
      domain: dom,
      description: `Business operation \`${intent}\`. Governed by ${keys.length} ${keys.length === 1 ? "rule" : "rules"}${tbls.length ? `, touching ${tbls.join(", ")}` : ""}.`,
      tables: tbls,
      policies: keys,
      roles: info ? Array.from(info.roles).map(humanize) : [],
      triggers: `Invoked as the \`${intent}\` intent`,
      output: keys.length ? `Governed result (${keys.length} rule${keys.length !== 1 ? "s" : ""} enforced)` : "Ungoverned (no rule attached yet)",
    };
  });

  // ── Roles (callers referenced by rules) ───────────────────────────────────
  const roleNames = new Set<string>();
  allRules.forEach((r) => { r.roles.forEach((x) => roleNames.add(x)); if (r.approver) roleNames.add(r.approver); });

  const roleToEnts = new Map<string, Set<string>>();
  for (const [tbl, pols] of policyIndex) {
    const entId = tableToEntId.get(tbl);
    if (!entId) continue;
    for (const p of pols) for (const r of p.roles || []) {
      if (!roleToEnts.has(r)) roleToEnts.set(r, new Set());
      roleToEnts.get(r)!.add(entId);
    }
  }
  const roleToProcs = new Map<string, Set<string>>();
  for (const [intent, info] of intentToRules) {
    const procId = `proc-${intent}`;
    for (const r of info.roles) {
      if (!roleToProcs.has(r)) roleToProcs.set(r, new Set());
      roleToProcs.get(r)!.add(procId);
    }
  }

  const roles: BizEntity[] = Array.from(roleNames).map((r) => {
    const dom = roleDomain(r);
    const entLabels = Array.from(roleToEnts.get(r) || [])
      .map((id) => entById.get(id)?.label).filter(Boolean) as string[];
    const polKeys = new Set<string>();
    for (const [, pols] of policyIndex) for (const p of pols) if ((p.roles || []).includes(r)) polKeys.add(p.rule_key);
    return {
      id: `role-${r}`,
      kind: "role",
      label: humanize(r),
      icon: DOMAIN_ICON[dom] || "👤",
      domain: dom,
      description: `Caller role \`${r}\`. Referenced by ${polKeys.size} governance ${polKeys.size === 1 ? "rule" : "rules"}${entLabels.length ? `, with access governed on ${entLabels.join(", ")}` : ""}.`,
      tables: [],
      policies: Array.from(polKeys),
      roles: [],
    };
  });

  // ── Governance (fixed structural nodes) ───────────────────────────────────
  const allRuleKeys = new Set<string>();
  entities.forEach((e) => e.policies.forEach((k) => allRuleKeys.add(k)));
  processes.forEach((p) => p.policies.forEach((k) => allRuleKeys.add(k)));
  const govPolicies = Array.from(allRuleKeys);

  const governance: BizEntity[] = [
    {
      id: "gov-policy", kind: "governance", label: "Policy Engine", icon: "⚙️", domain: "governance",
      description: "Runtime enforcement layer. Applies every approved governance rule to each data access and business action — resolving the caller's role and context, then masking, blocking, or requiring approval per policy.",
      tables: [], policies: govPolicies, roles: [],
    },
    {
      id: "gov-audit", kind: "governance", label: "Audit Trail", icon: "📜", domain: "governance",
      description: "Immutable, tamper-evident record of governed events — every policy decision, masked field, and approval is logged for compliance review.",
      tables: [], policies: govPolicies, roles: [],
    },
  ];

  const nodes = [...roles, ...entities, ...processes, ...governance];
  const nodeIds = new Set(nodes.map((n) => n.id));

  // ── Edges ─────────────────────────────────────────────────────────────────
  const edges: BizEdge[] = [];
  const seen = new Set<string>();
  let ei = 0;
  const link = (source: string, target: string, label: string, style: BizEdge["style"]) => {
    if (!nodeIds.has(source) || !nodeIds.has(target) || source === target) return;
    const k = `${source}->${target}`;
    if (seen.has(k)) return;
    seen.add(k);
    edges.push({ id: `be-${ei++}`, source, target, label, style });
  };

  for (const [r, ents] of roleToEnts) for (const entId of ents) link(`role-${r}`, entId, "accesses", "permission");
  for (const [r, procs] of roleToProcs) for (const procId of procs) link(`role-${r}`, procId, "performs", "permission");
  for (const [intent, tbls] of intentToTables) {
    for (const tbl of tbls) { const entId = tableToEntId.get(tbl); if (entId) link(entId, `proc-${intent}`, "feeds", "flow"); }
  }
  for (const t of tables) for (const fk of t.foreign_keys || []) {
    const from = tableToEntId.get(t.name); const to = tableToEntId.get(fk.to_table);
    if (from && to) link(from, to, "references", "flow");
  }
  for (const p of processes) if (p.policies.length) link("gov-policy", p.id, "governs", "governance");
  for (const e of entities) if (e.policies.length) link(e.id, "gov-audit", "records", "governance");

  return { nodes, edges };
}

// ── Tiered layout (roles → entities → processes → governance) ─────────────────
const TIER_ORDER: Record<BizEntity["kind"], number> = { role: 0, entity: 1, process: 2, governance: 3 };
const TIER_Y = [20, 210, 440, 670];
const COL_W = 245;

function layoutNodes(nodes: BizEntity[]): Record<string, { x: number; y: number }> {
  const byTier: BizEntity[][] = [[], [], [], []];
  nodes.forEach((n) => byTier[TIER_ORDER[n.kind]].push(n));
  const maxCount = Math.max(1, ...byTier.map((a) => a.length));
  const fullW = maxCount * COL_W;
  const pos: Record<string, { x: number; y: number }> = {};
  byTier.forEach((row, tier) => {
    const offset = (fullW - row.length * COL_W) / 2;
    row.forEach((n, i) => { pos[n.id] = { x: offset + i * COL_W, y: TIER_Y[tier] }; });
  });
  return pos;
}

// ── React Flow node types ─────────────────────────────────────────────────────

function RoleNode({ data }: NodeProps) {
  const d = DOMAIN[data.domain] ?? DOMAIN.governance;
  return (
    <div
      className="bg-node"
      style={{
        background: d.bg,
        border: `1.5px solid ${d.border}`,
        borderRadius: 20,
        padding: "5px 12px",
        display: "flex",
        alignItems: "center",
        gap: 6,
        fontSize: 11,
        fontWeight: 600,
        color: d.text,
        whiteSpace: "nowrap",
        boxShadow: data.selected ? `0 0 0 2px ${d.border}` : "0 1px 3px rgba(0,0,0,.1)",
        cursor: "pointer",
      }}
    >
      <span style={{ fontSize: 13 }}>{data.icon}</span>
      {data.label}
      <Handle type="source" position={Position.Bottom} style={{ opacity: 0, pointerEvents: "none" }} />
    </div>
  );
}

function EntityNode({ data }: NodeProps) {
  const d = DOMAIN[data.domain] ?? DOMAIN.governance;
  return (
    <div
      className="bg-entity-node"
      style={{
        background: "#fff",
        border: `2px solid ${d.border}`,
        borderRadius: 10,
        width: 200,
        overflow: "hidden",
        boxShadow: data.selected ? `0 0 0 3px ${d.border}55` : "0 2px 8px rgba(0,0,0,.08)",
        cursor: "pointer",
      }}
    >
      <div style={{ background: d.bg, borderBottom: `1px solid ${d.border}33`, padding: "8px 10px", display: "flex", alignItems: "center", gap: 7 }}>
        <span style={{ fontSize: 16 }}>{data.icon}</span>
        <div>
          <div style={{ fontWeight: 700, fontSize: 12, color: d.text }}>{data.label}</div>
          <div style={{ fontSize: 10, color: d.badge, textTransform: "uppercase", letterSpacing: "0.04em", fontWeight: 600 }}>
            {data.domain}
          </div>
        </div>
      </div>
      <div style={{ padding: "6px 10px", fontSize: 10.5, color: "#6b7280", lineHeight: 1.45 }}>
        {data.tables?.length > 0 && (
          <div style={{ display: "flex", gap: 4, flexWrap: "wrap", marginBottom: 4 }}>
            {data.tables.map((t: string) => (
              <span key={t} style={{ background: "#f1f5f9", borderRadius: 4, padding: "1px 5px", fontSize: 10, color: "#475569", fontFamily: "monospace" }}>{t}</span>
            ))}
          </div>
        )}
        <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
          <span style={{ color: "#9ca3af" }}>{data.policies?.length} policies</span>
          {data.roles?.length > 0 && <span style={{ color: "#9ca3af" }}>· {data.roles.length} roles</span>}
        </div>
      </div>
      <Handle type="target" position={Position.Top}    style={{ opacity: 0 }} />
      <Handle type="source" position={Position.Bottom} style={{ opacity: 0 }} />
      <Handle type="source" position={Position.Right}  style={{ opacity: 0 }} id="r" />
      <Handle type="target" position={Position.Left}   style={{ opacity: 0 }} id="l" />
    </div>
  );
}

function ProcessNode({ data }: NodeProps) {
  const d = DOMAIN[data.domain] ?? DOMAIN.governance;
  return (
    <div
      className="bg-process-node"
      style={{
        background: "#fff",
        border: `1.5px dashed ${d.border}`,
        borderRadius: 10,
        width: 200,
        overflow: "hidden",
        boxShadow: data.selected ? `0 0 0 3px ${d.border}55` : "0 1px 4px rgba(0,0,0,.06)",
        cursor: "pointer",
      }}
    >
      <div style={{ background: d.bg, borderBottom: `1px solid ${d.border}33`, padding: "8px 10px", display: "flex", alignItems: "center", gap: 7 }}>
        <span style={{ fontSize: 16 }}>{data.icon}</span>
        <div>
          <div style={{ fontWeight: 700, fontSize: 12, color: d.text }}>{data.label}</div>
          <div style={{ fontSize: 10, color: d.badge, textTransform: "uppercase", letterSpacing: "0.04em", fontWeight: 600 }}>
            process
          </div>
        </div>
      </div>
      <div style={{ padding: "6px 10px", fontSize: 10.5, color: "#6b7280", lineHeight: 1.4 }}>
        {data.triggers && <div><span style={{ color: "#9ca3af", fontWeight: 600 }}>⚡ </span>{data.triggers}</div>}
        {data.output && <div style={{ marginTop: 2 }}><span style={{ color: "#9ca3af", fontWeight: 600 }}>→ </span>{data.output}</div>}
      </div>
      <Handle type="target" position={Position.Top}    style={{ opacity: 0 }} />
      <Handle type="source" position={Position.Bottom} style={{ opacity: 0 }} />
      <Handle type="source" position={Position.Right}  style={{ opacity: 0 }} id="r" />
      <Handle type="target" position={Position.Left}   style={{ opacity: 0 }} id="l" />
    </div>
  );
}

function GovNode({ data }: NodeProps) {
  return (
    <div
      className="bg-gov-node"
      style={{
        background: "#f8fafc",
        border: "1.5px solid #cbd5e1",
        borderRadius: 10,
        width: 210,
        overflow: "hidden",
        boxShadow: data.selected ? "0 0 0 3px #64748b55" : "0 1px 4px rgba(0,0,0,.06)",
        cursor: "pointer",
      }}
    >
      <div style={{ background: "#f1f5f9", borderBottom: "1px solid #e2e8f0", padding: "8px 10px", display: "flex", alignItems: "center", gap: 7 }}>
        <span style={{ fontSize: 16 }}>{data.icon}</span>
        <div>
          <div style={{ fontWeight: 700, fontSize: 12, color: "#334155" }}>{data.label}</div>
          <div style={{ fontSize: 10, color: "#64748b", textTransform: "uppercase", letterSpacing: "0.04em", fontWeight: 600 }}>governance</div>
        </div>
      </div>
      <div style={{ padding: "6px 10px", fontSize: 10.5, color: "#6b7280" }}>
        <span>{data.policies?.length} enforced policies</span>
      </div>
      <Handle type="target" position={Position.Top}    style={{ opacity: 0 }} />
      <Handle type="source" position={Position.Bottom} style={{ opacity: 0 }} />
      <Handle type="source" position={Position.Left}   style={{ opacity: 0 }} id="l" />
      <Handle type="target" position={Position.Right}  style={{ opacity: 0 }} id="r" />
    </div>
  );
}

const NODE_TYPES = {
  bizRole:       RoleNode,
  bizEntity:     EntityNode,
  bizProcess:    ProcessNode,
  bizGovernance: GovNode,
};

// ── Detail Panel ──────────────────────────────────────────────────────────────
function DetailPanel({ node, onClose }: { node: BizEntity; onClose: () => void }) {
  const d = DOMAIN[node.domain] ?? DOMAIN.governance;
  const kindLabel = node.kind === "entity" ? "Business Entity" : node.kind === "process" ? "Business Process" : node.kind === "role" ? "Actor / Role" : "Governance";

  return (
    <div className="dg-detail-panel" style={{ width: 300, minWidth: 300 }}>
      {/* Header */}
      <div className="dg-detail-header" style={{ borderBottom: `2px solid ${d.border}` }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span style={{ fontSize: 20 }}>{node.icon}</span>
          <div>
            <div className="dg-detail-name">{node.label}</div>
            <div style={{ fontSize: 10, color: d.badge, fontWeight: 600, textTransform: "uppercase" }}>
              {kindLabel} · {node.domain}
            </div>
          </div>
        </div>
        <button className="dg-detail-close" onClick={onClose}>×</button>
      </div>

      <div className="dg-detail-body">
        {/* Description */}
        <div className="dg-detail-section">
          <div className="dg-detail-section-title">Description</div>
          <p style={{ fontSize: 12, color: "#374151", lineHeight: 1.6, margin: 0 }}>{node.description}</p>
        </div>

        {/* Triggers & Output (processes) */}
        {(node.triggers || node.output) && (
          <div className="dg-detail-section">
            <div className="dg-detail-section-title">Process Flow</div>
            {node.triggers && (
              <div style={{ fontSize: 12, marginBottom: 4 }}>
                <span style={{ fontWeight: 600, color: "#6b7280" }}>⚡ Trigger: </span>
                <span style={{ color: "#374151" }}>{node.triggers}</span>
              </div>
            )}
            {node.output && (
              <div style={{ fontSize: 12 }}>
                <span style={{ fontWeight: 600, color: "#6b7280" }}>→ Output: </span>
                <span style={{ color: "#374151" }}>{node.output}</span>
              </div>
            )}
          </div>
        )}

        {/* Mapped tables */}
        {node.tables.length > 0 && (
          <div className="dg-detail-section">
            <div className="dg-detail-section-title">Mapped Tables</div>
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
              {node.tables.map(t => (
                <span key={t} style={{ background: "#f1f5f9", border: "1px solid #e2e8f0", borderRadius: 5, padding: "2px 8px", fontSize: 11, color: "#334155", fontFamily: "monospace" }}>
                  {t}
                </span>
              ))}
            </div>
          </div>
        )}

        {/* Policy references */}
        <div className="dg-detail-section">
          <div className="dg-detail-section-title">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>
            </svg>
            Attached Policies ({node.policies.length})
          </div>
          {node.policies.length === 0 && (
            <div style={{ fontSize: 11.5, color: "#9ca3af" }}>No rules reference this node yet.</div>
          )}
          {node.policies.map(p => (
            <div key={p} style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 4 }}>
              <span style={{ background: d.bg, color: d.text, border: `1px solid ${d.border}55`, borderRadius: 4, padding: "1px 6px", fontSize: 10, fontWeight: 700, fontFamily: "monospace" }}>
                {p}
              </span>
            </div>
          ))}
        </div>

        {/* Authorized roles */}
        {node.roles.length > 0 && (
          <div className="dg-detail-section">
            <div className="dg-detail-section-title">Authorized Roles</div>
            <div style={{ display: "flex", gap: 5, flexWrap: "wrap" }}>
              {node.roles.map(r => (
                <span key={r} className="dg-role-chip">{r}</span>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// ── Stats bar ─────────────────────────────────────────────────────────────────
function StatsBar({ nodes, datasourceId, domain }: { nodes: BizEntity[]; datasourceId?: string; domain?: string }) {
  const entityCount  = nodes.filter(n => n.kind === "entity").length;
  const processCount = nodes.filter(n => n.kind === "process").length;
  const roleCount    = nodes.filter(n => n.kind === "role").length;
  const policyCount  = new Set(nodes.flatMap(n => n.policies)).size;

  return (
    <div className="dg-stats-bar">
      <div className="dg-stat-item">
        <span className="dg-stat-value">{entityCount}</span>
        <span className="dg-stat-label">ENTITIES</span>
      </div>
      <div className="dg-stat-sep" />
      <div className="dg-stat-item">
        <span className="dg-stat-value">{processCount}</span>
        <span className="dg-stat-label">PROCESSES</span>
      </div>
      <div className="dg-stat-sep" />
      <div className="dg-stat-item">
        <span className="dg-stat-value">{roleCount}</span>
        <span className="dg-stat-label">ROLES</span>
      </div>
      <div className="dg-stat-sep" />
      <div className="dg-stat-item">
        <span className="dg-stat-value" style={{ color: "var(--blue)" }}>{policyCount}</span>
        <span className="dg-stat-label">POLICY RULES</span>
      </div>
      <div style={{ marginLeft: "auto", display: "flex", gap: 8, alignItems: "center" }}>
        {domain && (
          <span style={{ background: "#f8fafc", border: "1px solid #e2e8f0", color: "#64748b", borderRadius: 6, padding: "3px 10px", fontSize: 11 }}>
            {humanize(domain)} · Business Domain Model
          </span>
        )}
        {datasourceId && <span className="dg-source-badge">{datasourceId}</span>}
      </div>
    </div>
  );
}

// ── Legend ────────────────────────────────────────────────────────────────────
function Legend() {
  return (
    <div className="dg-legend" style={{ bottom: 44 }}>
      <div className="dg-legend-item"><span style={{ display: "inline-block", width: 12, height: 12, borderRadius: 6, background: "#e0f2fe", border: "1.5px solid #0891b2" }} /> Entity</div>
      <div className="dg-legend-item"><span style={{ display: "inline-block", width: 12, height: 12, borderRadius: 3, background: "#dcfce7", border: "1.5px dashed #16a34a" }} /> Process</div>
      <div className="dg-legend-item"><span style={{ display: "inline-block", width: 12, height: 12, borderRadius: 10, background: "#ede9fe", border: "1.5px solid #4f46e5" }} /> Role</div>
      <div className="dg-legend-item"><span style={{ display: "inline-block", width: 12, height: 12, borderRadius: 3, background: "#f1f5f9", border: "1.5px solid #64748b" }} /> Governance</div>
    </div>
  );
}

// ── Edge style helpers ────────────────────────────────────────────────────────
function makeEdge(e: BizEdge) {
  const isGov  = e.style === "governance";
  const isPerm = e.style === "permission";
  return {
    id: e.id,
    source: e.source,
    target: e.target,
    label: e.label,
    type: "smoothstep",
    animated: isGov,
    style: {
      stroke: isGov ? "#64748b" : isPerm ? "#818cf8" : "#0891b2",
      strokeWidth: isGov ? 1 : isPerm ? 1.2 : 1.8,
      strokeDasharray: isGov ? "4 3" : undefined,
    },
    labelStyle: { fontSize: 9, fill: "#9ca3af", fontWeight: 600 },
    labelBgStyle: { fill: "#fff", fillOpacity: 0.85 },
    labelBgPadding: [3, 4] as [number, number],
    markerEnd: {
      type: MarkerType.ArrowClosed,
      width: 10,
      height: 10,
      color: isGov ? "#64748b" : isPerm ? "#818cf8" : "#0891b2",
    },
  };
}

// ── Main component ─────────────────────────────────────────────────────────────
interface Props {
  catalog?: any;
  datasourceId?: string;
  rules?: any[];
  intents?: string;
  domain?: string;
  pii?: Record<string, { label: string; score: number }>;
}

export default function BusinessGraph({ catalog, datasourceId, rules = [], intents = "", domain }: Props) {
  const [bound, setBound] = useState<any>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);

  // Authoritative bound policy bundle for this datasource (best-effort).
  useEffect(() => {
    let live = true;
    setBound(null);
    if (!datasourceId) return;
    getPolicy(datasourceId)
      .then((r) => { if (live) setBound(r?.policy_bundle || null); })
      .catch(() => { if (live) setBound(null); });
    return () => { live = false; };
  }, [datasourceId]);

  const hasCatalog = !!(catalog && (catalog.tables?.length ?? 0) > 0);
  const hasPolicy  = (rules || []).some((r) => r.review_status === "approved") || (bound?.rules?.length ?? 0) > 0;
  const ready = hasCatalog && hasPolicy;

  const policyIndex = useMemo(
    () => buildPolicyIndex(catalog, rules, bound),
    [catalog, rules, bound]
  );

  const graph = useMemo<BuiltGraph>(
    () => (ready ? buildGraph(catalog, rules, intents, policyIndex, bound) : { nodes: [], edges: [] }),
    [ready, catalog, rules, intents, policyIndex, bound]
  );

  const positions = useMemo(() => layoutNodes(graph.nodes), [graph.nodes]);

  const rfNodes = useMemo(() =>
    graph.nodes.map((n) => ({
      id: n.id,
      type: n.kind === "role" ? "bizRole" : n.kind === "entity" ? "bizEntity" : n.kind === "process" ? "bizProcess" : "bizGovernance",
      position: positions[n.id] ?? { x: 0, y: 0 },
      data: { ...n } as any,
    })),
  [graph.nodes, positions]);

  const rfEdges = useMemo(() => graph.edges.map(makeEdge), [graph.edges]);

  const [nodes, setNodes, onNodesChange] = useNodesState(rfNodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState(rfEdges);

  useEffect(() => {
    setNodes(rfNodes);
    setEdges(rfEdges);
    setSelectedId((prev) => (prev && graph.nodes.some((n) => n.id === prev) ? prev : null));
  }, [rfNodes, rfEdges, graph.nodes, setNodes, setEdges]);

  const styledNodes = useMemo(
    () => nodes.map((n) => ({ ...n, data: { ...n.data, selected: n.id === selectedId } })),
    [nodes, selectedId]
  );

  const selectedNode = useMemo(
    () => (selectedId ? graph.nodes.find((n) => n.id === selectedId) ?? null : null),
    [selectedId, graph.nodes]
  );

  if (!ready) {
    return (
      <div className="dg-shell">
        <div className="dg-empty-state">
          <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.4" style={{ color: "var(--muted)", marginBottom: 14 }}>
            <circle cx="12" cy="12" r="3" />
            <circle cx="5" cy="5" r="2" />
            <circle cx="19" cy="5" r="2" />
            <circle cx="19" cy="19" r="2" />
            <path d="M7 6.5 10 10M17 6.5 14 10M17 17.5 14 14" />
          </svg>
          <div style={{ fontWeight: 600, color: "var(--ink-soft)", marginBottom: 6, fontSize: 15 }}>
            {!hasCatalog ? "No datasource connected" : "No approved policy yet"}
          </div>
          <div style={{ color: "var(--muted)", fontSize: 13, textAlign: "center", maxWidth: 380, lineHeight: 1.5 }}>
            {!hasCatalog ? (
              <>Connect a datasource in the <strong>Data Connector</strong> tab, then approve policy in <strong>Policy Studio</strong>, to derive the business domain model — entities, processes, roles, and the policies attached to each.</>
            ) : (
              <>Approve at least one rule in the <strong>Policy Studio</strong> tab. The Business Graph is a join of your schema and your policy — it shows which governance rules attach to which entity, process, and role.</>
            )}
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="dg-shell bg-graph-shell">
      <StatsBar nodes={graph.nodes} datasourceId={datasourceId} domain={domain} />

      <div className="dg-workspace">
        <div className="dg-canvas">
          <ReactFlow
            nodes={styledNodes}
            edges={edges}
            nodeTypes={NODE_TYPES}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onNodeClick={(_, node) => setSelectedId(prev => prev === node.id ? null : node.id)}
            fitView
            fitViewOptions={{ padding: 0.1 }}
            minZoom={0.3}
            maxZoom={2}
            proOptions={{ hideAttribution: true }}
          >
            <Background color="#e4e7ed" gap={24} size={1} />
            <Controls showInteractive={false} />
          </ReactFlow>
          <Legend />
        </div>

        {selectedNode ? (
          <DetailPanel node={selectedNode} onClose={() => setSelectedId(null)} />
        ) : (
          <div className="dg-detail-panel dg-detail-empty" style={{ width: 280, minWidth: 280 }}>
            <div className="dg-detail-empty-icon">
              <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="#cbd5e1" strokeWidth="1.5">
                <rect x="2" y="7" width="20" height="14" rx="2"/>
                <path d="M16 7V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v2"/>
                <line x1="12" y1="12" x2="12" y2="16"/>
                <line x1="10" y1="14" x2="14" y2="14"/>
              </svg>
            </div>
            <div className="dg-detail-empty-title">Select a node</div>
            <div className="dg-detail-empty-sub">Click any entity, process, role, or governance node to see its description, mapped tables, and attached policies.</div>
          </div>
        )}
      </div>
    </div>
  );
}
