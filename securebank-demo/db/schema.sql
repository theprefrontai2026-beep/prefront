-- ============================================================================
-- Prefront SecureBank — Sample Application Schema
-- ----------------------------------------------------------------------------
-- A retail-banking dataset: customers + staff, their accounts, the transaction
-- ledger, and loan applications. This is the ENTERPRISE DATA SOURCE that
-- Prefront governs; it contains no governance logic itself. Columns marked
-- [GOVERNED] or [SENSITIVE] exist so Prefront's policies have something to act
-- on. Vocabulary mirrors the bundled `securebank` domain pack (roles, intents,
-- fields) so the skill-builder / semantic-layer can ground rules against it.
--
-- Target: PostgreSQL 16+
-- ============================================================================

DROP TABLE IF EXISTS transactions CASCADE;
DROP TABLE IF EXISTS loans        CASCADE;
DROP TABLE IF EXISTS accounts     CASCADE;
DROP TABLE IF EXISTS users        CASCADE;

-- ----------------------------------------------------------------------------
-- users — both CUSTOMERS (Account Holder) and STAFF (Teller/Manager/Admin).
-- The caller "acts as" one of these; their role + user_id are what Prefront
-- binds into policy (ownership + role rules). ssn is manager-only PII.
-- ----------------------------------------------------------------------------
CREATE TABLE users (
    user_id         INT  PRIMARY KEY,
    name            TEXT NOT NULL,
    email           TEXT NOT NULL UNIQUE,
    role            TEXT NOT NULL                              -- [GOVERNED] Role policy
        CHECK (role IN ('Account Holder', 'Bank Teller', 'Bank Manager', 'System Admin')),
    status          TEXT NOT NULL DEFAULT 'active'             -- [GOVERNED]
        CHECK (status IN ('active', 'inactive', 'suspended')),
    clearance_level INT  NOT NULL DEFAULT 0,                   -- staff transfer-authority band
    ssn             TEXT NOT NULL,                             -- [SENSITIVE] Bank Manager only
    created_at      DATE NOT NULL DEFAULT CURRENT_DATE
);

-- ----------------------------------------------------------------------------
-- accounts — owned by a user. Ownership (user_id) drives the OWN_DATA_ONLY
-- policy: an Account Holder may touch only their own accounts.
-- ----------------------------------------------------------------------------
CREATE TABLE accounts (
    account_id   INT  PRIMARY KEY,
    user_id      INT  NOT NULL REFERENCES users(user_id),      -- [GOVERNED] ownership
    account_type TEXT NOT NULL
        CHECK (account_type IN ('checking', 'savings', 'credit')),
    balance      NUMERIC(14,2) NOT NULL DEFAULT 0,             -- [GOVERNED] Balance policy
    status       TEXT NOT NULL DEFAULT 'active'                -- [GOVERNED] Account-state policy
        CHECK (status IN ('active', 'inactive', 'suspended')),
    opened_at    DATE NOT NULL DEFAULT CURRENT_DATE
);

-- ----------------------------------------------------------------------------
-- transactions — the ledger. initiate_transfer writes a 'transfer_out' row.
-- ----------------------------------------------------------------------------
CREATE TABLE transactions (
    txn_id               INT  PRIMARY KEY,
    account_id           INT  NOT NULL REFERENCES accounts(account_id),
    txn_type             TEXT NOT NULL
        CHECK (txn_type IN ('deposit', 'withdrawal', 'transfer_in', 'transfer_out')),
    amount               NUMERIC(14,2) NOT NULL,
    counterparty_account INT,
    memo                 TEXT,
    created_at           DATE NOT NULL DEFAULT CURRENT_DATE
);

-- ----------------------------------------------------------------------------
-- loans — applications + decisions. decide_loan is Bank Manager only.
-- ----------------------------------------------------------------------------
CREATE TABLE loans (
    loan_id      INT  PRIMARY KEY,
    user_id      INT  NOT NULL REFERENCES users(user_id),
    amount       NUMERIC(14,2) NOT NULL,
    status       TEXT NOT NULL DEFAULT 'pending'               -- [GOVERNED] Loan-decision policy
        CHECK (status IN ('pending', 'approved', 'rejected')),
    credit_score INT,                                          -- [GOVERNED]
    risk_rating  TEXT                                          -- [GOVERNED]
        CHECK (risk_rating IN ('low', 'medium', 'high')),
    decided_by   INT  REFERENCES users(user_id),               -- a manager; NULL while pending
    created_at   DATE NOT NULL DEFAULT CURRENT_DATE
);

-- Access-path indexes the governed templates will use.
CREATE INDEX idx_accounts_owner ON accounts(user_id);
CREATE INDEX idx_txn_account    ON transactions(account_id);
CREATE INDEX idx_loans_user     ON loans(user_id);
