-- =============================================================================
-- SecureBank Database Schema & Seed Data
-- PostgreSQL 14+
-- =============================================================================

-- Enable UUID generation
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- =============================================================================
-- DROP (safe re-run)
-- =============================================================================
DROP TABLE IF EXISTS audit_logs       CASCADE;
DROP TABLE IF EXISTS loans            CASCADE;
DROP TABLE IF EXISTS transactions     CASCADE;
DROP TABLE IF EXISTS accounts         CASCADE;
DROP TABLE IF EXISTS users            CASCADE;

-- =============================================================================
-- TABLE: users
-- =============================================================================
CREATE TABLE users (
  id                      UUID          PRIMARY KEY DEFAULT gen_random_uuid(),

  -- Core identity
  name                    VARCHAR(100)  NOT NULL,
  unique_id               VARCHAR(100),
  risk_level              VARCHAR(100)  DEFAULT 'Low',           -- Low | Medium | High
  email                   VARCHAR(150)  NOT NULL UNIQUE,
  phone                   VARCHAR(20),
  role                    VARCHAR(50)   NOT NULL DEFAULT 'Account Holder',  -- Account Holder | Bank Teller | Bank Manager
  status                  VARCHAR(20)   NOT NULL DEFAULT 'active',          -- active | inactive | suspended

  -- PII (Account Holder)
  full_name               VARCHAR(200),
  ssn                     VARCHAR(11),                           -- XXX-XX-XXXX (Bank Manager view only)
  date_of_birth           TIMESTAMP,
  home_address            TEXT,
  alternate_phone         VARCHAR(20),

  -- Banking profile
  employer_name           VARCHAR(200),
  annual_income           DECIMAL(15,2),
  credit_score            INTEGER,                               -- 300–850 (Bank Staff view only)

  -- Verification
  identity_verified       VARCHAR(20)   DEFAULT 'pending',       -- pending | verified | failed
  kyc_completed           VARCHAR(20)   DEFAULT 'pending',       -- pending | completed | failed

  -- Pending change workflow
  pending_profile_changes TEXT,                                  -- JSON of changes awaiting Bank Manager approval
  last_profile_update     TIMESTAMP,
  profile_updated_by      UUID          REFERENCES users(id),

  -- Authorization attributes
  clearance_level         INTEGER,
  department              VARCHAR(100),                          -- Bank staff only
  branch_id               VARCHAR(50),

  created_at              TIMESTAMP     NOT NULL DEFAULT NOW()
);

-- Indexes
CREATE INDEX idx_users_email    ON users(email);
CREATE INDEX idx_users_role     ON users(role);
CREATE INDEX idx_users_status   ON users(status);
CREATE INDEX idx_users_risk     ON users(risk_level);

