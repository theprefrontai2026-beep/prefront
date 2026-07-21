# LoanPro Origination Policy — Consumer Unsecured Lending

**Document owner:** Chief Credit Officer
**Applies to:** Unsecured personal installment loans originated through all channels
**Version:** 1.0
**Effective date:** 1 January 2026
**Review cycle:** Annual (or upon material change in portfolio loss rates)
**Classification:** Internal — Confidential

> This is the **design-time policy document** for the LoanPro demo. Prefront's
> governed rule bundle (`../policy/policy.yaml`) is derived from it: each runtime
> rule cites the section of this document it enforces (`source:` provenance), and
> the deterministic runtime evaluates only those published rules. Adapted from the
> reference loan-decisioning application's credit policy for the LoanPro roles
> (Applicant · Loan Officer · Underwriter · Branch Manager).

---

## 1. Purpose and Scope

This policy establishes the minimum credit standards, decisioning framework,
data-access controls, and authority levels governing the origination of
unsecured consumer personal loans ("Loans") by LoanPro, Inc. ("the Company"). It
applies to every application received, regardless of channel, and to all staff
and automated systems involved in intake, underwriting, approval, and exception
handling.

The objective is to extend credit responsibly — balancing portfolio growth
against credit risk, regulatory obligations (ECOA, FCRA, TILA, UDAAP), and the
Company's risk appetite as set by the Board.

---

## 2. Guiding Principles

1. **Decisions are rules-based, not discretionary.** Every credit decision is
   produced by the Company's documented decision engine. Human judgment is
   exercised only within defined manual-review and exception channels, never to
   override a hard decline.
2. **Verify, never assume.** No applicant attribute (credit score, income,
   default history) may be self-reported as the basis for a decision. All
   credit-bearing data must originate from an authoritative source: a credit
   bureau or the Company's verified records.
3. **The same rule applies to everyone.** Eligibility and pricing are functions
   of risk attributes only. Decisions must be reproducible and explainable, and
   protected characteristics may never enter the decision.
4. **Affordability before approval.** A loan is only approved where the applicant
   can demonstrably service it; exposure is capped relative to verified income
   regardless of credit quality.
5. **Least-privilege access.** Staff and agents may see and do only what their
   role requires. Sensitive fields are masked by default and released only to the
   role that needs them; borrowers see only their own file.
6. **Every decision is auditable.** Each application produces a complete, retained
   record of the inputs, the rules evaluated, and the reason(s) for the outcome.

---

## 3. Roles and Access

The Company operates a segregated-duties model. Four roles participate in
origination:

| Role | Responsibilities |
|------|------------------|
| **Applicant** | The borrower. Applies for a loan and tracks their own application. |
| **Loan Officer** | Intakes and packages applications; works an assigned pipeline. Does **not** render credit decisions. |
| **Underwriter** | Renders credit decisions up to their authority; sees the credit data required to decide. |
| **Branch Manager** | Holds full decision authority and approves escalations; the sole role cleared for the most sensitive PII. |

### 3.1 Borrower self-service

An Applicant may view the status and details of **their own** loan application
only. Applications belonging to other borrowers are not visible to them.

### 3.2 Directory access

Only Loan Officers, Underwriters, and Branch Managers may list, search, or export
the applicant directory. Borrowers may not enumerate other borrowers.

---

## 4. Decision Framework

Every application is evaluated through a framework whose stages are applied in
strict order of precedence: **a hard decline always wins over a referral, which
wins over an approval.** Decline and referral rules are evaluated against
verified data — the applicant's bureau score, delinquency record, verified
income, and the requested amount.

### 4.1 Hard declines (mandatory, non-overridable)

A match on **any** hard-decline rule results in an immediate **DECLINE**. Hard
declines take precedence over every other rule and may not be overridden by staff
at any authority level.

| Rule | Section | Condition |
|------|---------|-----------|
| Credit floor | §4.1.1 | Bureau credit score below 580 (deep subprime) |
| Delinquency | §4.1.2 | An unresolved default outstanding on the applicant's file |
| Affordability | §4.1.3 | Requested amount exceeds five times verified annual income |
| Risk limits | §4.1.4 | Requested amount above the $1,000,000 origination ceiling |

**4.1.1 Credit floor.** Applications from borrowers with a credit score below 580
are declined at origination. 580 is the Company's minimum acceptable risk grade.

