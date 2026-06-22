# 2. Design-Time Skill Builder

## Purpose

The **Design-Time Skill Builder** converts business policy documents into governed Prefront artifacts.

It should take messy source documents like:

```text
PDF
DOCX
Markdown
HTML
Confluence export
Policy text
SOP documents
```

and produce reviewed, versioned runtime assets:

```text
Policy skills
Structured rules
Intent constraints
Approval rules
Restricted fields
Mandatory filters
Test cases
Trace requirements
```

This module is **not** used to answer live agent requests directly. It prepares artifacts that runtime Prefront can enforce deterministically.

This matches the Prefront direction we agreed: LLMs are useful for design-time work, while runtime should use approved intents, templates, policies, validators, and traces instead of fresh LLM SQL. 

---

# Core Design Principle

The Skill Builder should behave like a **policy compiler**.

```text
Business Policy Document
        ↓
Document Extraction
        ↓
Canonical Markdown
        ↓
Policy Clause Detection
        ↓
Candidate Rule Extraction
        ↓
Human Review
        ↓
Approved Policy Skill
        ↓
Runtime Artifacts
```

The output should not be just a Markdown summary.

It should produce artifacts Prefront can enforce.

---

# What the Skill Builder Produces

For each source policy document, it should produce four things:

```text
1. source_policy.md
2. policy_skill.yaml
3. extracted_rules.yaml
4. test_cases.yaml
```

## 1. `source_policy.md`

Clean, normalized Markdown version of the original document.

Purpose:

```text
Human-readable policy reference
Source preservation
Citation support
Review support
```

## 2. `policy_skill.yaml`

High-level skill metadata.

Example:

```yaml
skill_id: discount_approval_policy
name: Discount Approval Policy
version: 1.0
status: draft
domain: sales_discounting
source_document: discount_policy.pdf
owner: revenue_operations
effective_from: 2026-01-01
requires_human_review: true
applies_to:
  - find_discount_eligible_customers
  - create_discount_approval_request
  - calculate_discount_exposure_by_region
```

## 3. `extracted_rules.yaml`

Machine-readable rules.

Example:

```yaml
rules:
  - rule_id: discount_up_to_10_allowed
    type: approval_threshold
    source:
      document: discount_policy.pdf
      section: "3.1 Discount Thresholds"
      page: 4
    condition:
      field: discount_percentage
      operator: "<="
      value: 10
    effect:
      decision: allow
      approval_required: false
    confidence: 0.91
    review_status: pending

  - rule_id: discount_above_15_requires_vp
    type: approval_threshold
    source:
      document: discount_policy.pdf
      section: "3.1 Discount Thresholds"
      page: 4
    condition:
      field: discount_percentage
      operator: ">"
      value: 15
    effect:
      decision: approval_required
      approver_role: VP_SALES
    confidence: 0.94
    review_status: pending
```

## 4. `test_cases.yaml`

Generated policy tests.

Example:

```yaml
test_cases:
  - test_id: discount_8_percent_allowed
    input:
      role: sales_manager
      discount_percentage: 8
    expected:
      decision: allow
      approval_required: false

  - test_id: discount_20_percent_requires_vp
    input:
      role: sales_manager
      discount_percentage: 20
    expected:
      decision: approval_required
      approver_role: VP_SALES
```

This is important. If you cannot test the extracted rule, it is not ready for runtime.

---

# Skill Builder Architecture

```text
                    ┌────────────────────────┐
                    │  Policy Document Upload │
                    └───────────┬────────────┘
                                │
                                ▼
                    ┌────────────────────────┐
                    │ Document Extractor      │
                    │ PDF/DOCX/MD/Text        │
                    └───────────┬────────────┘
                                │
                                ▼
                    ┌────────────────────────┐
                    │ Markdown Normalizer     │
                    │ headings/tables/clauses │
                    └───────────┬────────────┘
                                │
                                ▼
                    ┌────────────────────────┐
                    │ Clause Segmenter        │
                    │ sections/rules/exceptions│
                    └───────────┬────────────┘
                                │
                                ▼
                    ┌────────────────────────┐
                    │ LLM Rule Extractor      │
                    │ candidate rules only    │
                    └───────────┬────────────┘
                                │
                                ▼
                    ┌────────────────────────┐
                    │ Rule Normalizer         │
                    │ schema validation       │
                    └───────────┬────────────┘
                                │
                                ▼
                    ┌────────────────────────┐
                    │ Conflict Detector       │
                    │ overlaps/gaps/conflicts │
                    └───────────┬────────────┘
                                │
                                ▼
                    ┌────────────────────────┐
                    │ Human Review UI         │
                    │ approve/edit/reject     │
                    └───────────┬────────────┘
                                │
                                ▼
                    ┌────────────────────────┐
                    │ Approved Skill Registry │
                    │ versioned artifacts     │
                    └────────────────────────┘
```