-- =============================================================================
-- TABLE: accounts
-- =============================================================================
CREATE TABLE accounts (
  id                          UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id                     UUID          NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  account_number              VARCHAR(20)   NOT NULL UNIQUE,
  account_type                VARCHAR(50)   NOT NULL,            -- checking | savings | business | credit | investment

  -- Balance
  balance                     DECIMAL(15,2) NOT NULL DEFAULT 0.00,
  status                      VARCHAR(20)   NOT NULL DEFAULT 'active', -- active | inactive | frozen | closed | suspended

  -- Interest
  interest_rate               DECIMAL(5,4)  DEFAULT 0.0000,     -- Annual percentage rate
  compounding_frequency       VARCHAR(20)   DEFAULT 'monthly',  -- daily | monthly | quarterly | annually

  -- Fees
  monthly_fee                 DECIMAL(10,2) DEFAULT 0.00,
  transaction_fee             DECIMAL(10,2) DEFAULT 0.00,
  overdraft_fee               DECIMAL(10,2) DEFAULT 35.00,
  minimum_balance_fee         DECIMAL(10,2) DEFAULT 0.00,

  -- Limits
  minimum_balance             DECIMAL(15,2) DEFAULT 0.00,
  maximum_balance             DECIMAL(15,2) DEFAULT 250000.00,
  daily_withdrawal_limit      DECIMAL(15,2) DEFAULT 1000.00,
  monthly_transaction_limit   INTEGER       DEFAULT 50,

  -- Credit & overdraft
  overdraft_limit             DECIMAL(15,2) DEFAULT 0.00,
  overdraft_protection        BOOLEAN       DEFAULT FALSE,
  credit_limit                DECIMAL(15,2) DEFAULT 0.00,
  available_credit            DECIMAL(15,2) DEFAULT 0.00,

  -- Features
  online_banking              BOOLEAN       DEFAULT TRUE,
  mobile_banking              BOOLEAN       DEFAULT TRUE,
  debit_card                  BOOLEAN       DEFAULT TRUE,
  checks_enabled              BOOLEAN       DEFAULT TRUE,
  wire_transfers              BOOLEAN       DEFAULT TRUE,
  international_transfers     BOOLEAN       DEFAULT FALSE,

  -- Routing
  branch_code                 VARCHAR(10)   DEFAULT '001',
  routing_number              VARCHAR(9)    DEFAULT '123456789',
  swift_code                  VARCHAR(11)   DEFAULT 'SECUBANKXXX',

  -- Dates
  opened_date                 TIMESTAMP     NOT NULL DEFAULT NOW(),
  last_activity_date          TIMESTAMP,
  dormancy_date               TIMESTAMP,
  closed_date                 TIMESTAMP,

  -- Compliance
  risk_rating                 VARCHAR(20)   DEFAULT 'low',       -- low | medium | high | critical
  compliance_status           VARCHAR(20)   DEFAULT 'compliant', -- compliant | under_review | non_compliant
  kyc_status                  VARCHAR(20)   DEFAULT 'verified',  -- pending | verified | expired | failed
  aml_status                  VARCHAR(20)   DEFAULT 'clear',     -- clear | flagged | under_investigation

  -- Tax
  tax_id                      VARCHAR(20),
  tax_reporting_category      VARCHAR(50)   DEFAULT 'personal',  -- personal | business | trust | estate

  -- Relationships (JSON)
  primary_account_holder      UUID          REFERENCES users(id),
  joint_account_holders       TEXT,                              -- JSON array of user IDs
  beneficiaries               TEXT,                              -- JSON array of beneficiary info
  authorized_users            TEXT,                              -- JSON array of user IDs

  -- Product
  account_package             VARCHAR(50)   DEFAULT 'standard', -- basic | standard | premium | private
  product_code                VARCHAR(20),
  sub_product                 VARCHAR(50),

  -- Digital banking
  digital_statements          BOOLEAN       DEFAULT TRUE,
  sms_notifications           BOOLEAN       DEFAULT TRUE,
  email_notifications         BOOLEAN       DEFAULT TRUE,
  push_notifications          BOOLEAN       DEFAULT TRUE,

  -- Security
  two_factor_auth             BOOLEAN       DEFAULT FALSE,
  biometric_auth              BOOLEAN       DEFAULT FALSE,
  security_questions          TEXT,                              -- JSON Q&A

  -- Notes
  notes                       TEXT,
  internal_notes              TEXT,
  special_instructions        TEXT,

  -- Pending change workflow (Maker-Checker)
  pending_account_changes     TEXT,                              -- JSON of changes awaiting Bank Manager approval
  last_account_update         TIMESTAMP,
  account_updated_by          UUID          REFERENCES users(id),

  created_at                  TIMESTAMP     NOT NULL DEFAULT NOW()
);

-- Indexes
CREATE INDEX idx_accounts_user_id       ON accounts(user_id);
CREATE INDEX idx_accounts_status        ON accounts(status);
CREATE INDEX idx_accounts_type          ON accounts(account_type);
CREATE INDEX idx_accounts_account_number ON accounts(account_number);

