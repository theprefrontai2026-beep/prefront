import { useCallback, useMemo, useState } from "react";
import ReactFlow, {
  Background,
  Controls,
  Handle,
  Position,
  MarkerType,
  useNodesState,
  useEdgesState,
} from "reactflow";

// ── Stub data from SecureBank SQL + BRD ──────────────────────────────────────

const CATEGORY_COLOR: Record<string, string> = {
  identity:    "#4f46e5",
  financial:   "#0891b2",
  transaction: "#059669",
  credit:      "#d97706",
  audit:       "#7c3aed",
};

interface Column {
  name: string;
  type: string;
  pk?: boolean;
  fk?: string;
  sensitive?: boolean;
  restriction?: string;
  gov?: boolean;
}

interface PolicyRule {
  id: string;
  title: string;
  reqRef: string;
  description: string;
  severity: "high" | "medium" | "low";
  roles?: string[];
  columns?: string[];
}

interface TableDef {
  id: string;
  label: string;
  category: string;
  icon: string;
  columns: Column[];
  tags: string[];
  policies: string[];
  rowCount: string;
}

const POLICY_RULES: Record<string, PolicyRule> = {
  "P-SSN-VIEW": {
    id: "P-SSN-VIEW",
    title: "SSN restricted to Bank Manager",
    reqRef: "FR-USER-1, FR-ACCT-3",
    description: "The SSN field on the users table is visible only to users with the Bank Manager role. Bank Tellers and Account Holders receive a masked value.",
    severity: "high",
    roles: ["Bank Manager"],
    columns: ["ssn"],
  },
  "P-CREDIT-VIEW": {
    id: "P-CREDIT-VIEW",
    title: "Credit score visible to Bank Staff only",
    reqRef: "FR-USER-1",
    description: "Credit score and annual income are restricted to Bank Teller and Bank Manager roles. Account Holders cannot access these fields.",
    severity: "high",
    roles: ["Bank Teller", "Bank Manager"],
    columns: ["credit_score", "annual_income"],
  },
  "P-TXN-LIMIT-AH": {
    id: "P-TXN-LIMIT-AH",
    title: "Account Holder daily transfer limit: $5,000",
    reqRef: "FR-TXN-2",
    description: "Any single transfer or cumulative daily transfers by an Account Holder that reach or exceed $5,000 shall be rejected. This is a non-negotiable business rule.",
    severity: "high",
    roles: ["Account Holder"],
    columns: ["amount"],
  },
  "P-TXN-APPROVAL": {
    id: "P-TXN-APPROVAL",
    title: "Transfers > $5,000 require Manager approval",
    reqRef: "FR-TXN-3",
    description: "Transfers exceeding $5,000 may only be initiated by a Bank Teller and require explicit Bank Manager approval before funds are moved.",
    severity: "high",
    roles: ["Bank Teller", "Bank Manager"],
    columns: ["amount"],
  },
  "P-ACCT-MAKER-CHECKER": {
    id: "P-ACCT-MAKER-CHECKER",
    title: "Sensitive account fields require maker-checker",
    reqRef: "FR-ACCT-3",
    description: "Changes to interest_rate, credit_limit, overdraft_limit, and available_credit must go through a pending-change approval workflow. Bank Manager approval is required before changes take effect.",
    severity: "high",
    columns: ["interest_rate", "credit_limit", "overdraft_limit", "available_credit"],
  },
  "P-LOAN-LIMITS": {
    id: "P-LOAN-LIMITS",
    title: "Loan approval limits by role",
    reqRef: "FR-LOAN-3",
    description: "Bank Tellers may approve loans up to and including $50,000. Bank Managers may approve loans of any amount. Account Holders may not approve any loan.",
    severity: "high",
    roles: ["Bank Teller", "Bank Manager"],
    columns: ["amount", "status"],
  },
  "P-LOAN-LIFECYCLE": {
    id: "P-LOAN-LIFECYCLE",
    title: "Loan status follows defined lifecycle",
    reqRef: "FR-LOAN-5",
    description: "Loan status transitions must follow: pending → offered → accepted/rejected → approved/declined → active → paid_off. No out-of-order transitions are permitted.",
    severity: "medium",
    columns: ["status"],
  },
  "P-AUDIT-EXPORT": {
    id: "P-AUDIT-EXPORT",
    title: "Audit log export restricted to Bank Manager",
    reqRef: "FR-AUDIT-3",
    description: "Bank Tellers may view and filter the audit log but cannot export it. Only Bank Managers may export audit log data.",
    severity: "medium",
    roles: ["Bank Manager"],
  },
  "P-AUDIT-DENY-LOG": {
    id: "P-AUDIT-DENY-LOG",
    title: "Every authorization decision must be logged",
    reqRef: "FR-AUTHZ-4, FR-AUDIT-1",
    description: "Every Allow or Deny decision from the Authorization Service must produce a structured audit_logs entry including principal, role, action, resource, decision, and risk level.",
    severity: "medium",
    columns: ["decision", "principal_id", "action"],
  },
  "P-ACCT-DELETE": {
    id: "P-ACCT-DELETE",
    title: "Account deletion restricted to Bank Manager",
    reqRef: "FR-ACCT-4",
    description: "Only Bank Managers may delete bank accounts. Bank Tellers and Account Holders are denied.",
    severity: "medium",
    roles: ["Bank Manager"],
  },
};