---

# Module Breakdown

## 1. Document Upload Service

Accepts:

```text
.pdf
.docx
.md
.txt
.html
```

Stores:

```text
original_file
file_hash
uploaded_by
uploaded_at
document_type
domain
owner
version
```

Do not overwrite documents. Every upload should create a new immutable source version.

---

## 2. Document Extractor

Use deterministic extraction first.

Recommended implementation:

```text
DOCX → mammoth or python-docx
PDF text → PyMuPDF / pdfplumber
Scanned PDF → OCR later, not MVP
Markdown → direct parse
HTML → BeautifulSoup/readability
```

For MVP, support:

```text
.md
.txt
.docx
text-based PDF
```

Skip scanned PDFs for now unless needed.

---

## 3. Markdown Normalizer

Convert extracted content into canonical Markdown.

Goals:

```text
Preserve headings
Preserve tables
Preserve numbered sections
Preserve bullets
Preserve page/section references
Create paragraph IDs
```

Example:

```markdown
# Discount Approval Policy

Source: discount_policy.pdf
Version: 2026.01

## 3.1 Discount Thresholds

[page:4 paragraph:p004]

Discounts above 15% require VP Sales approval.
```

This source trace matters because Prefront’s final decision trace should be able to say which policy section caused a block or approval requirement.

---

## 4. Clause Segmenter

Split the normalized Markdown into policy clauses.

Clause types:

```text
definition
eligibility_rule
approval_threshold
restriction
exception
role_permission
data_access_rule
regional_rule
audit_requirement
fallback_or_escalation
```

Example clause object:

```json
{
  "clause_id": "clause_003_001",
  "document_id": "discount_policy_v1",
  "section": "3.1 Discount Thresholds",
  "page": 4,
  "text": "Discounts above 15% require VP Sales approval.",
  "clause_type": "approval_threshold"
}
```

---

## 5. LLM Rule Extractor

The LLM should extract **candidate rules**, not approved runtime rules.

Input:

```text
One clause or small section
Known domain
Known schema terms if available
Expected output JSON schema
```

Output:

```json
{
  "candidate_rules": [
    {
      "rule_id": "discount_above_15_requires_vp",
      "rule_type": "approval_threshold",
      "condition": {
        "field": "discount_percentage",
        "operator": ">",
        "value": 15
      },
      "effect": {
        "decision": "approval_required",
        "approver_role": "VP_SALES"
      },
      "source_clause_id": "clause_003_001",
      "confidence": 0.94,
      "ambiguities": []
    }
  ]
}
```

Hard rule:

> LLM output cannot become runtime configuration until it passes schema validation and human approval.

---

## 6. Rule Normalizer

This converts messy extracted rules into canonical Prefront policy objects.

It should enforce:

```text
Known rule types
Known operators
Known fields
Known roles
Known decision effects
Required source citation
Required confidence
Required review status
```

Bad rule:

```yaml
condition: customer is risky
effect: be careful
```

Good rule:

```yaml
condition:
  field: risk_score
  operator: ">"
  value: 750
effect:
  decision: approval_required
  approver_role: FINANCE_REVIEWER
```

---

## 7. Conflict Detector

Detect problems before review.

Examples:

```text
Two rules define different thresholds for the same approval.
A rule references an unknown role.
A rule references a field not in the semantic map.
A rule has an exception but no base rule.
A discount threshold has a gap.
A policy says allow in one section and block in another.
```

Example output:

```yaml
conflicts:
  - conflict_id: conflict_001
    severity: high
    type: threshold_overlap
    rules:
      - discount_above_15_requires_vp
      - discount_above_20_requires_cfo
    message: "Two approval rules overlap for discounts above 20%."
    recommended_action: "Clarify precedence."
```