-- =============================================================================
-- TABLE: transactions
-- =============================================================================
CREATE TABLE transactions (
  id              UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
  from_account_id UUID          REFERENCES accounts(id),
  to_account_id   UUID          REFERENCES accounts(id),
  type            VARCHAR(50)   NOT NULL,                        -- deposit | withdrawal | transfer | fee
  amount          DECIMAL(15,2) NOT NULL,
  description     TEXT          NOT NULL,
  status          VARCHAR(20)   NOT NULL DEFAULT 'completed',    -- completed | pending | failed
  processed_by    UUID          REFERENCES users(id),            -- Staff member who processed
  created_at      TIMESTAMP     NOT NULL DEFAULT NOW()
);

-- Indexes
CREATE INDEX idx_transactions_from_account ON transactions(from_account_id);
CREATE INDEX idx_transactions_to_account   ON transactions(to_account_id);
CREATE INDEX idx_transactions_type         ON transactions(type);
CREATE INDEX idx_transactions_status       ON transactions(status);
CREATE INDEX idx_transactions_created_at   ON transactions(created_at DESC);

-- =============================================================================
-- TABLE: loans
-- =============================================================================
CREATE TABLE loans (
  id                UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id           UUID          NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  type              VARCHAR(50)   NOT NULL,                      -- personal | auto | home | business | mortgage | education
  amount            DECIMAL(15,2) NOT NULL,
  interest_rate     DECIMAL(5,2)  NOT NULL,
  term_months       INTEGER       NOT NULL,
  monthly_payment   DECIMAL(15,2) NOT NULL,
  remaining_balance DECIMAL(15,2) NOT NULL,
  status            VARCHAR(20)   NOT NULL DEFAULT 'pending',    -- pending | offered | accepted | approved | rejected | active | paid_off
  approved_by       UUID          REFERENCES users(id),          -- Bank Manager or Teller who approved
  notes             TEXT,
  created_at        TIMESTAMP     NOT NULL DEFAULT NOW()
);

-- Indexes
CREATE INDEX idx_loans_user_id    ON loans(user_id);
CREATE INDEX idx_loans_status     ON loans(status);
CREATE INDEX idx_loans_type       ON loans(type);
CREATE INDEX idx_loans_created_at ON loans(created_at DESC);

-- =============================================================================
-- TABLE: audit_logs
-- =============================================================================
CREATE TABLE audit_logs (
  id                      UUID          PRIMARY KEY DEFAULT gen_random_uuid(),

  -- Principal (who made the request)
  principal_id            UUID          NOT NULL REFERENCES users(id),
  principal_type          VARCHAR(50)   NOT NULL DEFAULT 'User',
  principal_role          VARCHAR(50)   NOT NULL,                -- Account Holder | Bank Teller | Bank Manager

  -- Action & resource
  action                  VARCHAR(100)  NOT NULL,                -- e.g. TransferFunds, ApproveLoan, ViewAccount
  resource_type           VARCHAR(50),                           -- Account | Transaction | Loan | User | AuditLog
  resource_id             VARCHAR(100),

  -- Decision
  decision                VARCHAR(20)   NOT NULL,                -- Allow | Deny
  reasons                 TEXT,                                  -- JSON array of decision reasons

  -- Request context
  request_context         TEXT,                                  -- JSON (IP, user agent, etc.)
  session_id              VARCHAR(100),
  ip_address              VARCHAR(45),
  user_agent              TEXT,

  -- Processing metadata
  policy_engine_version   VARCHAR(50)   DEFAULT '1.0',
  processing_time_ms      INTEGER,

  -- Risk & compliance
  risk_level              VARCHAR(20)   DEFAULT 'low',           -- low | medium | high | critical
  compliance_flags        TEXT,                                  -- JSON array

  -- Business context
  business_context        TEXT,
  data_accessed           TEXT,                                  -- Sensitive data fields accessed
  action_outcome          VARCHAR(50),                           -- success | failed | partial

  created_at              TIMESTAMP     NOT NULL DEFAULT NOW()
);

