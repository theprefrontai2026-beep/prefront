-- LoanPro demo seed. The rows are engineered so each loan application in the
-- scenario catalogue (scenarios.py) hits a DISTINCT credit-policy outcome when
-- decided — a clean allow, a large-amount approval, a near-prime review, and
-- blocks for the credit floor, an unresolved default, an unaffordable
-- income-multiple, and the hard ceiling. Today's reference date is 2026-07-21.

-- ---------------------------------------------------------------------- users
-- Two self-service Applicants (1,2), the three staff callers (3,4,5), a root row.
INSERT INTO users (user_id, name, email, role, status, clearance_level, ssn) VALUES
  (1, 'Aisha Khan',   'aisha.khan@loanpro.example',   'Applicant',      'active', 0, '111-11-1111'),
  (2, 'Ben Torres',   'ben.torres@loanpro.example',   'Applicant',      'active', 0, '222-22-2222'),
  (3, 'Olivia Reed',  'olivia.reed@loanpro.example',  'Loan Officer',   'active', 1, '333-33-3333'),
  (4, 'Uma Patel',    'uma.patel@loanpro.example',    'Underwriter',    'active', 2, '444-44-4444'),
  (5, 'Martin Cole',  'martin.cole@loanpro.example',  'Branch Manager', 'active', 3, '555-55-5555'),
  (6, 'root',         'admin@loanpro.example',        'System Admin',   'active', 5, '666-66-6666');

-- ----------------------------------------------------------------- applicants
-- Aisha (5001) & Ben (5002) are self-service (user_id set); the rest are
-- officer-entered walk-ins. `annual_income` drives the loan-to-income guardrail.
INSERT INTO applicants (applicant_id, user_id, full_name, email, annual_income, employment_years, ssn, assigned_officer) VALUES
  (5001, 1,    'Aisha Khan',  'aisha.khan@loanpro.example',   82000.00,  6.0, '111-11-1111', 3),
  (5002, 2,    'Ben Torres',  'ben.torres@loanpro.example',   60000.00,  4.0, '222-22-2222', 3),
  (5003, NULL, 'Grace Chen',  'grace.chen@loanpro.example',   50000.00,  5.0, '777-77-7777', 4),
  (5004, NULL, 'David Lee',   'david.lee@loanpro.example',    40000.00,  1.0, '888-88-8888', 4),
  (5005, NULL, 'Eva Martinez','eva.martinez@loanpro.example', 55000.00,  5.0, '999-99-9999', 4),
  (5006, NULL, 'Frank Wilson','frank.wilson@loanpro.example', 45000.00,  2.0, '121-21-2121', 3),
  (5007, NULL, 'Carol Davis', 'carol.davis@loanpro.example', 500000.00, 12.0, '343-43-4343', 4),
  (5008, NULL, 'Henry Patel', 'henry.patel@loanpro.example',  55000.00,  3.0, '565-65-6565', 3);

-- -------------------------------------------------------------- credit_scores
INSERT INTO credit_scores (applicant_id, score, bureau, last_updated) VALUES
  (5001, 720, 'Equifax',    '2026-06-01'),   -- prime
  (5002, 700, 'Experian',   '2026-06-01'),   -- prime
  (5003, 630, 'Experian',   '2026-06-01'),   -- near prime (< 640 → review)
  (5004, 540, 'TransUnion', '2026-06-01'),   -- deep subprime (< 580 → block)
  (5005, 700, 'Equifax',    '2026-06-01'),   -- prime, but has an open default
  (5006, 720, 'Equifax',    '2026-06-01'),   -- prime, but unaffordable ask
  (5007, 810, 'Equifax',    '2026-06-01'),   -- superprime, but over the ceiling
  (5008, 700, 'Experian',   '2026-06-01');   -- prime, but a recent (resolved) default

-- ------------------------------------------------------------- default_records
-- Eva (5005): an OLD but still UNRESOLVED default → hard block on a new loan.
-- Henry (5008): a RECENT but resolved default (~5 months ago) → manual review.
INSERT INTO default_records (applicant_id, amount_overdue, default_date, resolved) VALUES
  (5005, 4200.00, '2023-01-10', FALSE),
  (5008, 1800.00, '2026-03-01', TRUE);

-- ---------------------------------------------------------- loan_applications
-- Each pending application is tuned to a single primary policy outcome:
--   7001 clean $30k / $82k income      → ALLOW
--   7002 $75k (> $50k)                 → APPROVAL (Branch Manager)
--   7003 near-prime (score 630)        → APPROVAL (manual review)
--   7004 deep subprime (score 540)     → BLOCK (credit floor)
--   7005 unresolved default on file    → BLOCK (delinquency)
--   7006 $300k on $45k income (6.7×)   → BLOCK (income multiple)
--   7007 $2,000,000                    → BLOCK (ceiling)
--   7008 recent resolved default       → APPROVAL (manual review)  [hidden scenario]
INSERT INTO loan_applications (loan_id, applicant_id, requested_amount, term_months, status, assigned_officer) VALUES
  (7001, 5001,   30000.00,  36, 'pending', 3),
  (7002, 5002,   75000.00,  60, 'pending', 3),
  (7003, 5003,   25000.00,  36, 'pending', 4),
  (7004, 5004,   12000.00,  24, 'pending', 4),
  (7005, 5005,   15000.00,  24, 'pending', 4),
  (7006, 5006,  300000.00, 120, 'pending', 3),
  (7007, 5007, 2000000.00, 120, 'pending', 4),
  (7008, 5008,   20000.00,  36, 'pending', 3);