const TABLES: TableDef[] = [
  {
    id: "users",
    label: "users",
    category: "identity",
    icon: "👤",
    rowCount: "2.4K",
    tags: ["PII", "Auth", "Governance"],
    policies: ["P-SSN-VIEW", "P-CREDIT-VIEW"],
    columns: [
      { name: "id", type: "UUID", pk: true },
      { name: "name", type: "varchar(100)" },
      { name: "email", type: "varchar(150)", gov: true },
      { name: "role", type: "varchar(50)", gov: true },
      { name: "ssn", type: "varchar(11)", sensitive: true, restriction: "Bank Manager only" },
      { name: "date_of_birth", type: "timestamp", sensitive: true },
      { name: "credit_score", type: "integer", sensitive: true, restriction: "Bank Staff only" },
      { name: "annual_income", type: "decimal", sensitive: true, restriction: "Bank Staff only" },
      { name: "risk_level", type: "varchar(100)", gov: true },
      { name: "status", type: "varchar(20)" },
      { name: "identity_verified", type: "varchar(20)" },
    ],
  },
  {
    id: "accounts",
    label: "accounts",
    category: "financial",
    icon: "🏦",
    rowCount: "5.1K",
    tags: ["Financial", "Maker-Checker", "Risk"],
    policies: ["P-ACCT-MAKER-CHECKER", "P-ACCT-DELETE"],
    columns: [
      { name: "id", type: "UUID", pk: true },
      { name: "user_id", type: "UUID", fk: "users" },
      { name: "account_number", type: "varchar(20)", gov: true },
      { name: "account_type", type: "varchar(50)" },
      { name: "balance", type: "decimal", sensitive: true },
      { name: "credit_limit", type: "decimal", sensitive: true, restriction: "Maker-checker required" },
      { name: "interest_rate", type: "decimal", sensitive: true, restriction: "Maker-checker required" },
      { name: "overdraft_limit", type: "decimal", restriction: "Maker-checker required" },
      { name: "status", type: "varchar(20)" },
      { name: "risk_rating", type: "varchar(20)", gov: true },
      { name: "aml_status", type: "varchar(20)", gov: true },
      { name: "kyc_status", type: "varchar(20)", gov: true },
    ],
  },
  {
    id: "transactions",
    label: "transactions",
    category: "transaction",
    icon: "💸",
    rowCount: "48K",
    tags: ["Financial", "Limit-Enforced", "Approval"],
    policies: ["P-TXN-LIMIT-AH", "P-TXN-APPROVAL"],
    columns: [
      { name: "id", type: "UUID", pk: true },
      { name: "from_account_id", type: "UUID", fk: "accounts" },
      { name: "to_account_id", type: "UUID", fk: "accounts" },
      { name: "type", type: "varchar(50)", gov: true },
      { name: "amount", type: "decimal", sensitive: true, restriction: "$5k limit (AH) / approval >$5k" },
      { name: "description", type: "text" },
      { name: "status", type: "varchar(20)" },
      { name: "processed_by", type: "UUID", fk: "users" },
    ],
  },
  {
    id: "loans",
    label: "loans",
    category: "credit",
    icon: "📋",
    rowCount: "890",
    tags: ["Credit", "Lifecycle", "Role-Gated"],
    policies: ["P-LOAN-LIMITS", "P-LOAN-LIFECYCLE"],
    columns: [
      { name: "id", type: "UUID", pk: true },
      { name: "user_id", type: "UUID", fk: "users" },
      { name: "type", type: "varchar(50)" },
      { name: "amount", type: "decimal", sensitive: true, restriction: "Teller ≤$50k · Manager unlimited" },
      { name: "interest_rate", type: "decimal" },
      { name: "term_months", type: "integer" },
      { name: "monthly_payment", type: "decimal" },
      { name: "remaining_balance", type: "decimal", sensitive: true },
      { name: "status", type: "varchar(20)", gov: true },
      { name: "approved_by", type: "UUID", fk: "users" },
    ],
  },
  {
    id: "audit_logs",
    label: "audit_logs",
    category: "audit",
    icon: "🔍",
    rowCount: "124K",
    tags: ["Audit", "Compliance", "Immutable"],
    policies: ["P-AUDIT-EXPORT", "P-AUDIT-DENY-LOG"],
    columns: [
      { name: "id", type: "UUID", pk: true },
      { name: "principal_id", type: "UUID", fk: "users" },
      { name: "principal_role", type: "varchar(50)", gov: true },
      { name: "action", type: "varchar(100)", gov: true },
      { name: "resource_type", type: "varchar(50)" },
      { name: "resource_id", type: "varchar(100)" },
      { name: "decision", type: "varchar(20)", gov: true },
      { name: "risk_level", type: "varchar(20)", gov: true },
      { name: "ip_address", type: "varchar(45)" },
      { name: "processing_time_ms", type: "integer" },
    ],
  },
];