-- Indexes
CREATE INDEX idx_audit_principal_id   ON audit_logs(principal_id);
CREATE INDEX idx_audit_action         ON audit_logs(action);
CREATE INDEX idx_audit_decision       ON audit_logs(decision);
CREATE INDEX idx_audit_resource_type  ON audit_logs(resource_type);
CREATE INDEX idx_audit_risk_level     ON audit_logs(risk_level);
CREATE INDEX idx_audit_created_at     ON audit_logs(created_at DESC);

-- =============================================================================
-- SEED DATA
-- =============================================================================

-- -----------------------------------------------------------------------------
-- Users
-- -----------------------------------------------------------------------------
INSERT INTO users (
  id, name, unique_id, email, phone, role, status,
  risk_level, clearance_level, department, branch_id,
  identity_verified, kyc_completed, credit_score, annual_income, employer_name
) VALUES

-- Bank Manager
(
  'a0000000-0000-0000-0000-000000000001',
  'Alice Johnson', 'EMP-MGR-001',
  'alice.johnson@securebank.com', '555-0100',
  'Bank Manager', 'active',
  'Low', 5, 'Operations', 'BR-001',
  'verified', 'completed', NULL, NULL, 'SecureBank'
),

-- Bank Tellers
(
  'a0000000-0000-0000-0000-000000000002',
  'Bob Martinez', 'EMP-TLR-001',
  'bob.martinez@securebank.com', '555-0101',
  'Bank Teller', 'active',
  'Low', 3, 'Retail Banking', 'BR-001',
  'verified', 'completed', NULL, NULL, 'SecureBank'
),
(
  'a0000000-0000-0000-0000-000000000003',
  'Carol Smith', 'EMP-TLR-002',
  'carol.smith@securebank.com', '555-0102',
  'Bank Teller', 'active',
  'Low', 3, 'Retail Banking', 'BR-002',
  'verified', 'completed', NULL, NULL, 'SecureBank'
),

-- Account Holders
(
  'a0000000-0000-0000-0000-000000000004',
  'David Chen', 'CUST-0001',
  'david.chen@email.com', '555-0200',
  'Account Holder', 'active',
  'Low', NULL, NULL, NULL,
  'verified', 'completed', 740, 85000.00, 'Apex Technologies'
),
(
  'a0000000-0000-0000-0000-000000000005',
  'Emma Wilson', 'CUST-0002',
  'emma.wilson@email.com', '555-0201',
  'Account Holder', 'active',
  'Medium', NULL, NULL, NULL,
  'verified', 'completed', 680, 62000.00, 'Green Valley Schools'
),
(
  'a0000000-0000-0000-0000-000000000006',
  'Frank Davis', 'CUST-0003',
  'frank.davis@email.com', '555-0202',
  'Account Holder', 'active',
  'High', NULL, NULL, NULL,
  'pending', 'pending', 590, 45000.00, 'Davis Contracting'
),
(
  'a0000000-0000-0000-0000-000000000007',
  'Grace Lee', 'CUST-0004',
  'grace.lee@email.com', '555-0203',
  'Account Holder', 'active',
  'Low', NULL, NULL, NULL,
  'verified', 'completed', 800, 120000.00, 'Lee & Partners Law Firm'
),
(
  'a0000000-0000-0000-0000-000000000008',
  'Henry Brown', 'CUST-0005',
  'henry.brown@email.com', '555-0204',
  'Account Holder', 'suspended',
  'High', NULL, NULL, NULL,
  'failed', 'pending', 520, 38000.00, 'Unemployed'
);

