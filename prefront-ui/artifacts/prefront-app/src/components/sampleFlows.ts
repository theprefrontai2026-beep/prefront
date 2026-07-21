/*
 * Sample intent flows — illustrative sessions that populate the Intent Flows
 * Sankey with varied, governance-accurate activity for the ACTIVE demo. Each
 * entry is a `/api/diff`-shaped decision element; the page posts them to the
 * generic /api/decisions endpoint (one fresh session id per array), so the
 * engine stays domain-neutral — this demo vocabulary lives only in the UI layer.
 *
 * `sampleSessions(demo)` dispatches to the per-demo builder. Outcomes mirror
 * what each published policy actually produces.
 */

import type { DemoId } from "../demos";

type Kind = "allow" | "mask" | "block" | "approval";
interface Caller { caller: string; role: string; }

// ── SecureBank ──────────────────────────────────────────────────────────────
function securebankSessions() {
  const MARIA: Caller = { caller: "Maria Lopez", role: "Account Holder" };
  const SAM: Caller = { caller: "Sam Carter", role: "Account Holder" };
  const TOM: Caller = { caller: "Tom Reed", role: "Bank Teller" };
  const PRIYA: Caller = { caller: "Priya Shah", role: "Bank Manager" };

  const REASON: Record<string, string> = {
    view_account_owner_only: "view_account_owner_only: OWN_DATA_ONLY: account holders may only view their own accounts.",
    role_not_permitted: "role_not_permitted: this role is not allowed to call this intent.",
    transfer_ceiling: "transfer_ceiling: Transfer exceeds the $250,000 hard ceiling.",
    transfer_requires_approval: "transfer_requires_approval: Transfers above $10,000 require Bank Manager approval.",
  };
  const MASK_GOV = { rules_evaluated: [{
    fired: true, rule_key: "ssn_manager_only", rule_type: "data_access", decision: "mask",
    reason: "MANAGER_ONLY_FIELD: ssn is visible to Bank Managers only.",
  }] };

  let seq = 0;
  function step(c: Caller, intent: string, args: Record<string, any>, kind: Kind, policy?: string) {
    const g: any = { intent, args };
    if (kind === "allow") { g.status = "allowed"; g.outcome = "ALLOW (scoped to caller)"; }
    else if (kind === "mask") { g.status = "allowed"; g.outcome = "ALLOW (fields masked)"; g.masked_fields = ["ssn"]; g.governance = MASK_GOV; }
    else if (kind === "block") { g.status = "blocked"; g.outcome = "BLOCK (policy)"; g.reasons = policy ? [REASON[policy]] : []; }
    else { g.status = "approval_required"; g.outcome = "APPROVAL (policy)"; g.approver_roles = ["Bank Manager"]; g.reasons = [REASON.transfer_requires_approval]; }
    return { id: `SAMPLE-${++seq}`, caller: c.caller, role: c.role, capability: "Sample", governed: g };
  }
  const tx = (acct: number, amount: number) => ({ account_id: acct, amount, counterparty_account: 5005 });

  return [
    // Teller morning shift — staff lookups, a gated transfer, a role block; manager alongside.
    [
      step(TOM, "view_users", {}, "mask"),
      step(TOM, "view_user", { user_id: 1 }, "mask"),
      step(TOM, "view_account", { account_id: 1042 }, "allow"),
      step(TOM, "initiate_transfer", tx(1042, 75000), "approval"),
      step(TOM, "decide_loan", { loan_id: 7001, decision: "approved" }, "block", "role_not_permitted"),
      step(PRIYA, "view_users", {}, "allow"),
      step(PRIYA, "decide_loan", { loan_id: 7001, decision: "approved" }, "allow"),
      step(PRIYA, "initiate_transfer", tx(1042, 120000), "approval"),
    ],
    // Account holders self-serve — ownership scoping (sam owns 1042, maria doesn't).
    [
      step(MARIA, "view_accounts", {}, "allow"),
      step(MARIA, "view_account", { account_id: 1042 }, "block", "view_account_owner_only"),
      step(MARIA, "initiate_transfer", tx(1001, 500), "allow"),
      step(SAM, "view_accounts", {}, "allow"),
      step(SAM, "view_account", { account_id: 1042 }, "allow"),
      step(SAM, "view_users", {}, "block", "role_not_permitted"),
      step(SAM, "initiate_transfer", tx(1042, 2000), "allow"),
    ],
    // Manager review — full reads + loan decisions; a teller hits the ceiling.
    [
      step(PRIYA, "view_user", { user_id: 1 }, "allow"),
      step(PRIYA, "view_users", {}, "allow"),
      step(PRIYA, "decide_loan", { loan_id: 7003, decision: "approved" }, "allow"),
      step(PRIYA, "decide_loan", { loan_id: 7001, decision: "approved" }, "allow"),
      step(TOM, "view_user", { user_id: 2 }, "mask"),
      step(TOM, "initiate_transfer", tx(1042, 300000), "block", "transfer_ceiling"),
    ],
    // Afternoon mix — a large-transfer approval, more role blocks and masks.
    [
      step(SAM, "view_accounts", {}, "allow"),
      step(SAM, "initiate_transfer", tx(1042, 50000), "approval"),
      step(SAM, "view_account", { account_id: 5005 }, "allow"),
      step(MARIA, "view_accounts", {}, "allow"),
      step(MARIA, "view_users", {}, "block", "role_not_permitted"),
      step(TOM, "view_users", {}, "mask"),
      step(TOM, "view_user", { user_id: 1 }, "mask"),
      step(TOM, "decide_loan", { loan_id: 7002, decision: "approved" }, "block", "role_not_permitted"),
    ],
  ];
}

