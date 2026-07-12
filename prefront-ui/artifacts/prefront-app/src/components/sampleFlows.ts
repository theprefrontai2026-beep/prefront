/*
 * SecureBank sample intent flows — illustrative sessions that populate the
 * Intent Flows Sankey with varied, governance-accurate activity across all four
 * callers (Account Holders maria & sam, Teller tom, Manager priya). Each entry
 * is a `/api/diff`-shaped decision element; the page posts them to the generic
 * /api/decisions endpoint (one fresh session id per array), so the engine stays
 * domain-neutral — this SecureBank vocabulary lives only in the demo UI layer.
 *
 * Outcomes mirror what the published SecureBank policy actually produces:
 * account holders may only read their own accounts, only staff may view_users
 * (ssn masked off-manager), transfers > $10k need approval / > $250k are
 * blocked, and only managers may decide_loan.
 */

type Kind = "allow" | "mask" | "block" | "approval";
interface Caller { caller: string; role: string; }
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
const MASK_GOV = {
  rules_evaluated: [{
    fired: true, rule_key: "ssn_manager_only", rule_type: "data_access", decision: "mask",
    reason: "MANAGER_ONLY_FIELD: ssn is visible to Bank Managers only.",
  }],
};

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

/** One array per session; each is posted under a single fresh session id. */
export function sampleSessions() {
  seq = 0;
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
