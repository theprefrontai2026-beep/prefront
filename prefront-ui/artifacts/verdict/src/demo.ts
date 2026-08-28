/*
 * Verdict runs exactly one demo: LoanPro. Unlike the main Prefront app's
 * demos.ts (which switches between SecureBank's governed/ungoverned diff and
 * LoanPro's session runner), Verdict only ever needs SessionRunner's three
 * fields — so this is a trimmed local config, not the full DemoConfig
 * interface. Values copied from prefront-app/src/demos.ts's "loanpro" entry.
 */

export type DemoConfig = {
  label: string;
  orchestratorUrl: string;
  sensitiveFields: string[];
};

export const LOANPRO: DemoConfig = {
  label: "LoanPro",
  orchestratorUrl: "http://localhost:8098",
  sensitiveFields: ["ssn", "tax_id", "bank_account_hint", "credit_score", "internal_risk_score"],
};
