-- ============================================================================
-- Prefront SecureBank — Seed Data
-- ----------------------------------------------------------------------------
-- Small enough to read end-to-end, but stocked so the SAME natural-language
-- request returns DIFFERENT results depending on who asks, and so each policy
-- has a row that trips it. See README.md → "Demo hooks".
-- ============================================================================

-- ----------------------------------------------------------------- users ---
-- Customers (Account Holder) + staff (Teller/Manager/Admin). Callers act as one.
INSERT INTO users (user_id, name, email, role, status, clearance_level, ssn) VALUES
  (1, 'Maria Lopez',  'maria.lopez@securebank.example',  'Account Holder', 'active', 0, '111-11-1111'),
  (2, 'Sam Carter',   'sam.carter@securebank.example',   'Account Holder', 'active', 0, '222-22-2222'),
  (3, 'Tom Reed',     'tom.reed@securebank.example',     'Bank Teller',    'active', 1, '333-33-3333'),
  (4, 'Priya Shah',   'priya.shah@securebank.example',   'Bank Manager',   'active', 3, '444-44-4444'),
  (5, 'root',         'admin@securebank.example',        'System Admin',   'active', 5, '555-55-5555'),
  (6, 'Dana Whitfield','dana.whitfield@securebank.example','Account Holder','active', 0, '666-66-6666');

-- -------------------------------------------------------------- accounts ---
-- Maria (user 1): 1001 low balance (overdraft hook), 1002 suspended (state hook).
-- Sam (user 2):   1042 healthy balance (cross-customer ownership hook).
INSERT INTO accounts (account_id, user_id, account_type, balance, status) VALUES
  (1001, 1, 'checking',   1200.00, 'active'),     -- insufficient-funds hook ($5k > $1.2k)
  (1002, 1, 'savings',    8000.00, 'suspended'),  -- suspended-account hook
  (1042, 2, 'checking', 400000.00, 'active'),     -- B2 cross-customer hook + B6/B7 well-funded source
  (1055, 6, 'savings',  23000.00, 'active'),
  (5005, 2, 'checking',  9000.00, 'active');     -- transfer destination

-- ---------------------------------------------------------- transactions ---
INSERT INTO transactions (txn_id, account_id, txn_type, amount, counterparty_account, memo) VALUES
  (90001, 1001, 'deposit',       2500.00, NULL, 'Payroll'),
  (90002, 1001, 'withdrawal',    1300.00, NULL, 'Rent'),
  (90003, 1042, 'transfer_in',   5000.00, 5005, 'From savings'),
  (90004, 1055, 'deposit',      23000.00, NULL, 'Opening balance');

-- ----------------------------------------------------------------- loans ---
-- 7001: Sam's pending application — the decide_loan (Bank-Manager-only) hook.
INSERT INTO loans (loan_id, user_id, amount, status, credit_score, risk_rating, decided_by) VALUES
  (7001, 2, 25000.00, 'pending',  640, 'medium', NULL),  -- B8 loan-decision hook
  (7002, 6, 10000.00, 'approved', 720, 'low',    4),
  (7003, 1, 40000.00, 'pending',  580, 'high',   NULL);  -- high-risk hook (future scenario)
