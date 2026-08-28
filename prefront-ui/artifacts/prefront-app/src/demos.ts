/*
 * Demo registry — the single source of truth for "which worked example am I
 * walking through." The engine is domain-neutral; every piece of demo-specific
 * vocabulary the SPA needs (default datasource id, the sensitive fields to
 * flag, the role→agent-surface names, sample intent flows) lives HERE, keyed
 * by demo, instead of hardcoded across components.
 *
 * Each bundled demo ships its own stack in docker-compose.yaml (a Postgres, a
 * seed job, a Prefront MCP server, an app-layer "before" service, and an
 * orchestrator — the orchestrator itself is what Verdict and the demo's own
 * scenario/diff CLIs point at, prefront-app has no Runtime tab any more).
 * `id` is what the api-server uses to scope persisted decision traces per demo.
 */

export type DemoId = "securebank" | "loanpro";

export interface DemoConfig {
  id: DemoId;
  label: string;        // display name, e.g. "SecureBank"
  tagline: string;      // one-liner for the chooser card + pill
  blurb: string;        // a sentence of context for the chooser
  accent: string;       // card/pill accent color
  glyph: string;        // short badge glyph (emoji)
  scenarioCount: number;

  // Data Connector defaults (prefills for connecting this demo's datasource).
  datasourceId: string;
  ddlPlaceholder: string;
  // Prefilled (not just a placeholder — an actual editable default value) MCP
  // server URL for the MCP Server connect tab, when this demo ships a plain,
  // ungoverned app-tool MCP server worth connecting to directly. Omit when the
  // demo has no such target (e.g. SecureBank's MCP server is itself Prefront's
  // governed runtime, not a raw app API).
  defaultMcpServerUrl?: string;

  // Semantic / Policy Studio defaults.
  defaultMetrics: string;
  defaultCallerScope: string;

  // Fields the runtime treats as sensitive — flagged in the diff even when the
  // ungoverned side leaks them; the governed side masks them.
  sensitiveFields: string[];

  // Each role fronts a different agent surface in the demo's story.
  roleAgents: Record<string, string>;

  // Fallback approver shown when a decision routes for approval but names no role.
  defaultApprover: string;
}

export const DEMOS: DemoConfig[] = [
  {
    id: "securebank",
    label: "SecureBank",
    tagline: "Retail banking — accounts, transfers, loans",
    blurb:
      "A bank assistant over customer accounts. Watch ownership, SSN masking, transfer approvals, and role limits enforced deterministically.",
    accent: "#2563eb",
    glyph: "🏦",
    scenarioCount: 8,
    datasourceId: "securebank",
    ddlPlaceholder:
      "CREATE TABLE users (\n  user_id INT PRIMARY KEY,\n  name TEXT,\n  role TEXT,\n  ssn TEXT\n);\n\nCREATE TABLE accounts (\n  account_id INT PRIMARY KEY,\n  user_id INT REFERENCES users(user_id),\n  balance NUMERIC,\n  status TEXT\n);",
    defaultMetrics:
      "available_credit = credit_limit - current_balance\n" +
      "credit_utilization_pct = current_balance / credit_limit * 100",
    defaultCallerScope: "region = region_id\nrep_id = rep_id",
    sensitiveFields: ["ssn"],
    roleAgents: {
      "Account Holder": "Customer Assistant",
      "Bank Teller": "Teller Copilot",
      "Bank Manager": "Manager Console",
    },
    defaultApprover: "Bank Manager",
  },
  {
    id: "loanpro",
    label: "LoanPro",
    tagline: "Loan origination — applicants, credit, decisions",
    blurb:
      "An ungoverned loan-origination agent with tools over MCP. Its sessions are built to exhibit every failure mode Prefront's out-of-band checks detect — provenance, policy, and intent conformance.",
    accent: "#7c3aed",
    glyph: "💳",
    scenarioCount: 34,
    datasourceId: "loanpro",
    ddlPlaceholder:
      "CREATE TABLE users (\n  user_id INT PRIMARY KEY,\n  name TEXT,\n  role TEXT,\n  ssn TEXT\n);\n\nCREATE TABLE loan_applications (\n  loan_id INT PRIMARY KEY,\n  applicant_id INT,\n  requested_amount NUMERIC,\n  status TEXT\n);",
    // The demo's own plain, ungoverned app-tool MCP server (loanpro-app-mcp) —
    // reachable at this address from other containers on the compose network
    // (not "localhost", which from Prefront's own containers means themselves).
    defaultMcpServerUrl: "http://loanpro-app-mcp:8102/sse",
    defaultMetrics:
      "dti_ratio = requested_amount / annual_income\n" +
      "loan_to_income_pct = requested_amount / annual_income * 100",
    defaultCallerScope: "officer_id = assigned_officer",
    sensitiveFields: ["ssn", "tax_id", "bank_account_hint", "credit_score", "internal_risk_score"],
    roleAgents: {
      "Applicant": "Borrower Portal",
      "Loan Officer": "Officer Workbench",
      "Underwriter": "Underwriting Copilot",
      "Branch Manager": "Manager Console",
    },
    defaultApprover: "Branch Manager",
  },
];

// LoanPro is the active demo — SecureBank is profile-disabled in docker-compose.
export const DEFAULT_DEMO: DemoId = "loanpro";

export function getDemo(id: string | null | undefined): DemoConfig {
  return DEMOS.find((d) => d.id === id) ?? DEMOS[0];
}