-- -----------------------------------------------------------------------------
-- Accounts
-- -----------------------------------------------------------------------------
INSERT INTO accounts (
  id, user_id, account_number, account_type,
  balance, status, interest_rate,
  daily_withdrawal_limit, credit_limit,
  risk_rating, kyc_status, aml_status, account_package
) VALUES

-- David Chen — checking + savings
(
  'b0000000-0000-0000-0000-000000000001',
  'a0000000-0000-0000-0000-000000000004',
  '1001000001', 'checking',
  12500.00, 'active', 0.0100,
  2000.00, 0.00,
  'low', 'verified', 'clear', 'standard'
),
(
  'b0000000-0000-0000-0000-000000000002',
  'a0000000-0000-0000-0000-000000000004',
  '1001000002', 'savings',
  34750.50, 'active', 0.0450,
  500.00, 0.00,
  'low', 'verified', 'clear', 'standard'
),

-- Emma Wilson — checking
(
  'b0000000-0000-0000-0000-000000000003',
  'a0000000-0000-0000-0000-000000000005',
  '1001000003', 'checking',
  3200.00, 'active', 0.0100,
  1000.00, 0.00,
  'low', 'verified', 'clear', 'basic'
),

-- Frank Davis — savings (frozen due to high risk)
(
  'b0000000-0000-0000-0000-000000000004',
  'a0000000-0000-0000-0000-000000000006',
  '1001000004', 'savings',
  800.00, 'frozen', 0.0200,
  200.00, 0.00,
  'high', 'verified', 'flagged', 'basic'
),

-- Grace Lee — checking + business
(
  'b0000000-0000-0000-0000-000000000005',
  'a0000000-0000-0000-0000-000000000007',
  '1001000005', 'checking',
  67000.00, 'active', 0.0150,
  5000.00, 0.00,
  'low', 'verified', 'clear', 'premium'
),
(
  'b0000000-0000-0000-0000-000000000006',
  'a0000000-0000-0000-0000-000000000007',
  '1001000006', 'business',
  185000.00, 'active', 0.0200,
  10000.00, 25000.00,
  'low', 'verified', 'clear', 'premium'
),

-- Henry Brown — checking (suspended)
(
  'b0000000-0000-0000-0000-000000000007',
  'a0000000-0000-0000-0000-000000000008',
  '1001000007', 'checking',
  150.00, 'frozen', 0.0000,
  0.00, 0.00,
  'high', 'expired', 'under_investigation', 'basic'
);

-- -----------------------------------------------------------------------------
-- Transactions
-- -----------------------------------------------------------------------------
INSERT INTO transactions (
  id, from_account_id, to_account_id,
  type, amount, description, status, processed_by, created_at
) VALUES

-- David Chen deposits
(
  'c0000000-0000-0000-0000-000000000001',
  NULL, 'b0000000-0000-0000-0000-000000000001',
  'deposit', 5000.00, 'Direct deposit — Apex Technologies payroll',
  'completed', 'a0000000-0000-0000-0000-000000000002',
  NOW() - INTERVAL '30 days'
),

-- David Chen transfer (checking → savings, within $5k daily limit)
(
  'c0000000-0000-0000-0000-000000000002',
  'b0000000-0000-0000-0000-000000000001', 'b0000000-0000-0000-0000-000000000002',
  'transfer', 2000.00, 'Monthly savings transfer',
  'completed', NULL,
  NOW() - INTERVAL '25 days'
),

-- Emma Wilson deposit
(
  'c0000000-0000-0000-0000-000000000003',
  NULL, 'b0000000-0000-0000-0000-000000000003',
  'deposit', 3200.00, 'Direct deposit — Green Valley Schools payroll',
  'completed', 'a0000000-0000-0000-0000-000000000002',
  NOW() - INTERVAL '20 days'
),