const EDGES_DEF = [
  { source: "accounts",     target: "users",    label: "user_id" },
  { source: "transactions", target: "accounts", label: "from/to_account_id" },
  { source: "transactions", target: "users",    label: "processed_by" },
  { source: "loans",        target: "users",    label: "user_id" },
  { source: "audit_logs",   target: "users",    label: "principal_id" },
];

// Manual positions for a clean layout
const POSITIONS: Record<string, { x: number; y: number }> = {
  users:        { x: 340, y: 160 },
  accounts:     { x: 680, y: 20  },
  transactions: { x: 680, y: 340 },
  loans:        { x: 0,   y: 340 },
  audit_logs:   { x: 0,   y: 20  },
};

// ── Graph node component ──────────────────────────────────────────────────────

function GraphTableNode({ data, selected }: { data: any; selected?: boolean }) {
  const t: TableDef = data.table;
  const color = CATEGORY_COLOR[t.category];
  const sensitiveCount = t.columns.filter(c => c.sensitive).length;
  const policyCount = t.policies.length;

  return (
    <div
      className="dg-node"
      style={{ "--node-color": color, boxShadow: selected ? `0 0 0 2px ${color}` : undefined } as any}
    >
      <Handle type="target" position={Position.Left}  style={{ opacity: 0 }} />
      <Handle type="target" position={Position.Top}   style={{ opacity: 0 }} />
      <Handle type="source" position={Position.Right} style={{ opacity: 0 }} />
      <Handle type="source" position={Position.Bottom}style={{ opacity: 0 }} />

      {/* Header */}
      <div className="dg-node-head" style={{ background: color }}>
        <span className="dg-node-icon">{t.icon}</span>
        <span className="dg-node-title">{t.label}</span>
        <div className="dg-node-tags">
          {t.tags.slice(0, 2).map(tag => (
            <span key={tag} className="dg-tag">{tag}</span>
          ))}
        </div>
      </div>

      {/* Columns */}
      <div className="dg-node-cols">
        {t.columns.slice(0, 8).map(col => (
          <div key={col.name} className={`dg-col ${col.sensitive ? "sensitive" : ""} ${col.pk ? "pk" : ""}`}>
            <span className="dg-col-icon">
              {col.pk ? "🔑" : col.fk ? "↗" : col.sensitive ? "⚠" : col.gov ? "⚙" : "○"}
            </span>
            <span className="dg-col-name">{col.name}</span>
            <span className="dg-col-type">{shortType(col.type)}</span>
          </div>
        ))}
        {t.columns.length > 8 && (
          <div className="dg-col-more">+{t.columns.length - 8} more columns</div>
        )}
      </div>

      {/* Footer */}
      <div className="dg-node-foot">
        <span className="dg-stat" title="Applied policy rules">
          <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>
          </svg>
          {policyCount} rule{policyCount !== 1 ? "s" : ""}
        </span>
        {sensitiveCount > 0 && (
          <span className="dg-stat sensitive" title="Sensitive columns">
            <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/>
              <circle cx="12" cy="12" r="3"/>
            </svg>
            {sensitiveCount} sensitive
          </span>
        )}
        <span className="dg-stat rows">~{t.rowCount} rows</span>
      </div>
    </div>
  );
}

function shortType(t: string) {
  return String(t || "").replace(/character varying/i, "varchar").replace(/\(.*\)/, "").trim().slice(0, 10);
}

const NODE_TYPES = { graphTable: GraphTableNode };

// ── Build initial nodes/edges ─────────────────────────────────────────────────

