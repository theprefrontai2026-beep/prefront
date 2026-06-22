Saved. This is a strong correction to the plan.

The new positioning should be:

> **Prefront is a governed data-access runtime between AI agents and enterprise data sources. It uses LLMs to build and maintain context, but avoids relying on LLMs for every live query.**

That makes the product much more enterprise-ready.

---

# Updated Architecture Principle


```text
Agent/User Request
      ↓
Prefront Gateway
      ↓
Intent + Policy + Semantic Resolution
      ↓
Approved Query Template / Query Plan
      ↓
Deterministic Validation
      ↓
Datasource Execution
      ↓
Decision Trace
```

The LLM should not be the live execution engine. It should be the **context builder**, **policy interpreter**, and **template assistant**.

---

# Revised Role of LLMs in Prefront

## Use LLMs for design-time tasks

Good uses:

```text
Business Policy document → extracted rules
Schema + docs → semantic map
Natural-language examples → reusable intents
Business questions → candidate query templates
Human review assistant
Rule conflict explanation
Documentation generation
```

These are acceptable because they can be reviewed, versioned, tested and approved.

---

## Minimize LLMs at runtime

Runtime should be mostly deterministic:

```text
Classify request
Match to approved intent
Select approved template
Bind parameters
Apply policy
Validate query
Execute
Trace
```

The runtime path should avoid:

```text
Every request → LLM → fresh SQL
```

because that creates:

```text
Unpredictable output
Harder certification
Higher latency
Higher cost
Harder audit
Harder debugging
Customer distrust
```

This is exactly the right concern.

---

# Updated Product Definition

## Prefront

**Prefront is a governed access layer between AI agents and enterprise data sources. It converts policies, schemas and business semantics into approved data-access plans that agents can safely invoke.**

The agent should not directly query the database.

The agent calls Prefront:

```http
POST /ask
```

or:

```http
POST /query-plan/execute
```

Prefront decides:

```text
Is this request allowed?
Which approved intent does it match?
Which data source can answer it?
Which query template should be used?
Which filters are mandatory?
Which fields are restricted?
Is approval required?
What trace must be recorded?
```

---

# New Runtime Flow

```text
AI Agent
  │
  │  "Show customers eligible for 20% discount"
  ▼
Prefront Runtime Gateway
  │
  ├─ Authenticate agent/user
  ├─ Classify intent
  ├─ Match approved query template
  ├─ Bind safe parameters
  ├─ Apply policy constraints
  ├─ Validate final query
  ├─ Check approval requirement
  └─ Execute against datasource
        │
        ▼
Database / API / Warehouse
        │
        ▼
Result returned through Prefront
        │
        ▼
Decision trace stored
```

---

# Updated Core Modules

## 1. Design-Time Context Builder

Uses LLMs.

Responsibilities:

```text
Extract rules from policy documents
Convert rules into structured policy objects
Map schema to business entities
Suggest query templates
Generate test cases
Identify conflicts or missing metadata
```

Output:

```text
Approved skills
Semantic map
Intent catalog
Query templates
Policy constraints
Validation rules
```

---

## 2. Intent Catalog

This becomes central.

Instead of generating SQL from scratch every time, Prefront maintains approved intents.

Example:

```yaml
intent_id: find_discount_eligible_customers
description: Find customers eligible for a discount
allowed_roles:
  - sales_ops
  - revenue_manager
parameters:
  discount_percentage:
    type: number
    max: 25
  region:
    type: string
    optional: true
approval_rules:
  - if: discount_percentage > 15
    require: VP_APPROVAL
template_id: discount_eligibility_query_v1
```

---

## 3. Query Template Store

Approved SQL templates live here.

Example:

```sql
SELECT
  c.customer_id,
  c.customer_name,
  c.segment,
  d.discount_percentage
FROM customers c
JOIN discounts d
  ON c.customer_id = d.customer_id
WHERE d.discount_percentage <= :discount_percentage
  AND c.status = 'ACTIVE'
  AND c.risk_category != 'RESTRICTED'
```

The agent never gets to freely invent the query unless explicitly allowed in a sandbox or review mode.

---

## 4. Policy Enforcement Engine

This should be deterministic.

Example policy:

```yaml
policy_id: discount_above_15_requires_vp_approval
condition:
  field: discount_percentage
  operator: ">"
  value: 15
effect:
  approval_required: true
  approver_role: VP_SALES
```

Runtime output:

```json
{
  "allowed": true,
  "approval_required": true,
  "approval_role": "VP_SALES",
  "reason": "Discount above 15% requires VP approval."
}
```

---

## 5. Query Planner

Instead of “LLM generates SQL,” use:

```text
Intent → Template → Parameters → Policy filters → Final Query
```

Example:

```json
{
  "intent": "find_discount_eligible_customers",
  "template": "discount_eligibility_query_v1",
  "params": {
    "discount_percentage": 20,
    "region": "South"
  },
  "mandatory_filters": [
    "customer.status = ACTIVE",
    "customer.risk_category != RESTRICTED"
  ]
}
```