-- Grace Lee large transfer (Bank Teller initiated, Manager approved)
(
  'c0000000-0000-0000-0000-000000000004',
  'b0000000-0000-0000-0000-000000000006', 'b0000000-0000-0000-0000-000000000005',
  'transfer', 15000.00, 'Business to personal — quarterly draw',
  'completed', 'a0000000-0000-0000-0000-000000000002',
  NOW() - INTERVAL '15 days'
),

-- David Chen withdrawal
(
  'c0000000-0000-0000-0000-000000000005',
  'b0000000-0000-0000-0000-000000000001', NULL,
  'withdrawal', 500.00, 'ATM cash withdrawal',
  'completed', NULL,
  NOW() - INTERVAL '10 days'
),

-- Emma to David transfer (within Account Holder $5k daily limit)
(
  'c0000000-0000-0000-0000-000000000006',
  'b0000000-0000-0000-0000-000000000003', 'b0000000-0000-0000-0000-000000000001',
  'transfer', 500.00, 'Rent share payment',
  'completed', NULL,
  NOW() - INTERVAL '5 days'
),

-- Monthly fee
(
  'c0000000-0000-0000-0000-000000000007',
  'b0000000-0000-0000-0000-000000000003', NULL,
  'fee', 12.00, 'Monthly account maintenance fee',
  'completed', 'a0000000-0000-0000-0000-000000000002',
  NOW() - INTERVAL '1 day'
),

-- Failed transfer (Frank Davis — frozen account)
(
  'c0000000-0000-0000-0000-000000000008',
  'b0000000-0000-0000-0000-000000000004', 'b0000000-0000-0000-0000-000000000003',
  'transfer', 300.00, 'Transfer attempt — account frozen',
  'failed', NULL,
  NOW() - INTERVAL '3 days'
);

-- -----------------------------------------------------------------------------
-- Loans
-- -----------------------------------------------------------------------------
INSERT INTO loans (
  id, user_id, type, amount, interest_rate,
  term_months, monthly_payment, remaining_balance,
  status, approved_by, notes, created_at
) VALUES

-- David Chen — active auto loan (approved by Teller, within $50k limit)
(
  'd0000000-0000-0000-0000-000000000001',
  'a0000000-0000-0000-0000-000000000004',
  'auto', 22000.00, 6.50,
  60, 429.22, 18650.00,
  'active', 'a0000000-0000-0000-0000-000000000002',
  'Approved for 2023 Honda Accord purchase', NOW() - INTERVAL '18 months'
),

-- Emma Wilson — pending personal loan application
(
  'd0000000-0000-0000-0000-000000000002',
  'a0000000-0000-0000-0000-000000000005',
  'personal', 8000.00, 9.00,
  36, 254.61, 8000.00,
  'pending', NULL,
  'Home appliance replacement and minor repairs', NOW() - INTERVAL '2 days'
),

-- Grace Lee — active home loan (requires Bank Manager — amount > $50k)
(
  'd0000000-0000-0000-0000-000000000003',
  'a0000000-0000-0000-0000-000000000007',
  'home', 350000.00, 4.75,
  360, 1825.38, 312400.00,
  'active', 'a0000000-0000-0000-0000-000000000001',
  'Primary residence mortgage — 30 year fixed', NOW() - INTERVAL '3 years'
),

-- Grace Lee — business loan offered, awaiting acceptance
(
  'd0000000-0000-0000-0000-000000000004',
  'a0000000-0000-0000-0000-000000000007',
  'business', 75000.00, 7.25,
  84, 1142.60, 75000.00,
  'offered', 'a0000000-0000-0000-0000-000000000001',
  'Offered — expansion of law firm office space', NOW() - INTERVAL '4 days'
),

-- Frank Davis — rejected loan (high risk profile)
(
  'd0000000-0000-0000-0000-000000000005',
  'a0000000-0000-0000-0000-000000000006',
  'personal', 15000.00, 0.00,
  0, 0.00, 15000.00,
  'rejected', 'a0000000-0000-0000-0000-000000000001',
  'Rejected — high risk level, insufficient credit score (590), account frozen', NOW() - INTERVAL '7 days'
),