function buildInitial() {
  const nodes = TABLES.map(t => ({
    id: t.id,
    type: "graphTable",
    position: POSITIONS[t.id],
    data: { table: t },
    selected: false,
  }));

  const edges = EDGES_DEF.map((e, i) => ({
    id: `e${i}`,
    source: e.source,
    target: e.target,
    label: e.label,
    type: "smoothstep",
    animated: false,
    markerEnd: { type: MarkerType.ArrowClosed, width: 16, height: 16, color: "#6b7280" },
    style: { stroke: "#9ca3af", strokeWidth: 1.5 },
    labelStyle: { fill: "#6b7280", fontSize: 10 },
    labelBgStyle: { fill: "#ffffff", fillOpacity: 0.85 },
    labelBgPadding: [4, 3] as [number, number],
    labelBgBorderRadius: 4,
  }));

  return { nodes, edges };
}

// ── Detail panel ──────────────────────────────────────────────────────────────

function DetailPanel({ table, onClose }: { table: TableDef; onClose: () => void }) {
  const color = CATEGORY_COLOR[table.category];
  const sensitive = table.columns.filter(c => c.sensitive);
  const fks = table.columns.filter(c => c.fk);

  return (
    <div className="dg-detail">
      <div className="dg-detail-head" style={{ borderColor: color }}>
        <div>
          <div className="dg-detail-title" style={{ color }}>{table.icon} {table.label}</div>
          <div className="dg-detail-sub">~{table.rowCount} rows · {table.columns.length} columns</div>
        </div>
        <button className="dg-detail-close" onClick={onClose}>×</button>
      </div>

      {/* Tags */}
      <div className="dg-detail-tags">
        {table.tags.map(tag => (
          <span key={tag} className="dg-detail-tag">{tag}</span>
        ))}
      </div>

      {/* All columns */}
      <div className="dg-detail-section">
        <div className="dg-detail-section-title">Columns</div>
        <table className="dg-col-table">
          <thead>
            <tr>
              <th>Name</th>
              <th>Type</th>
              <th>Flags</th>
            </tr>
          </thead>
          <tbody>
            {table.columns.map(col => (
              <tr key={col.name} className={col.sensitive ? "sensitive-row" : ""}>
                <td className="dg-col-table-name">
                  {col.pk && <span className="dg-flag pk">PK</span>}
                  {col.fk && <span className="dg-flag fk">FK</span>}
                  {col.name}
                </td>
                <td className="dg-col-table-type">{col.type}</td>
                <td>
                  {col.sensitive && <span className="dg-flag sens">SENSITIVE</span>}
                  {col.gov && !col.sensitive && <span className="dg-flag gov">GOV</span>}
                  {col.restriction && (
                    <span className="dg-restriction" title={col.restriction}>⚠ {col.restriction}</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* FK relationships */}
      {fks.length > 0 && (
        <div className="dg-detail-section">
          <div className="dg-detail-section-title">Foreign Keys</div>
          {fks.map(col => (
            <div key={col.name} className="dg-fk-row">
              <span className="dg-fk-col">{col.name}</span>
              <span className="dg-fk-arrow">→</span>
              <span className="dg-fk-target">{col.fk}.id</span>
            </div>
          ))}
        </div>
      )}

      {/* Sensitive columns */}
      {sensitive.length > 0 && (
        <div className="dg-detail-section">
          <div className="dg-detail-section-title">
            <span style={{ color: "var(--red)" }}>⚠</span> Sensitive Columns ({sensitive.length})
          </div>
          {sensitive.map(col => (
            <div key={col.name} className="dg-sensitive-row">
              <span className="dg-sensitive-name">{col.name}</span>
              {col.restriction && <span className="dg-sensitive-note">{col.restriction}</span>}
            </div>
          ))}
        </div>
      )}

      {/* Applied policy rules */}
      <div className="dg-detail-section">
        <div className="dg-detail-section-title">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>
          </svg>
          Applied Policies ({table.policies.length})
        </div>
        {table.policies.map(pid => {
          const rule = POLICY_RULES[pid];
          if (!rule) return null;
          return (
            <div key={pid} className={`dg-policy-card sev-${rule.severity}`}>
              <div className="dg-policy-head">
                <span className={`dg-policy-badge sev-${rule.severity}`}>{rule.severity.toUpperCase()}</span>
                <span className="dg-policy-id">{rule.reqRef}</span>
              </div>
              <div className="dg-policy-title">{rule.title}</div>
              <div className="dg-policy-desc">{rule.description}</div>
              {rule.roles && (
                <div className="dg-policy-roles">
                  {rule.roles.map(r => <span key={r} className="dg-role-chip">{r}</span>)}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ── Stats bar ─────────────────────────────────────────────────────────────────

function StatsBar() {
  const totalTables = TABLES.length;
  const totalCols = TABLES.reduce((s, t) => s + t.columns.length, 0);
  const sensitiveCols = TABLES.reduce((s, t) => s + t.columns.filter(c => c.sensitive).length, 0);
  const totalPolicies = Object.keys(POLICY_RULES).length;

  return (
    <div className="dg-stats-bar">
      <div className="dg-stat-item">
        <span className="dg-stat-value">{totalTables}</span>
        <span className="dg-stat-label">Tables</span>
      </div>
      <div className="dg-stat-sep" />
      <div className="dg-stat-item">
        <span className="dg-stat-value">{totalCols}</span>
        <span className="dg-stat-label">Columns</span>
      </div>
      <div className="dg-stat-sep" />
      <div className="dg-stat-item">
        <span className="dg-stat-value" style={{ color: "var(--red)" }}>{sensitiveCols}</span>
        <span className="dg-stat-label">Sensitive</span>
      </div>
      <div className="dg-stat-sep" />
      <div className="dg-stat-item">
        <span className="dg-stat-value" style={{ color: "var(--blue)" }}>{totalPolicies}</span>
        <span className="dg-stat-label">Policy Rules</span>
      </div>
      <div className="dg-stat-sep" />
      <div className="dg-stat-item">
        <span className="dg-stat-value" style={{ color: "var(--green)" }}>verified</span>
        <span className="dg-stat-label">KYC status</span>
      </div>
      <div style={{ flex: 1 }} />
      <span className="dg-source-badge">SecureBank · BRD v2.0 · June 2026</span>
    </div>
  );
}

// ── Legend ────────────────────────────────────────────────────────────────────

function Legend() {
  return (
    <div className="dg-legend">
      <div className="dg-legend-item"><span className="dg-legend-icon">🔑</span> Primary key</div>
      <div className="dg-legend-item"><span className="dg-legend-icon">↗</span> Foreign key</div>
      <div className="dg-legend-item"><span className="dg-legend-icon sens">⚠</span> Sensitive</div>
      <div className="dg-legend-item"><span className="dg-legend-icon">⚙</span> Governed</div>
    </div>
  );
}

// ── Main component ────────────────────────────────────────────────────────────

const { nodes: INIT_NODES, edges: INIT_EDGES } = buildInitial();

export default function DataGraph() {
  const [nodes, , onNodesChange] = useNodesState(INIT_NODES);
  const [edges, , onEdgesChange] = useEdgesState(INIT_EDGES);
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const selectedTable = useMemo(
    () => selectedId ? TABLES.find(t => t.id === selectedId) ?? null : null,
    [selectedId]
  );

  const onNodeClick = useCallback((_: any, node: any) => {
    setSelectedId(prev => prev === node.id ? null : node.id);
  }, []);

  const styledNodes = useMemo(() =>
    nodes.map(n => ({ ...n, selected: n.id === selectedId })),
    [nodes, selectedId]
  );

  return (
    <div className="dg-shell">
      <StatsBar />

      <div className="dg-workspace">
        {/* Graph canvas */}
        <div className="dg-canvas">
          <ReactFlow
            nodes={styledNodes}
            edges={edges}
            nodeTypes={NODE_TYPES}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onNodeClick={onNodeClick}
            fitView
            fitViewOptions={{ padding: 0.15 }}
            minZoom={0.3}
            maxZoom={2}
            proOptions={{ hideAttribution: true }}
          >
            <Background color="#e4e7ed" gap={24} size={1} />
            <Controls showInteractive={false} />
          </ReactFlow>
          <Legend />
        </div>

        {/* Detail panel */}
        {selectedTable && (
          <DetailPanel table={selectedTable} onClose={() => setSelectedId(null)} />
        )}
        {!selectedTable && (
          <div className="dg-empty-detail">
            <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" style={{ color: "var(--muted)", marginBottom: 10 }}>
              <rect x="3" y="3" width="7" height="7" rx="1"/>
              <rect x="14" y="3" width="7" height="7" rx="1"/>
              <rect x="14" y="14" width="7" height="7" rx="1"/>
              <rect x="3" y="14" width="7" height="7" rx="1"/>
            </svg>
            <div style={{ fontWeight: 500, color: "var(--ink-soft)", marginBottom: 4 }}>Select a table</div>
            <div style={{ color: "var(--muted)", fontSize: 12, textAlign: "center", maxWidth: 160 }}>
              Click any node in the graph to see columns, relationships, and applied policies.
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