---

## 6. Validator

Validation becomes stronger because the expected structure is known.

Checks:

```text
Template is approved
Parameters are typed
Mandatory filters are present
Restricted columns are absent
Datasource is allowed
User role is allowed
Approval rule was evaluated
SQL AST matches allowed pattern
```

This is much more predictable than validating arbitrary LLM SQL.

---

## 7. Decision Trace Store

Trace now records deterministic execution decisions.

```json
{
  "trace_id": "trace_001",
  "agent_id": "sales_copilot",
  "user_id": "user_123",
  "request": "Show customers eligible for 20% discount",
  "matched_intent": "find_discount_eligible_customers",
  "template_id": "discount_eligibility_query_v1",
  "policy_evaluations": [
    {
      "policy_id": "discount_above_15_requires_vp_approval",
      "result": "approval_required"
    }
  ],
  "parameters": {
    "discount_percentage": 20
  },
  "execution_status": "blocked_pending_approval"
}
```

This is a better audit story.

---

# Updated 90-Day MVP

## MVP Claim

Not:

> “We use an LLM to generate SQL.”

Instead:

> **Prefront lets AI agents safely access enterprise data through governed, policy-aware, pre-approved query plans.**

Much stronger.

---

## Day 1-15: Define Domain and Approved Intents

Pick one domain:

```text
Credit approval
Discount approval
Vendor risk
Claims review
```

For the first MVP, I would still pick **discount approval** or **credit approval**.

Create:

```text
1 policy document
1 schema
10-20 approved intents
10-20 approved query templates
20 test requests
Expected policy outcomes
```

---

## Day 16-35: Build Design-Time Builder

Build LLM-assisted, human-reviewed generation of:

```text
Rules
Semantic entities
Intent definitions
Query templates
Validation rules
```

Output should be editable and versioned.

---

## Day 36-55: Build Runtime Gateway

Core API:

```http
POST /prefront/resolve
POST /prefront/execute
POST /prefront/trace
```

Runtime should do:

```text
request classification
intent matching
template lookup
parameter extraction
policy enforcement
query assembly
SQL validation
execution decision
trace creation
```

LLM use here should be optional and controlled.

---

## Day 56-70: Build Policy and Validation Layer

Build deterministic controls:

```text
Allowed intents
Allowed roles
Allowed templates
Parameter constraints
Mandatory filters
Approval thresholds
Restricted fields
SQL AST validation
```

This is the heart of the product.

---

## Day 71-90: Build Demo and Benchmark

Demo should show:

```text
Agent tries to access database directly → risky/uncontrolled
Agent calls Prefront → governed, predictable, auditable
Prefront blocks unsafe request
Prefront allows safe request
Prefront requires approval for threshold-crossing request
Trace shows exactly why
```

---

# Revised Architecture

```text
                  ┌────────────────────┐
                  │      AI Agent       │
                  └─────────┬──────────┘
                            │
                            ▼
                  ┌────────────────────┐
                  │   Prefront Gateway  │
                  └─────────┬──────────┘
                            │
          ┌─────────────────┼─────────────────┐
          ▼                 ▼                 ▼
 ┌────────────────┐ ┌────────────────┐ ┌────────────────┐
 │ Intent Catalog │ │ Policy Engine  │ │ Semantic Layer │
 └───────┬────────┘ └───────┬────────┘ └───────┬────────┘
         │                  │                  │
         └──────────────────┼──────────────────┘
                            ▼
                  ┌────────────────────┐
                  │ Query Template     │
                  │ / Query Plan Store │
                  └─────────┬──────────┘
                            ▼
                  ┌────────────────────┐
                  │ Validator          │
                  └─────────┬──────────┘
                            ▼
                  ┌────────────────────┐
                  │ Datasource Adapter │
                  └─────────┬──────────┘
                            ▼
                  ┌────────────────────┐
                  │ DB / Warehouse/API │
                  └────────────────────┘

Design-time side:

Policy Docs + Schema Docs + Examples
          ↓
LLM-assisted Builder
          ↓
Human Review
          ↓
Approved Skills / Intents / Templates / Policies
```

---

# Updated Product Modules

```text
1. Prefront Gateway
2. Design-Time Skill Builder
3. Semantic Mapper
4. Intent Catalog
5. Query Template Store
6. Policy Enforcement Engine
7. Query Planner
8. SQL / Query Validator
9. Datasource Adapter
10. Decision Trace Store
```

The important addition is:

```text
Intent Catalog + Query Template Store
```

That is what makes the system predictable.

---

# Updated Positioning

Use this:

> **Prefront is a governed access layer that lets AI agents query enterprise data through approved, policy-aware query plans instead of unpredictable live-generated SQL.**

Avoid saying:

> “We generate SQL using LLMs.”

Say:

> “We use LLMs to help build and maintain the context layer, but runtime execution is policy-driven, template-based and auditable.”

That is a much better enterprise pitch.