-- David Chen — new mortgage application pending (> $50k, requires Bank Manager)
(
  'd0000000-0000-0000-0000-000000000006',
  'a0000000-0000-0000-0000-000000000004',
  'home', 280000.00, 5.10,
  360, 1521.46, 280000.00,
  'pending', NULL,
  'First home purchase — 30 year fixed rate mortgage', NOW() - INTERVAL '1 day'
);

-- -----------------------------------------------------------------------------
-- Audit Logs (sample authorization decisions)
-- -----------------------------------------------------------------------------
INSERT INTO audit_logs (
  id, principal_id, principal_type, principal_role,
  action, resource_type, resource_id,
  decision, risk_level, business_context, action_outcome, created_at
) VALUES

-- Allowed: Teller approves auto loan ($22k < $50k limit)
(
  'e0000000-0000-0000-0000-000000000001',
  'a0000000-0000-0000-0000-000000000002', 'User', 'Bank Teller',
  'ApproveLoan', 'Loan', 'd0000000-0000-0000-0000-000000000001',
  'Allow', 'low', 'Auto loan $22,000 — within Bank Teller $50,000 limit', 'success',
  NOW() - INTERVAL '18 months'
),

-- Allowed: Manager approves home loan ($350k > $50k — Manager only)
(
  'e0000000-0000-0000-0000-000000000002',
  'a0000000-0000-0000-0000-000000000001', 'User', 'Bank Manager',
  'ApproveLoan', 'Loan', 'd0000000-0000-0000-0000-000000000003',
  'Allow', 'medium', 'Home mortgage $350,000 — approved by Bank Manager', 'success',
  NOW() - INTERVAL '3 years'
),

-- Denied: Teller attempts to approve $75k loan (exceeds $50k limit)
(
  'e0000000-0000-0000-0000-000000000003',
  'a0000000-0000-0000-0000-000000000002', 'User', 'Bank Teller',
  'ApproveLoan', 'Loan', 'd0000000-0000-0000-0000-000000000004',
  'Deny', 'medium', 'Business loan $75,000 — exceeds Bank Teller approval limit of $50,000', 'failed',
  NOW() - INTERVAL '4 days'
),

-- Allowed: Manager approves $75k business loan
(
  'e0000000-0000-0000-0000-000000000004',
  'a0000000-0000-0000-0000-000000000001', 'User', 'Bank Manager',
  'ApproveLoan', 'Loan', 'd0000000-0000-0000-0000-000000000004',
  'Allow', 'medium', 'Business loan $75,000 — approved by Bank Manager, offered to customer', 'success',
  NOW() - INTERVAL '4 days'
),

-- Denied: Account Holder (Frank) tries to approve a loan
(
  'e0000000-0000-0000-0000-000000000005',
  'a0000000-0000-0000-0000-000000000006', 'User', 'Account Holder',
  'ApproveLoan', 'Loan', 'd0000000-0000-0000-0000-000000000005',
  'Deny', 'high', 'Account Holders are not authorized to approve loans', 'failed',
  NOW() - INTERVAL '7 days'
),

-- Allowed: David Chen transfer $2,000 (within $5k daily limit)
(
  'e0000000-0000-0000-0000-000000000006',
  'a0000000-0000-0000-0000-000000000004', 'User', 'Account Holder',
  'TransferFunds', 'Transaction', 'c0000000-0000-0000-0000-000000000002',
  'Allow', 'low', 'Transfer $2,000 — within Account Holder $5,000 daily limit', 'success',
  NOW() - INTERVAL '25 days'
),

-- Denied: Account Holder attempts $8,000 transfer (exceeds $5k daily limit)
(
  'e0000000-0000-0000-0000-000000000007',
  'a0000000-0000-0000-0000-000000000004', 'User', 'Account Holder',
  'TransferFunds', 'Transaction', NULL,
  'Deny', 'medium', 'Transfer $8,000 rejected — exceeds Account Holder $5,000 daily limit', 'failed',
  NOW() - INTERVAL '12 days'
),

