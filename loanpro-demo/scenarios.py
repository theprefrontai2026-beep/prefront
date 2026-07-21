"""LoanPro before/after scenarios — the single source of truth shared by the
ungoverned baseline runner and the governed runner, so the two runs are provably
the same requests. Mirrors securebank-demo/scenarios.py for the loan domain.

Each scenario carries:
  id, caller        — which identity makes the request (see CALLERS)
  capability        — the Prefront capability it exercises
  question          — the natural-language request (what a UI/agent demo shows)
  app_tool, app_args— the typed business function an UNGOVERNED agent would call
  intent, args      — the approved Prefront intent + explicit args the GOVERNED runner uses
  risk              — what goes wrong without Prefront
  prefront          — the governed outcome (ALLOW / BLOCK / MASK / APPROVAL)
"""

from __future__ import annotations

# Callers the harness can act as. `user_id` is the owned-data key; `role` drives
# role policy. Applicants own their own application; the Loan Officer owns an
# assigned pipeline; Underwriter/Branch Manager decide loans.
CALLERS = {
    "aisha":  {"name": "Aisha Khan",  "email": "aisha.khan@loanpro.example",
               "role": "Applicant",      "user_id": 1},
    "ben":    {"name": "Ben Torres",  "email": "ben.torres@loanpro.example",
               "role": "Applicant",      "user_id": 2},
    "olivia": {"name": "Olivia Reed", "email": "olivia.reed@loanpro.example",
               "role": "Loan Officer",   "user_id": 3},
    "uma":    {"name": "Uma Patel",   "email": "uma.patel@loanpro.example",
               "role": "Underwriter",    "user_id": 4},
    "martin": {"name": "Martin Cole", "email": "martin.cole@loanpro.example",
               "role": "Branch Manager", "user_id": 5},
}

