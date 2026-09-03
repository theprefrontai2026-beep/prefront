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

import { APPLICATIONS, DEFAULT_APP, type AppId, type AppIdentity } from "@apps";

// The IDENTITY half of a demo — id, label, tagline, Phoenix project,
// orchestrator URL, sensitive fields — now comes from the shared application
// registry (lib/apps/registry.ts), because Verdict needs exactly those and a
// second copy of them cannot be allowed to drift: an id or project mismatch
// scopes a read to an application that does not exist and renders a confident
// empty page. Everything below is the PRESENTATION half, which only this app
// has surfaces for.
export type DemoId = AppId;

export interface DemoConfig extends AppIdentity {
  blurb: string;        // a sentence of context for the chooser
  accent: string;       // card/pill accent color
  glyph: string;        // short badge glyph (emoji)
  scenarioCount: number;

  // Data Connector defaults (prefills for connecting this demo's datasource).
  // One app may have several datasources; this is the one the Data Connector
  // prefills. The full per-datasource registry (with each one's inline/oob
  // mode) lands with the phase that consumes it.
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

  // Each role fronts a different agent surface in the demo's story.
  roleAgents: Record<string, string>;

  // Fallback approver shown when a decision routes for approval but names no role.
  defaultApprover: string;

  // Scenario ids the "Populate from the demo" control runs, or undefined to run
  // the whole catalogue. A full run is one live LLM session per scenario —
  // doubled for a demo with a governed lane — so a large catalogue must name a
  // representative subset or the request times out before inserting anything.
  // Demo vocabulary, so it lives HERE rather than in the api-server.
  populateScenarios?: string[];
}

// Each entry MERGES its shared identity (id/label/tagline/phoenixProject/
// orchestratorUrl/sensitiveFields) with this app's presentation fields, so
// there is exactly one definition of the identity half.
const identity = (id: DemoId): AppIdentity =>
  APPLICATIONS.find((a) => a.id === id)!;

export const DEMOS: DemoConfig[] = [
  {
    ...identity("securebank"),
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
    roleAgents: {
      "Account Holder": "Customer Assistant",
      "Bank Teller": "Teller Copilot",
      "Bank Manager": "Manager Console",
    },
    defaultApprover: "Bank Manager",
  },
  {
    ...identity("loanpro"),
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
    // One scenario per governed OUTCOME, measured rather than assumed, so the
    // populated store shows a range instead of one verdict repeated:
    //   F1-04  mask    — ungoverned leaks ssn/tax_id/bank hint/credit score to
    //                    a Loan Officer; governed masks all four.
    //   F3-02  block   — an Applicant invokes an intent no Applicant may call.
    //   BASE-03 allow  — a clean scoped read, which also exercises upstream
    //                    identity forwarding (the caller's own pipeline only).
    // No approval_required entry: that rule keys on quote_terms.amount /
    // amend_application.requested_amount above $50,000 and every quote in the
    // catalogue is $25k–$35k, so no existing scenario reaches it. The path
    // works (a $60k quote_terms returns approval_required); the catalogue just
    // never asks for one. Adding a scenario is a catalogue change, not a UI one.
    populateScenarios: ["F1-04", "F3-02", "BASE-03"],
    roleAgents: {
      "Applicant": "Borrower Portal",
      "Loan Officer": "Officer Workbench",
      "Underwriter": "Underwriting Copilot",
      "Branch Manager": "Manager Console",
    },
    defaultApprover: "Branch Manager",
  },
];

// Derived, never restated: the two front-ends must open on the same app.
export const DEFAULT_DEMO: DemoId = DEFAULT_APP;

// Falls back to DEFAULT_DEMO, not DEMOS[0] — an unrecognised id (a stale
// localStorage value, or a bad ?demo= in a shared link) must land on the
// demo the rest of the app defaults to, not silently on a different one.
export function getDemo(id: string | null | undefined): DemoConfig {
  return DEMOS.find((d) => d.id === id) ?? DEMOS.find((d) => d.id === DEFAULT_DEMO) ?? DEMOS[0];
}
