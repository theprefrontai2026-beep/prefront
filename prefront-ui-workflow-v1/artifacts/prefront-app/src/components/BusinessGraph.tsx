import { useMemo, useState } from "react";
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

// ── Domain palette ────────────────────────────────────────────────────────────
const DOMAIN: Record<string, { bg: string; border: string; text: string; badge: string }> = {
  identity:   { bg: "#ede9fe", border: "#4f46e5", text: "#3730a3", badge: "#4f46e5" },
  financial:  { bg: "#e0f2fe", border: "#0891b2", text: "#0e7490", badge: "#0891b2" },
  credit:     { bg: "#fef3c7", border: "#d97706", text: "#92400e", badge: "#d97706" },
  compliance: { bg: "#fce7f3", border: "#db2777", text: "#9d174d", badge: "#db2777" },
  operations: { bg: "#dcfce7", border: "#16a34a", text: "#15803d", badge: "#16a34a" },
  governance: { bg: "#f1f5f9", border: "#64748b", text: "#334155", badge: "#64748b" },
};

// ── Stub data (SecureBank BRD) ────────────────────────────────────────────────

interface BizEntity {
  id: string;
  kind: "entity" | "process" | "role" | "governance";
  label: string;
  icon: string;
  domain: string;
  description: string;
  tables: string[];
  policies: string[];
  roles: string[];
  triggers?: string;
  output?: string;
}

