-- LoanPro demo — schema for the second worked example (a retail loan-origination
-- shop). Mirrors the shape of securebank-demo/db/schema.sql: a `users` identity
-- table the runtime's IDENTITY_QUERY resolves the caller from, plus the domain
-- tables the governed query templates read/write. The engine names none of this;
-- all of it lives in the demo's published artifacts (policy.yaml / query_templates).
--
-- Roles: Applicant (self-service borrower), Loan Officer (owns an assigned
-- pipeline), Underwriter (decides loans), Branch Manager (decides any amount +
-- sees sensitive fields). This is the identity model the standalone loan chatbot
-- lacks and that Prefront governance is built on.

DROP TABLE IF EXISTS default_records   CASCADE;
DROP TABLE IF EXISTS loan_applications CASCADE;
DROP TABLE IF EXISTS credit_scores     CASCADE;
DROP TABLE IF EXISTS applicants        CASCADE;
DROP TABLE IF EXISTS users             CASCADE;

-- Identity table — the deployment's mapping of a signed-in identity to caller
-- attributes. IDENTITY_QUERY aliases these columns onto the contract names the
-- policy binds against (role, user_id). Prefront itself assumes no such table.
CREATE TABLE users (
    user_id         INT PRIMARY KEY,
    name            TEXT NOT NULL,
    email           TEXT NOT NULL UNIQUE,
    role            TEXT NOT NULL CHECK (role IN
        ('Applicant','Loan Officer','Underwriter','Branch Manager','System Admin')),
    status          TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','inactive','suspended')),
    clearance_level INT  NOT NULL DEFAULT 0,
    ssn             TEXT NOT NULL,          -- [SENSITIVE] branch-manager-only
    created_at      DATE NOT NULL DEFAULT CURRENT_DATE
);

-- The borrower's application profile. `user_id` links a self-service Applicant to
-- their own login (NULL for walk-ins an officer entered). `assigned_officer` is
-- the Loan Officer who owns the file — the scope key for view_applications.
CREATE TABLE applicants (
    applicant_id     INT PRIMARY KEY,
    user_id          INT REFERENCES users(user_id),          -- the applicant's own login (nullable)
    full_name        TEXT NOT NULL,
    email            TEXT NOT NULL,
    annual_income    NUMERIC(12,2) NOT NULL,
    employment_years NUMERIC(4,1)  NOT NULL,
    ssn              TEXT NOT NULL,                           -- [SENSITIVE] branch-manager-only
    assigned_officer INT REFERENCES users(user_id),
    created_at       DATE NOT NULL DEFAULT CURRENT_DATE
);

-- Bureau credit score per applicant. `score` is [SENSITIVE] — masked from a Loan
-- Officer, visible to Underwriters and Branch Managers.
CREATE TABLE credit_scores (
    applicant_id INT PRIMARY KEY REFERENCES applicants(applicant_id),
    score        INT  NOT NULL CHECK (score BETWEEN 300 AND 850),
    bureau       TEXT NOT NULL,
    last_updated DATE NOT NULL DEFAULT CURRENT_DATE
);

-- Prior delinquencies (enriches the applicant's risk picture).
CREATE TABLE default_records (
    id             SERIAL PRIMARY KEY,
    applicant_id   INT NOT NULL REFERENCES applicants(applicant_id),
    amount_overdue NUMERIC(12,2) NOT NULL,
    default_date   DATE NOT NULL,
    resolved       BOOLEAN NOT NULL DEFAULT FALSE
);

-- The thing being governed: a loan application an Underwriter/Manager decides.
CREATE TABLE loan_applications (
    loan_id          INT PRIMARY KEY,
    applicant_id     INT NOT NULL REFERENCES applicants(applicant_id),
    requested_amount NUMERIC(12,2) NOT NULL,
    term_months      INT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'pending'
                         CHECK (status IN ('pending','approved','rejected')),
    assigned_officer INT REFERENCES users(user_id),
    decided_by       INT REFERENCES users(user_id),
    created_at       DATE NOT NULL DEFAULT CURRENT_DATE
);

CREATE INDEX idx_applicants_officer ON applicants (assigned_officer);
CREATE INDEX idx_loans_applicant    ON loan_applications (applicant_id);
CREATE INDEX idx_loans_officer      ON loan_applications (assigned_officer);
CREATE INDEX idx_defaults_applicant ON default_records (applicant_id);
