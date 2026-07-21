# LoanPro in-repo demo

A retail loan-origination example that ships **inside this repo** and runs from
the same `docker-compose.yaml` — the second worked example alongside
`securebank-demo/`. It demonstrates the same before/after governance contrast on
a different domain, proving the engine is domain-independent: only the published
artifacts (`policy/policy.yaml`, `policy/query_templates.yaml`) and this demo's
own services carry loan vocabulary; the engine image is unchanged.

## Roles

The standalone loan chatbot (`~/sample-loan-app`) has no identity/role model —
its policy engine decides on credit signals alone. Prefront governance is built
on caller identity, so this demo adds one:

| Role | Can |
|---|---|
| **Applicant** | View only their **own** loan application (own-data-only) |
| **Loan Officer** | See their assigned pipeline; look up applicants (ssn + raw score **masked**); **cannot** decide loans |
| **Underwriter** | Decide loans **≤ $50,000**; sees raw credit scores; larger loans need approval |
| **Branch Manager** | Decide any amount (up to the $1M ceiling); sees unmasked ssn |

## Scenarios (`scenarios.py`)

The catalogue spans the two kinds of governance Prefront enforces — **credit
policy** on the loan decision (outcome computed from the borrower's own data) and
**field-level data policy** on reads (role-graded SSN / credit-score masking).
The ungoverned app has neither, so it approves every loan and leaks every field.

| # | Caller | Request | Governed outcome |
|---|---|---|---|
| L1 | Underwriter | approve $30k, prime, no defaults | ALLOW (passes every rule) |
| L2 | Underwriter | approve $75k (> $50k) | APPROVAL (Branch Manager) |
| L3 | Underwriter | approve score-540 borrower | BLOCK (credit floor < 580) |
| L4 | Underwriter | approve borrower with an open default | BLOCK (delinquency) |
| L5 | Underwriter | approve $300k on $45k income (6.7×) | BLOCK (income multiple > 5×) |
| L6 | Underwriter | approve $2,000,000 | BLOCK (ceiling) |
| L7 | Loan Officer | look up an applicant (SSN + score) | MASK (both SSN **and** score hidden) |
| L8 | Underwriter | same lookup as L7 | MASK (SSN hidden; **score visible** — they decide on it) |

L7 vs L8 is the same request by two roles: masking is **role-graded** — a Loan
Officer sees neither field, an Underwriter sees the score, a Branch Manager sees
both. Hidden extras (kept enforced, not featured): `L9` near-prime review →
APPROVAL, `L10` the $75k resolved by a Branch Manager → ALLOW, `L11`
recent-default review → APPROVAL, `L12` applicant own-data-only block.

## Services (see `docker-compose.yaml`)

- `loanpro-db` (`:5435`) — Postgres seeded from `db/schema.sql` + `db/seed.sql`
- `loanpro-seed` — copies the curated artifacts into the shared `artifacts` volume at `/artifacts/loanpro-demo/`
- `loanpro-mcp` (`:8101`) — the `semantic-mcp-server` image pointed at `/artifacts/loanpro-demo/`
- `loanpro-ungoverned` (`:8097`) — the "before": typed business functions, no policy
- `loanpro-orchestrator` (`:8098`) — fans each scenario to both sides; the UI **Runtime tab** points here when the **LoanPro** demo is selected

An `OpenAI API key` is required for the ungoverned and orchestrator services.

## Governing policy document

`docs/credit_policy.md` is the **design-time policy** this demo enforces — the
human-authored credit policy (adapted from the reference loan-decisioning app)
from which `policy/policy.yaml` is derived. Each runtime rule's `source:` block
cites the section of that document it enforces, so the design-time → runtime link
is auditable end to end.