const BIZ_NODES: BizEntity[] = [
  // ── Roles ──────────────────────────────────────────────────────────────────
  {
    id: "role-ah",
    kind: "role",
    label: "Account Holder",
    icon: "👤",
    domain: "identity",
    description: "End-customer who owns accounts and initiates transactions. Subject to KYC and transfer-limit policies.",
    tables: ["users", "accounts"],
    policies: ["FR-USER-1", "FR-TXN-2"],
    roles: [],
  },
  {
    id: "role-teller",
    kind: "role",
    label: "Bank Teller",
    icon: "🏦",
    domain: "operations",
    description: "Front-office staff who process transactions, open accounts, and handle day-to-day banking operations.",
    tables: ["accounts", "transactions"],
    policies: ["FR-ACCT-1", "FR-TXN-3"],
    roles: [],
  },
  {
    id: "role-manager",
    kind: "role",
    label: "Bank Manager",
    icon: "👔",
    domain: "operations",
    description: "Approves high-value transactions (>$50k), loan decisions, and exception requests. Full data access.",
    tables: ["users", "accounts", "loans", "transactions"],
    policies: ["FR-LOAN-3", "FR-TXN-4", "FR-ACCT-2"],
    roles: [],
  },
  {
    id: "role-compliance",
    kind: "role",
    label: "Compliance Officer",
    icon: "🔍",
    domain: "compliance",
    description: "Reviews AML alerts, KYC documentation, and audit logs. Responsible for regulatory reporting.",
    tables: ["audit_logs", "users"],
    policies: ["FR-KYC-1", "FR-AML-1", "FR-AML-2"],
    roles: [],
  },
  {
    id: "role-risk",
    kind: "role",
    label: "Risk Analyst",
    icon: "📊",
    domain: "credit",
    description: "Evaluates credit risk, sets loan limits, and monitors portfolio exposure. Access to credit scoring data.",
    tables: ["loans", "users"],
    policies: ["FR-LOAN-1", "FR-LOAN-2"],
    roles: [],
  },

  // ── Core business entities ──────────────────────────────────────────────────
  {
    id: "ent-customer",
    kind: "entity",
    label: "Customer",
    icon: "👤",
    domain: "identity",
    description: "An individual or legal entity with a banking relationship with SecureBank. Must pass KYC before account opening. PII fields (SSN, DOB) governed by FR-USER-1.",
    tables: ["users"],
    policies: ["FR-USER-1", "FR-KYC-1"],
    roles: ["Account Holder", "Bank Manager"],
    triggers: "Account opening, loan application",
    output: "KYC status, risk profile",
  },
  {
    id: "ent-account",
    kind: "entity",
    label: "Account",
    icon: "🏦",
    domain: "financial",
    description: "A deposit or current account held by a customer. Balance and credit-limit fields are sensitive. Maker-checker required for high-value modifications (FR-ACCT-2).",
    tables: ["accounts"],
    policies: ["FR-ACCT-1", "FR-ACCT-2", "FR-ACCT-3"],
    roles: ["Account Holder", "Bank Teller", "Bank Manager"],
    triggers: "Deposit, withdrawal, transfer",
    output: "Balance updates, transaction records",
  },
  {
    id: "ent-transaction",
    kind: "entity",
    label: "Transaction",
    icon: "💸",
    domain: "financial",
    description: "A financial transfer between accounts. Subject to daily-limit rules for Account Holders ($5,000) and AML screening above $10,000. Immutable once settled.",
    tables: ["transactions"],
    policies: ["FR-TXN-2", "FR-TXN-3", "FR-AML-1"],
    roles: ["Account Holder", "Bank Teller", "Bank Manager"],
    triggers: "Transfer request, scheduled payment",
    output: "Settlement record, AML alert (if triggered)",
  },
  {
    id: "ent-loan",
    kind: "entity",
    label: "Loan",
    icon: "📋",
    domain: "credit",
    description: "A credit product (personal, mortgage, or overdraft) issued to a customer. Requires credit score ≥ 650, income verification, and Bank Manager approval for amounts >$50k.",
    tables: ["loans"],
    policies: ["FR-LOAN-1", "FR-LOAN-2", "FR-LOAN-3"],
    roles: ["Bank Manager", "Risk Analyst"],
    triggers: "Loan application, credit assessment",
    output: "Approval decision, repayment schedule",
  },

  // ── Business processes ──────────────────────────────────────────────────────
  {
    id: "proc-kyc",
    kind: "process",
    label: "KYC Verification",
    icon: "✅",
    domain: "compliance",
    description: "Identity verification required before account opening. Collects government-issued ID, proof of address, and biometric data. Failure blocks all banking access. (FR-KYC-1, FR-KYC-2)",
    tables: ["users", "audit_logs"],
    policies: ["FR-KYC-1", "FR-KYC-2"],
    roles: ["Compliance Officer"],
    triggers: "New customer onboarding",
    output: "KYC status: verified / pending / rejected",
  },
  {
    id: "proc-aml",
    kind: "process",
    label: "AML Screening",
    icon: "🔎",
    domain: "compliance",
    description: "Anti-money laundering check triggered automatically on transactions ≥ $10,000 or matching suspicious patterns. Generates SAR (Suspicious Activity Report) when flagged. (FR-AML-1, FR-AML-2)",
    tables: ["transactions", "audit_logs"],
    policies: ["FR-AML-1", "FR-AML-2"],
    roles: ["Compliance Officer"],
    triggers: "Transaction ≥ $10,000, pattern match",
    output: "AML alert, SAR filing",
  },
  {
    id: "proc-credit",
    kind: "process",
    label: "Credit Assessment",
    icon: "📈",
    domain: "credit",
    description: "Risk evaluation before loan issuance. Checks credit score (≥ 650 threshold), debt-to-income ratio, employment verification, and existing loan exposure. (FR-LOAN-1, FR-LOAN-2)",
    tables: ["users", "loans"],
    policies: ["FR-LOAN-1", "FR-LOAN-2"],
    roles: ["Risk Analyst", "Bank Manager"],
    triggers: "Loan application received",
    output: "Risk score, approval recommendation",
  },
  {
    id: "proc-onboard",
    kind: "process",
    label: "Account Opening",
    icon: "📂",
    domain: "operations",
    description: "End-to-end process to open a new account: KYC pass → product selection → account creation → initial deposit. All steps recorded to audit trail. (FR-ACCT-1)",
    tables: ["users", "accounts", "audit_logs"],
    policies: ["FR-ACCT-1", "FR-KYC-1"],
    roles: ["Bank Teller", "Compliance Officer"],
    triggers: "Customer request + KYC verified",
    output: "New account record",
  },

  // ── Governance ──────────────────────────────────────────────────────────────
  {
    id: "gov-audit",
    kind: "governance",
    label: "Audit Trail",
    icon: "📜",
    domain: "governance",
    description: "Immutable, tamper-evident log of all business events: logins, transactions, approvals, KYC updates, and policy changes. Retained for 7 years per regulatory requirement. (FR-AUDIT-1)",
    tables: ["audit_logs"],
    policies: ["FR-AUDIT-1"],
    roles: ["Compliance Officer", "Bank Manager"],
  },
  {
    id: "gov-policy",
    kind: "governance",
    label: "Policy Engine",
    icon: "⚙️",
    domain: "governance",
    description: "Runtime enforcement layer that applies BRD governance rules to every data access and business action. Intercepts queries, checks role/context, applies masking or rejection as defined by approved policies.",
    tables: [],
    policies: ["FR-USER-1", "FR-TXN-2", "FR-LOAN-3", "FR-KYC-1", "FR-AML-1"],
    roles: ["All roles"],
  },
];

// ── Graph edges (business relationships) ─────────────────────────────────────
interface BizEdge {
  id: string;
  source: string;
  target: string;
  label: string;
  style?: "flow" | "permission" | "governance";
}