**4.1.2 Delinquency.** No new loan may be approved while the applicant has an
unresolved default outstanding, regardless of amount or credit score.

**4.1.3 Affordability.** The requested loan amount may not exceed five times the
applicant's verified annual income. This debt-to-income guardrail binds
independently of credit quality — a strong score does not lift the affordability
cap.

**4.1.4 Risk limits.** No single loan above $1,000,000 may be originated at the
branch; such applications are escalated to the credit committee out of band.

### 4.2 Manual review (refer to a higher authority)

Where no hard decline has fired, a match on any review rule downgrades the
outcome from APPROVE to **REVIEW** — the decision routes to a Branch Manager
rather than being auto-approved. These flag elevated but not disqualifying risk.

| Rule | Section | Condition |
|------|---------|-----------|
| Manual review — near prime | §4.2 | Credit score under 640 |
| Manual review — recent default | §4.2 | A default within the trailing twelve months, regardless of resolution |

### 4.3 Decision integrity

Once a loan application has been approved or rejected, its decision is final and
may not be overwritten. A re-decision request on a settled application is
rejected.

### 4.4 Default outcome

An application that survives every stage without a match — no hard decline, no
review flag — is **APPROVED** at standard terms for its credit tier.

---

## 5. Credit Tiers

Applicants are graded into tiers by bureau score. Tier governs eligibility posture
and pricing.

| Tier | Score band | Posture |
|------|-----------|---------|
| **Superprime** | 800–850 | Strongest terms. |
| **Prime** | 740–799 | Standard approve. |
| **Near-prime** | 640–739 | Approve; scores under 640 route to manual review (§4.2). |
| **Subprime** | 580–639 | Manual review only. |
| **Deep subprime** | Below 580 | Hard decline (§4.1.1). |

---

## 6. Loan Authority

Credit decisions are made only by an Underwriter or a Branch Manager. Loan
Officers intake and package applications but do not approve them.

| Decision | Authority |
|----------|-----------|
| Loans up to and including **$50,000** | Underwriter |
| Loans **above $50,000** (within the ceiling) | Underwriter's recommendation **plus Branch Manager approval** before the decision is final |
| Any decision on a loan | **Not** available to a Loan Officer or an Applicant |
| Change to any rule, threshold, or tier band | Chief Credit Officer + Credit Risk Committee |

A loan above the Underwriter's authority is not declined for that reason alone —
it is routed to a Branch Manager, who holds the authority to approve it.

---

## 7. Data Protection

Credit and identity data are released on a least-privilege basis. The following
field controls apply to every read of an applicant record.

### 7.1 PII handling

Social Security Numbers are restricted. Only a **Branch Manager** may view an
unmasked SSN; for every other role the field is masked.

### 7.2 Credit data segregation

Raw bureau credit scores are available to **Underwriters and Branch Managers**,
who render or approve the credit decision. A **Loan Officer** works from the tier
band, not the exact score, so the raw score is masked from them. Masking is
therefore role-graded: a Loan Officer sees neither SSN nor score; an Underwriter
sees the score but not the SSN; a Branch Manager sees both.

---

## 8. Fair Lending and Compliance

- Decisions are based solely on permissible credit-risk attributes. Protected
  characteristics under ECOA and applicable law are excluded from all rules.
- Every declined or referred applicant is entitled to the specific principal
  reason(s) for the outcome (adverse-action notice under FCRA/ECOA). The decision
  engine records explicit, plain-language reasons for this purpose.
- Bureau data is used in accordance with FCRA permissible-purpose requirements.
- All consumer disclosures (APR, term, total cost) are provided per TILA prior to
  consummation.

---

## 9. Governance, Audit, and Change Control

- **Single source of truth.** The decision rules in this policy are implemented
  in, and only in, the Company's governed rule configuration
  (`policy/policy.yaml`). No credit logic may be embedded in application code or
  staff discretion outside this framework.
- **Auditability.** Each application retains a complete record of inputs, rules
  evaluated, and outcome reasons, made available to internal audit and regulators.
- **Change control.** Any change to a threshold, rule, or tier band requires Chief
  Credit Officer approval and Credit Risk Committee ratification, with the change,
  rationale, and effective date logged.

---

*This document is a sample business policy created for demonstration purposes.
LoanPro, Inc. is a fictitious entity. Thresholds and rules mirror the reference
loan-decisioning application and are illustrative only — not financial or legal
advice.*