-- Allowed: Teller initiates $15k transfer (> $5k, Manager approved)
(
  'e0000000-0000-0000-0000-000000000008',
  'a0000000-0000-0000-0000-000000000002', 'User', 'Bank Teller',
  'TransferFunds', 'Transaction', 'c0000000-0000-0000-0000-000000000004',
  'Allow', 'medium', 'Transfer $15,000 — Bank Teller initiated, Bank Manager approved', 'success',
  NOW() - INTERVAL '15 days'
),

-- Denied: Account Holder tries to delete own account
(
  'e0000000-0000-0000-0000-000000000009',
  'a0000000-0000-0000-0000-000000000005', 'User', 'Account Holder',
  'DeleteAccount', 'Account', 'b0000000-0000-0000-0000-000000000003',
  'Deny', 'high', 'Account Holders are not authorized to delete accounts', 'failed',
  NOW() - INTERVAL '8 days'
),

-- Allowed: Manager exports audit logs
(
  'e0000000-0000-0000-0000-000000000010',
  'a0000000-0000-0000-0000-000000000001', 'User', 'Bank Manager',
  'ExportAuditLogs', 'AuditLog', NULL,
  'Allow', 'low', 'Quarterly compliance audit log export', 'success',
  NOW() - INTERVAL '1 day'
);

-- =============================================================================
-- USEFUL VIEWS
-- =============================================================================

-- Account summary with owner name
CREATE OR REPLACE VIEW vw_account_summary AS
SELECT
  a.id,
  a.account_number,
  a.account_type,
  a.balance,
  a.status,
  a.interest_rate,
  a.credit_limit,
  a.risk_rating,
  u.name         AS owner_name,
  u.email        AS owner_email,
  u.role         AS owner_role,
  a.created_at
FROM accounts a
JOIN users u ON u.id = a.user_id;

-- Loan summary with applicant and approver names
CREATE OR REPLACE VIEW vw_loan_summary AS
SELECT
  l.id,
  l.type,
  l.amount,
  l.interest_rate,
  l.term_months,
  l.monthly_payment,
  l.remaining_balance,
  l.status,
  l.created_at,
  applicant.name   AS applicant_name,
  applicant.email  AS applicant_email,
  approver.name    AS approved_by_name,
  approver.role    AS approved_by_role,
  l.notes
FROM loans l
JOIN users applicant ON applicant.id = l.user_id
LEFT JOIN users approver ON approver.id = l.approved_by;

-- Daily transfer totals per account holder (for enforcing the $5,000 daily limit)
CREATE OR REPLACE VIEW vw_daily_transfer_totals AS
SELECT
  t.from_account_id,
  a.user_id,
  u.name                  AS account_holder_name,
  u.role,
  DATE(t.created_at)      AS transfer_date,
  SUM(t.amount)           AS daily_total,
  COUNT(*)                AS transfer_count
FROM transactions t
JOIN accounts a ON a.id = t.from_account_id
JOIN users u ON u.id = a.user_id
WHERE t.type = 'transfer'
  AND t.status = 'completed'
GROUP BY t.from_account_id, a.user_id, u.name, u.role, DATE(t.created_at);

-- Audit log report view
CREATE OR REPLACE VIEW vw_audit_report AS
SELECT
  al.id,
  al.created_at,
  u.name                  AS principal_name,
  al.principal_role,
  al.action,
  al.resource_type,
  al.resource_id,
  al.decision,
  al.risk_level,
  al.business_context,
  al.action_outcome,
  al.ip_address
FROM audit_logs al
JOIN users u ON u.id = al.principal_id
ORDER BY al.created_at DESC;

-- =============================================================================
-- END OF SCRIPT
-- =============================================================================