---

## 8. Human Review UI

This is non-negotiable for enterprise trust.

Reviewer actions:

```text
Approve rule
Reject rule
Edit rule
Merge rules
Split rule
Mark as ambiguous
Assign owner
Set effective date
Set expiration date
Map to intent
Map to query template
Add test case
```

The UI should show side-by-side:

```text
Original source clause
Extracted rule
Linked intent/template
Generated tests
Conflicts/warnings
```

---

## 9. Skill Registry

After approval, store versioned artifacts.

```text
skills/
  discount_approval_policy/
    v1/
      source_policy.md
      policy_skill.yaml
      extracted_rules.yaml
      test_cases.yaml
      review_log.yaml
```

Approved rules become available to:

```text
Intent Catalog
Policy Engine
Query Planner
Validator
Approval Workflow
Decision Trace Store
```

The CommerceRisk demo already requires policy enforcement, validators, approvals, and decision traces, so this Skill Builder directly feeds the demo runtime. 

---

# Data Model

## `source_documents`

```sql
CREATE TABLE source_documents (
  document_id UUID PRIMARY KEY,
  file_name TEXT NOT NULL,
  file_type TEXT NOT NULL,
  file_hash TEXT NOT NULL,
  domain TEXT NOT NULL,
  owner TEXT,
  version TEXT,
  status TEXT NOT NULL DEFAULT 'uploaded',
  uploaded_by TEXT,
  uploaded_at TIMESTAMP DEFAULT now()
);
```

## `document_sections`

```sql
CREATE TABLE document_sections (
  section_id UUID PRIMARY KEY,
  document_id UUID REFERENCES source_documents(document_id),
  section_path TEXT,
  heading TEXT,
  page_start INT,
  page_end INT,
  markdown TEXT NOT NULL,
  created_at TIMESTAMP DEFAULT now()
);
```

## `policy_clauses`

```sql
CREATE TABLE policy_clauses (
  clause_id UUID PRIMARY KEY,
  document_id UUID REFERENCES source_documents(document_id),
  section_id UUID REFERENCES document_sections(section_id),
  clause_type TEXT,
  source_text TEXT NOT NULL,
  page_number INT,
  paragraph_ref TEXT,
  created_at TIMESTAMP DEFAULT now()
);
```

## `candidate_rules`

```sql
CREATE TABLE candidate_rules (
  candidate_rule_id UUID PRIMARY KEY,
  clause_id UUID REFERENCES policy_clauses(clause_id),
  rule_key TEXT NOT NULL,
  rule_type TEXT NOT NULL,
  rule_json JSONB NOT NULL,
  confidence NUMERIC,
  review_status TEXT NOT NULL DEFAULT 'pending',
  reviewer_notes TEXT,
  created_at TIMESTAMP DEFAULT now(),
  updated_at TIMESTAMP DEFAULT now()
);
```

## `approved_policy_rules`

```sql
CREATE TABLE approved_policy_rules (
  policy_rule_id UUID PRIMARY KEY,
  rule_key TEXT NOT NULL,
  domain TEXT NOT NULL,
  rule_type TEXT NOT NULL,
  rule_json JSONB NOT NULL,
  source_document_id UUID REFERENCES source_documents(document_id),
  source_clause_id UUID REFERENCES policy_clauses(clause_id),
  version TEXT NOT NULL,
  effective_from DATE,
  effective_to DATE,
  status TEXT NOT NULL DEFAULT 'active',
  approved_by TEXT,
  approved_at TIMESTAMP,
  created_at TIMESTAMP DEFAULT now()
);
```

## `skill_versions`

```sql
CREATE TABLE skill_versions (
  skill_version_id UUID PRIMARY KEY,
  skill_id TEXT NOT NULL,
  version TEXT NOT NULL,
  domain TEXT NOT NULL,
  status TEXT NOT NULL,
  artifact_json JSONB NOT NULL,
  approved_by TEXT,
  approved_at TIMESTAMP,
  created_at TIMESTAMP DEFAULT now()
);
```

---

# APIs

## Upload document

```http
POST /design/skills/documents/upload
```

Request:

```json
{
  "domain": "discount_approval",
  "owner": "revenue_operations",
  "version": "2026.01"
}
```

Response:

```json
{
  "document_id": "doc_001",
  "status": "uploaded"
}
```

---

## Extract Markdown

```http
POST /design/skills/documents/{document_id}/extract
```

Response:

```json
{
  "document_id": "doc_001",
  "status": "markdown_generated",
  "sections_count": 8
}
```

---

## Segment clauses

```http
POST /design/skills/documents/{document_id}/segment
```

Response:

```json
{
  "document_id": "doc_001",
  "clauses_created": 34
}
```

---

## Extract candidate rules

```http
POST /design/skills/documents/{document_id}/extract-rules
```

Response:

```json
{
  "document_id": "doc_001",
  "candidate_rules_created": 18,
  "requires_review": true
}
```

---

## Get candidate rules

```http
GET /design/skills/candidate-rules?document_id=doc_001
```

---

## Approve candidate rule

```http
POST /design/skills/candidate-rules/{candidate_rule_id}/approve
```

Request:

```json
{
  "approved_by": "policy_admin",
  "effective_from": "2026-01-01",
  "version": "1.0"
}
```

---

## Reject candidate rule

```http
POST /design/skills/candidate-rules/{candidate_rule_id}/reject
```

Request:

```json
{
  "rejected_by": "policy_admin",
  "reason": "Rule is ambiguous and requires clarification from business owner."
}
```

---

## Publish skill version

```http
POST /design/skills/{skill_id}/publish
```

Response:

```json
{
  "skill_id": "discount_approval_policy",
  "version": "1.0",
  "status": "published"
}
```

---

# Output Artifact Format

## Skill file

```yaml
skill_id: discount_approval_policy
name: Discount Approval Policy
domain: discount_approval
version: 1.0
status: active
source_documents:
  - document_id: doc_001
    file_name: discount_policy.pdf
    file_hash: sha256:abc123
rules:
  - rule_id: discount_above_15_requires_vp
    type: approval_threshold
    source:
      document_id: doc_001
      page: 4
      section: "3.1 Discount Thresholds"
      paragraph_ref: "p004"
    condition:
      field: discount_percentage
      operator: ">"
      value: 15
    effect:
      decision: approval_required
      approver_role: VP_SALES
    applies_to_intents:
      - find_discount_eligible_customers
      - create_discount_approval_request
    trace_required: true
```

---

# UI Design

## Page 1: Document Library

Shows:

```text
Document name
Domain
Version
Upload status
Extraction status
Rule count
Review status
Published skill version
```

Actions:

```text
Upload document
Extract Markdown
View source
Extract rules
Publish skill
```

---

## Page 2: Markdown Viewer

Left side:

```text
Original extracted Markdown
Section headings
Page references
Clause IDs
```

Right side:

```text
Detected clauses
Candidate rules
Warnings
```

---

## Page 3: Candidate Rule Review

For each candidate rule:

```text
Source clause
Extracted condition
Extracted effect
Affected intents
Affected fields
Confidence
Warnings/conflicts
```

Actions:

```text
Approve
Reject
Edit
Request clarification
Generate test case
```

---

## Page 4: Conflict Review

Shows:

```text
Overlapping thresholds
Missing roles
Unknown fields
Ambiguous wording
Conflicting allow/block effects
Missing approval path
```

---

## Page 5: Published Skill Registry

Shows:

```text
Skill ID
Domain
Version
Status
Approved by
Effective date
Rule count
Linked intents
```

---

# MVP Scope

For the first implementation, build only this:

```text
1. Upload Markdown / DOCX / text-based PDF
2. Convert to canonical Markdown
3. Segment into clauses
4. Extract candidate rules using LLM
5. Validate candidate rules against JSON schema
6. Human approve/reject/edit
7. Publish approved skill YAML
8. Generate test cases
```

Do **not** build yet:

```text
OCR for scanned PDFs
Complex multi-document policy conflict resolution
Full enterprise approval workflows
Fine-tuning
Policy simulation engine
Automatic runtime deployment
```

---

# Prompt for Rule Extraction

Use a strict JSON prompt. Something like this:

```text
You are extracting candidate runtime policy rules for Prefront.

Prefront is a governed runtime layer between AI agents and enterprise data sources.
Your job is to extract candidate rules from the provided policy clause.

Rules must be machine-enforceable.
Do not summarize.
Do not invent missing conditions.
If a rule is ambiguous, mark it ambiguous.
If the clause is only explanatory text, return no rules.

Return only JSON matching the schema.

Policy clause:
{{clause_text}}

Known domain:
{{domain}}

Known roles:
{{known_roles}}

Known fields:
{{known_fields}}

Known intents:
{{known_intents}}

Required JSON shape:
{
  "candidate_rules": [
    {
      "rule_key": "string_snake_case",
      "rule_type": "approval_threshold | data_access | regional_access | restriction | exception | audit_requirement | mandatory_filter",
      "condition": {},
      "effect": {},
      "applies_to_intents": [],
      "requires_trace": true,
      "confidence": 0.0,
      "ambiguities": [],
      "source_evidence": "short exact phrase from clause"
    }
  ]
}
```

---

# Example End-to-End

Input policy clause:

```text
Discounts above 15% require approval from the VP of Sales.
```

Candidate rule:

```yaml
rule_key: discount_above_15_requires_vp_sales
rule_type: approval_threshold
condition:
  field: discount_percentage
  operator: ">"
  value: 15
effect:
  decision: approval_required
  approver_role: VP_SALES
applies_to_intents:
  - find_discount_eligible_customers
  - create_discount_approval_request
requires_trace: true
confidence: 0.95
source_evidence: "Discounts above 15% require approval from the VP of Sales."
review_status: pending
```

Published runtime rule:

```yaml
rule_key: discount_above_15_requires_vp_sales
version: 1.0
status: active
condition:
  field: discount_percentage
  operator: ">"
  value: 15
effect:
  decision: approval_required
  approver_role: VP_SALES
trace_required: true
approved_by: policy_admin
approved_at: "2026-06-06T10:00:00Z"
```

Runtime trace later records:

```json
{
  "request": "Find customers eligible for 20% discount",
  "matched_intent": "find_discount_eligible_customers",
  "policy_evaluations": [
    {
      "rule_key": "discount_above_15_requires_vp_sales",
      "result": "approval_required",
      "source": {
        "document": "discount_policy.pdf",
        "section": "3.1 Discount Thresholds",
        "page": 4
      }
    }
  ]
}
```

This connects design-time policy extraction to runtime decision trace, which is the moat. Foundation Capital’s context-graph thesis emphasizes that the valuable enterprise layer is not just rules, but decision traces showing how rules were applied in specific cases, including approvals and exceptions. 

---

# Implementation Order

Give this to the coding agent:

```text
1. Create source_documents, document_sections, policy_clauses, candidate_rules, approved_policy_rules, skill_versions tables.
2. Implement document upload endpoint.
3. Implement Markdown extraction for .md, .txt, .docx, and text-based .pdf.
4. Implement clause segmenter.
5. Implement LLM-based candidate rule extractor with strict JSON schema.
6. Implement rule schema validator.
7. Implement candidate rule review APIs.
8. Implement publish skill API.
9. Build Document Library UI.
10. Build Candidate Rule Review UI.
11. Build Published Skill Registry UI.
12. Generate skill YAML and test_cases YAML.
```

---

# Hard Rules

```text
1. LLM output is always candidate output.
2. Runtime artifacts must require human approval.
3. Every rule must cite source document, section, and clause.
4. Every approved rule must be versioned.
5. Every approved rule must have status: active, draft, retired, or superseded.
6. Every approval-required or block rule must be testable.
7. Do not allow ambiguous clauses into runtime without review.
8. Do not store only summaries; preserve source clauses.
9. Do not build runtime prompt injection as the enforcement mechanism.
10. The final output must be executable policy objects, not just documentation.
```

---

# My Recommendation

Build the Skill Builder as the first serious Prefront admin module.

It should produce this pipeline:

```text
Policy Document
  → Markdown
  → Clauses
  → Candidate Rules
  → Human Review
  → Approved Skill
  → Runtime Policy Engine
  → Decision Trace
```

That is the right design. It keeps the LLM useful but contained. Runtime stays predictable. Auditors get source evidence. And Prefront starts accumulating the decision context layer instead of becoming yet another RAG wrapper.