const BIZ_EDGES: BizEdge[] = [
  // Role → entity permissions
  { id: "e1",  source: "role-ah",         target: "ent-customer",   label: "is",           style: "permission" },
  { id: "e2",  source: "role-ah",         target: "ent-account",    label: "owns",         style: "permission" },
  { id: "e3",  source: "role-ah",         target: "ent-transaction", label: "initiates",   style: "permission" },
  { id: "e4",  source: "role-teller",     target: "ent-account",    label: "manages",      style: "permission" },
  { id: "e5",  source: "role-teller",     target: "ent-transaction", label: "processes",   style: "permission" },
  { id: "e6",  source: "role-manager",    target: "ent-loan",       label: "approves",     style: "permission" },
  { id: "e7",  source: "role-compliance", target: "proc-kyc",       label: "conducts",     style: "permission" },
  { id: "e8",  source: "role-compliance", target: "proc-aml",       label: "reviews",      style: "permission" },
  { id: "e9",  source: "role-risk",       target: "proc-credit",    label: "performs",     style: "permission" },
  // Business flow
  { id: "e10", source: "ent-customer",    target: "proc-kyc",       label: "undergoes",    style: "flow" },
  { id: "e11", source: "proc-kyc",        target: "proc-onboard",   label: "clears",       style: "flow" },
  { id: "e12", source: "proc-onboard",    target: "ent-account",    label: "creates",      style: "flow" },
  { id: "e13", source: "ent-account",     target: "ent-transaction", label: "funds",       style: "flow" },
  { id: "e14", source: "ent-transaction", target: "proc-aml",       label: "triggers",     style: "flow" },
  { id: "e15", source: "ent-customer",    target: "ent-loan",       label: "applies for",  style: "flow" },
  { id: "e16", source: "ent-loan",        target: "proc-credit",    label: "requires",     style: "flow" },
  // Governance
  { id: "e17", source: "proc-kyc",        target: "gov-audit",      label: "records",      style: "governance" },
  { id: "e18", source: "proc-aml",        target: "gov-audit",      label: "records",      style: "governance" },
  { id: "e19", source: "proc-credit",     target: "gov-audit",      label: "records",      style: "governance" },
  { id: "e20", source: "ent-transaction", target: "gov-audit",      label: "records",      style: "governance" },
  { id: "e21", source: "gov-policy",      target: "proc-kyc",       label: "governs",      style: "governance" },
  { id: "e22", source: "gov-policy",      target: "proc-aml",       label: "governs",      style: "governance" },
  { id: "e23", source: "gov-policy",      target: "ent-transaction", label: "governs",     style: "governance" },
];

// ── Node positions ────────────────────────────────────────────────────────────
const POSITIONS: Record<string, { x: number; y: number }> = {
  // Roles row (top)
  "role-ah":         { x: 40,   y: 20  },
  "role-teller":     { x: 260,  y: 20  },
  "role-manager":    { x: 480,  y: 20  },
  "role-compliance": { x: 700,  y: 20  },
  "role-risk":       { x: 920,  y: 20  },
  // Entities row
  "ent-customer":    { x: 40,   y: 175 },
  "ent-account":     { x: 300,  y: 175 },
  "ent-transaction": { x: 560,  y: 175 },
  "ent-loan":        { x: 820,  y: 175 },
  // Processes row
  "proc-kyc":        { x: 40,   y: 400 },
  "proc-onboard":    { x: 270,  y: 400 },
  "proc-aml":        { x: 560,  y: 400 },
  "proc-credit":     { x: 820,  y: 400 },
  // Governance row (bottom)
  "gov-audit":       { x: 320,  y: 600 },
  "gov-policy":      { x: 680,  y: 600 },
};

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
            BRD Policy References ({node.policies.length})
          </div>
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
function StatsBar() {
  const entityCount  = BIZ_NODES.filter(n => n.kind === "entity").length;
  const processCount = BIZ_NODES.filter(n => n.kind === "process").length;
  const roleCount    = BIZ_NODES.filter(n => n.kind === "role").length;
  const policyCount  = new Set(BIZ_NODES.flatMap(n => n.policies)).size;

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
        <span className="dg-stat-label">BRD REFS</span>
      </div>
      <div className="dg-stat-sep" />
      <div className="dg-stat-item">
        <span className="dg-stat-value" style={{ color: "var(--green)", fontWeight: 700 }}>verified</span>
        <span className="dg-stat-label">KYC STATUS</span>
      </div>
      <div style={{ marginLeft: "auto" }}>
        <span style={{ background: "#f8fafc", border: "1px solid #e2e8f0", color: "#64748b", borderRadius: 6, padding: "3px 10px", fontSize: 11 }}>
          SecureBank · Business Domain Model · BRD v2.0
        </span>
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
export default function BusinessGraph() {
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const initialNodes = useMemo(() =>
    BIZ_NODES.map(n => ({
      id: n.id,
      type: n.kind === "role" ? "bizRole" : n.kind === "entity" ? "bizEntity" : n.kind === "process" ? "bizProcess" : "bizGovernance",
      position: POSITIONS[n.id] ?? { x: 0, y: 0 },
      data: { ...n, selected: n.id === selectedId },
    })),
  [selectedId]);

  const initialEdges = useMemo(() => BIZ_EDGES.map(makeEdge), []);

  const [nodes, , onNodesChange]   = useNodesState(initialNodes);
  const [edges, , onEdgesChange]   = useEdgesState(initialEdges);

  const selectedNode = useMemo(() =>
    selectedId ? BIZ_NODES.find(n => n.id === selectedId) ?? null : null,
  [selectedId]);

  return (
    <div className="dg-shell">
      <StatsBar />

      <div className="dg-workspace">
        <div className="dg-canvas">
          <ReactFlow
            nodes={nodes}
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
            <div className="dg-detail-empty-sub">Click any entity, process, role, or governance node to see its description, mapped tables, and applied BRD policies.</div>
          </div>
        )}
      </div>
    </div>
  );
}