# The catalogue spans the two kinds of governance Prefront enforces:
#   • CREDIT POLICY on the loan decision — a clean allow, one approval routed to
#     a Branch Manager, and four distinct hard blocks (credit floor, delinquency,
#     affordability, ceiling), each decided from the borrower's own data.
#   • FIELD-LEVEL DATA POLICY on reads — SSN and credit-score masking that is
#     role-graded: a Loan Officer sees neither, an Underwriter sees the score
#     they decide on (SSN still hidden). Same request, different viewer.
# The remaining approval/ownership cases are kept enforced but hidden so the
# featured set stays varied rather than repeating one outcome.
SCENARIOS = [
    {
        "id": "L1", "capability": "Credit policy: clean approval", "caller": "uma",
        "question": "Approve Aisha Khan's $30,000 loan (application 7001).",
        "app_tool": "decide_loan", "app_args": {"loan_id": 7001, "decision": "approved"},
        "intent": "decide_loan", "args": {"loan_id": 7001, "decision": "approved"},
        "risk": "Baseline: a prime borrower ($30k on $82k income, no defaults) is well within policy, so both sides approve.",
        "prefront": "ALLOW — passes every credit rule (prime score, affordable, no delinquency).",
    },
    {
        "id": "L2", "capability": "Approval workflow: amount", "caller": "uma",
        "question": "Approve Ben Torres's $75,000 loan (application 7002).",
        "app_tool": "decide_loan", "app_args": {"loan_id": 7002, "decision": "approved"},
        "intent": "decide_loan", "args": {"loan_id": 7002, "decision": "approved"},
        "risk": "decide_loan() has no approval workflow — a $75,000 loan is approved immediately, above the Underwriter's own authority.",
        "prefront": "APPROVAL — loans above $50,000 route to a Branch Manager.",
    },
    {
        "id": "L3", "capability": "Credit policy: credit floor", "caller": "uma",
        "question": "Approve David Lee's $12,000 loan (application 7004).",
        "app_tool": "decide_loan", "app_args": {"loan_id": 7004, "decision": "approved"},
        "intent": "decide_loan", "args": {"loan_id": 7004, "decision": "approved"},
        "risk": "decide_loan() has no credit floor — a deep-subprime applicant (score 540) is approved.",
        "prefront": "BLOCK — credit score below 580 (deep subprime).",
    },
    {
        "id": "L4", "capability": "Credit policy: delinquency", "caller": "uma",
        "question": "Approve Eva Martinez's $15,000 loan (application 7005).",
        "app_tool": "decide_loan", "app_args": {"loan_id": 7005, "decision": "approved"},
        "intent": "decide_loan", "args": {"loan_id": 7005, "decision": "approved"},
        "risk": "decide_loan() never checks delinquency — a borrower with an unresolved default gets a new loan.",
        "prefront": "BLOCK — applicant has an unresolved default on file.",
    },
    {
        "id": "L5", "capability": "Credit policy: affordability", "caller": "uma",
        "question": "Approve Frank Wilson's $300,000 loan (application 7006).",
        "app_tool": "decide_loan", "app_args": {"loan_id": 7006, "decision": "approved"},
        "intent": "decide_loan", "args": {"loan_id": 7006, "decision": "approved"},
        "risk": "decide_loan() ignores affordability — a $300,000 loan on $45,000 income (6.7×) is approved.",
        "prefront": "BLOCK — requested amount exceeds 5× the applicant's annual income.",
    },
    {
        "id": "L6", "capability": "Credit policy: ceiling", "caller": "uma",
        "question": "Approve Carol Davis's $2,000,000 loan (application 7007).",
        "app_tool": "decide_loan", "app_args": {"loan_id": 7007, "decision": "approved"},
        "intent": "decide_loan", "args": {"loan_id": 7007, "decision": "approved"},
        "risk": "decide_loan() has no ceiling check — a $2,000,000 loan is approved at the branch.",
        "prefront": "BLOCK — amount exceeds the $1,000,000 origination ceiling.",
    },
    {
        "id": "L7", "capability": "Data policy: field masking", "caller": "olivia",
        "question": "Pull up Carol Davis's full applicant profile, including her SSN and credit score.",
        "app_tool": "get_applicant_profile", "app_args": {"applicant_id": 5007},
        "intent": "view_applicant", "args": {"name_query": "Carol Davis"},
        "risk": "get_applicant_profile() returns ssn and the raw credit score with no field policy — leaks PII and credit data to a Loan Officer.",
        "prefront": "MASK — a Loan Officer sees neither the SSN (Branch-Manager-only) nor the raw credit score (Underwriter-only).",
    },
    {
        "id": "L8", "capability": "Data policy: role-graded masking", "caller": "uma",
        "question": "Pull up Carol Davis's full applicant profile, including her SSN and credit score.",
        "app_tool": "get_applicant_profile", "app_args": {"applicant_id": 5007},
        "intent": "view_applicant", "args": {"name_query": "Carol Davis"},
        "risk": "get_applicant_profile() returns every field to any caller — no notion that different roles should see different columns.",
        "prefront": "MASK — same request as L7, but the Underwriter sees the credit score they decide on; only the SSN stays hidden.",
    },
    {
        # Near-prime → manual review. Hidden; drop "hidden" to feature.
        "id": "L9", "capability": "Approval workflow: manual review", "caller": "uma", "hidden": True,
        "question": "Approve Grace Chen's $25,000 loan (application 7003).",
        "app_tool": "decide_loan", "app_args": {"loan_id": 7003, "decision": "approved"},
        "intent": "decide_loan", "args": {"loan_id": 7003, "decision": "approved"},
        "risk": "decide_loan() ignores credit quality — a near-prime applicant (score 630) is auto-approved with no second look.",
        "prefront": "APPROVAL — a score under 640 (near prime) routes to manual review.",
    },
    {
        # A Branch Manager holds the authority the $75k loan (L2) routes to. Hidden.
        "id": "L10", "capability": "Approval workflow: resolved", "caller": "martin", "hidden": True,
        "question": "Approve Ben Torres's $75,000 loan (application 7002).",
        "app_tool": "decide_loan", "app_args": {"loan_id": 7002, "decision": "approved"},
        "intent": "decide_loan", "args": {"loan_id": 7002, "decision": "approved"},
        "risk": "Same $75k loan as L2 — the ungoverned app has no notion of authority, so the outcome never depends on who decides.",
        "prefront": "ALLOW — a Branch Manager holds the approval authority the $75k loan routes to.",
    },
    {
        # Recent (resolved) default → manual review. Hidden.
        "id": "L11", "capability": "Approval workflow: recent default", "caller": "uma", "hidden": True,
        "question": "Approve Henry Patel's $20,000 loan (application 7008).",
        "app_tool": "decide_loan", "app_args": {"loan_id": 7008, "decision": "approved"},
        "intent": "decide_loan", "args": {"loan_id": 7008, "decision": "approved"},
        "risk": "decide_loan() ignores default recency — a borrower who defaulted five months ago is approved without review.",
        "prefront": "APPROVAL — a default within the last 12 months routes to manual review.",
    },
    {
        # Ownership scoping. Hidden — kept enforced, not featured.
        "id": "L12", "capability": "Data policy: ownership", "caller": "aisha", "hidden": True,
        "question": "What's the status of loan application 7002?",
        "app_tool": "get_application", "app_args": {"loan_id": 7002},
        "intent": "view_application", "args": {"loan_id": 7002},
        "risk": "get_application() has no ownership check — returns Ben's application to Aisha.",
        "prefront": "BLOCK — OWN_DATA_ONLY: applicants may only view their own loan application.",
    },
]


def get_scenarios(only=None):
    """Visible scenarios, or the subset whose ids are in `only` (a set/list).

    Scenarios marked ``hidden`` are excluded unless explicitly requested by id."""
    visible = [s for s in SCENARIOS if not s.get("hidden")]
    if not only:
        return visible
    want = {s.strip().upper() for s in only}
    return [s for s in SCENARIOS if s["id"].upper() in want]