// ── LoanPro ───────────────────────────────────────────────────────────────
function loanproSessions() {
  const OLIVIA: Caller = { caller: "Olivia Reed", role: "Loan Officer" };
  const UMA: Caller = { caller: "Uma Patel", role: "Underwriter" };
  const MARTIN: Caller = { caller: "Martin Cole", role: "Branch Manager" };

  // Credit-policy reasons keyed by the rule that fired (block or approval).
  const REASON: Record<string, string> = {
    decide_loan_credit_floor: "decide_loan_credit_floor: Credit score below 580 (deep subprime) — loan cannot be approved.",
    decide_loan_unresolved_default: "decide_loan_unresolved_default: Applicant has an unresolved default on file — loan cannot be approved.",
    decide_loan_income_multiple: "decide_loan_income_multiple: Requested amount exceeds 5× the applicant's annual income.",
    decide_loan_ceiling: "decide_loan_ceiling: Loan exceeds the $1,000,000 origination ceiling and cannot be approved here.",
    decide_loan_requires_approval: "decide_loan_requires_approval: Loans above $50,000 require Branch Manager approval.",
    decide_loan_low_score_review: "decide_loan_low_score_review: Credit score under 640 (near prime) — manual review required.",
    decide_loan_recent_default_review: "decide_loan_recent_default_review: A default within the last 12 months — manual review required.",
  };
  // Loan Officers see ssn AND the raw credit score masked.
  const MASK_GOV = { rules_evaluated: [
    { fired: true, rule_key: "ssn_manager_only", rule_type: "data_access", decision: "mask",
      reason: "MANAGER_ONLY_FIELD: ssn is visible to Branch Managers only." },
    { fired: true, rule_key: "credit_score_officer_masked", rule_type: "data_access", decision: "mask",
      reason: "UNDERWRITER_ONLY_FIELD: the raw credit score is masked from Loan Officers." },
  ] };
  const decideGov = (policy: string, decision: string) => ({ rules_evaluated: [
    { fired: true, rule_key: policy, rule_type: policy.includes("review") || policy.includes("approval") ? "approval_threshold" : "restriction", decision, reason: REASON[policy] },
  ] });

  let seq = 0;
  function step(c: Caller, intent: string, args: Record<string, any>, kind: Kind, policy?: string) {
    const g: any = { intent, args };
    if (kind === "allow") { g.status = "allowed"; g.outcome = "ALLOW (scoped to caller)"; }
    else if (kind === "mask") { g.status = "allowed"; g.outcome = "ALLOW (fields masked)"; g.masked_fields = ["ssn", "credit_score"]; g.governance = MASK_GOV; }
    else if (kind === "block") { g.status = "blocked"; g.outcome = "BLOCK (policy)"; g.reasons = policy ? [REASON[policy]] : []; if (policy) g.governance = decideGov(policy, "block"); }
    else { g.status = "approval_required"; g.outcome = "APPROVAL (policy)"; g.approver_roles = ["Branch Manager"]; g.reasons = policy ? [REASON[policy]] : []; if (policy) g.governance = decideGov(policy, "approval_required"); }
    return { id: `SAMPLE-${++seq}`, caller: c.caller, role: c.role, capability: "Sample", governed: g };
  }
  const dl = (loan: number) => ({ loan_id: loan, decision: "approved" });

  return [
    // Underwriting desk — a spread of credit-policy outcomes on the loan decision.
    [
      step(UMA, "decide_loan", dl(7001), "allow"),
      step(UMA, "decide_loan", dl(7003), "approval", "decide_loan_low_score_review"),
      step(UMA, "decide_loan", dl(7004), "block", "decide_loan_credit_floor"),
      step(UMA, "decide_loan", dl(7005), "block", "decide_loan_unresolved_default"),
      step(UMA, "decide_loan", dl(7002), "approval", "decide_loan_requires_approval"),
    ],
    // Risk limits — affordability and the hard ceiling block; the manager clears the queued approval.
    [
      step(UMA, "decide_loan", dl(7006), "block", "decide_loan_income_multiple"),
      step(UMA, "decide_loan", dl(7007), "block", "decide_loan_ceiling"),
      step(UMA, "decide_loan", dl(7008), "approval", "decide_loan_recent_default_review"),
      step(MARTIN, "decide_loan", dl(7002), "allow"),
    ],
    // Officer intake — pipeline review + applicant lookups (ssn + score masked for the officer).
    [
      step(OLIVIA, "view_applications", {}, "allow"),
      step(OLIVIA, "view_applicant", { name_query: "Carol Davis" }, "mask"),
      step(OLIVIA, "view_applicant", { name_query: "Ben Torres" }, "mask"),
    ],
    // Manager review — unmasked reads then a clean decision.
    [
      step(MARTIN, "view_applicants", {}, "allow"),
      step(MARTIN, "view_applicant", { name_query: "Aisha Khan" }, "allow"),
      step(MARTIN, "decide_loan", dl(7001), "allow"),
    ],
  ];
}

/** One array per session; each is posted under a single fresh session id. */
export function sampleSessions(demo: DemoId) {
  return demo === "loanpro" ? loanproSessions() : securebankSessions();
}
