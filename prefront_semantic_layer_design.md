Below is the **final semantic layer design** you can give directly to your agentic coder.

Save this as:

```text
prefront_semantic_layer_design.md
```

---

# Prefront Semantic Layer Design

## 1. Purpose

The Prefront Semantic Layer is a **versioned semantic contract** between business language and physical data sources.

It must map:

```text
Business concepts
  → physical tables/columns
  → approved relationships/joins
  → metrics/calculations
  → sensitivity rules
  → intent bindings
  → query templates
  → MCP tool interfaces
```

The semantic layer is not a loose RAG index of database documentation. It is not a live LLM schema interpreter.

It is a reviewed, versioned, runtime artifact that Prefront uses to safely execute agent requests.

Prefront’s runtime model is:

```text
Agent / Customer LLM
  → MCP Tool / Approved Intent
  → Prefront Runtime
  → Semantic Binding
  → Policy Enforcement
  → Approved Query Template
  → Validation
  → Datasource Execution
  → Decision Trace
```

This follows the current Prefront direction: runtime must use approved intents, templates, policies, validators, and traces rather than fresh LLM SQL generation. 

---

# 2. Design Principle

The semantic layer should behave like a **governed semantic contract**.

```text
Database schema + DB documentation
        ↓
Physical catalog
        ↓
Candidate semantic mapping
        ↓
Human review
        ↓
Published semantic model
        ↓
Intent bindings
        ↓
Generated MCP tools
        ↓
Runtime validation and execution
```

LLMs may suggest semantic mappings at design time, but runtime must use only **published semantic model versions**.

---

# 3. Core Responsibilities

The semantic layer must provide:

```text
1. Datasource registry
2. Physical schema catalog
3. Business entity model
4. Attribute mappings
5. Relationship and join graph
6. Metric definitions
7. Sensitivity classification
8. Access semantics
9. Intent-to-semantic bindings
10. Query-template bindings
11. MCP tool contract generation
12. Runtime semantic validation
13. Semantic trace metadata
```

For the CommerceRisk demo, this directly supports the required Prefront features: intent catalog, query template store, policy enforcement, validator, datasource adapter, approval workflow, and decision traces. 

---

# 4. What the Semantic Layer Produces

For the MVP, produce these artifacts:

```text
semantic_model.yaml
physical_catalog.yaml
relationships.yaml
metrics.yaml
sensitivity.yaml
intent_bindings.yaml
mcp_tools.yaml
semantic_tests.yaml
```

The runtime should load only **published** versions of these artifacts.

---

# 5. Artifact: physical_catalog.yaml

Purpose:

> Describe what physically exists in the datasource.

```yaml
datasource_id: commercerisk_postgres
type: postgresql
schema_version: 1.0
status: published

tables:
  customers:
    primary_key: customer_id
    columns:
      customer_id:
        type: string
        nullable: false
      company_name:
        type: string
        nullable: false
      region_id:
        type: integer
        nullable: false
      status:
        type: string
      segment:
        type: string

  customer_risk_profiles:
    primary_key: risk_profile_id
    columns:
      risk_profile_id:
        type: string
        nullable: false
      customer_id:
        type: string
        nullable: false
      risk_score:
        type: integer
      risk_band:
        type: string
      review_status:
        type: string

  customer_credit_limits:
    primary_key: credit_limit_id
    columns:
      credit_limit_id:
        type: string
        nullable: false
      customer_id:
        type: string
        nullable: false
      credit_limit:
        type: numeric
      outstanding_balance:
        type: numeric
      available_credit:
        type: numeric
      currency:
        type: string

  discount_requests:
    primary_key: request_id
    columns:
      request_id:
        type: string
      customer_id:
        type: string
      order_id:
        type: string
      requested_discount_pct:
        type: numeric
      requested_by:
        type: string
      business_justification:
        type: string
      status:
        type: string

  sensitive_customer_data:
    primary_key: sensitive_data_id
    columns:
      sensitive_data_id:
        type: string
      customer_id:
        type: string
      tax_id:
        type: string
      bank_account_hint:
        type: string
      personal_contact:
        type: string
      pii_classification:
        type: string
```

---

# 6. Artifact: semantic_model.yaml

Purpose:

> Map business concepts to physical tables and columns.

```yaml
semantic_model_id: commercerisk_semantic_model
version: 1.0
status: published
domain: commercerisk
approved_by: policy_admin

entities:
  Customer:
    description: A business customer buying products or services.
    primary_table: customers
    primary_key: customers.customer_id
    synonyms:
      - account
      - client
      - buyer
    attributes:
      customer_id:
        column: customers.customer_id
        type: string
        required: true
      name:
        column: customers.company_name
        type: string
      region_id:
        column: customers.region_id
        type: integer
      status:
        column: customers.status
        type: string
      segment:
        column: customers.segment
        type: string

  CustomerRiskProfile:
    description: Risk profile associated with a customer.
    primary_table: customer_risk_profiles
    primary_key: customer_risk_profiles.risk_profile_id
    attributes:
      customer_id:
        column: customer_risk_profiles.customer_id
        type: string
      risk_score:
        column: customer_risk_profiles.risk_score
        type: integer
      risk_band:
        column: customer_risk_profiles.risk_band
        type: string
      review_status:
        column: customer_risk_profiles.review_status
        type: string

  CustomerCreditLimit:
    description: Credit exposure and available credit for a customer.
    primary_table: customer_credit_limits
    primary_key: customer_credit_limits.credit_limit_id
    attributes:
      customer_id:
        column: customer_credit_limits.customer_id
        type: string
      credit_limit:
        column: customer_credit_limits.credit_limit
        type: numeric
      outstanding_balance:
        column: customer_credit_limits.outstanding_balance
        type: numeric
      available_credit:
        column: customer_credit_limits.available_credit
        type: numeric
      currency:
        column: customer_credit_limits.currency
        type: string

  DiscountRequest:
    description: Request for a discount on behalf of a customer or order.
    primary_table: discount_requests
    primary_key: discount_requests.request_id
    attributes:
      request_id:
        column: discount_requests.request_id
        type: string
      customer_id:
        column: discount_requests.customer_id
        type: string
      requested_discount_pct:
        column: discount_requests.requested_discount_pct
        type: numeric
      requested_by:
        column: discount_requests.requested_by
        type: string
      business_justification:
        column: discount_requests.business_justification
        type: string
      status:
        column: discount_requests.status
        type: string

  SensitiveCustomerData:
    description: Sensitive customer attributes that require restricted access.
    primary_table: sensitive_customer_data
    primary_key: sensitive_customer_data.sensitive_data_id
    restricted: true
    attributes:
      tax_id:
        column: sensitive_customer_data.tax_id
        type: string
        sensitivity_level: restricted
      bank_account_hint:
        column: sensitive_customer_data.bank_account_hint
        type: string
        sensitivity_level: restricted
      personal_contact:
        column: sensitive_customer_data.personal_contact
        type: string
        sensitivity_level: restricted
```

---

# 7. Artifact: relationships.yaml

Purpose:

> Define approved join paths. The agent must not invent joins.

```yaml
relationships:
  Customer_to_RiskProfile:
    from_entity: Customer
    to_entity: CustomerRiskProfile
    join:
      from: customers.customer_id
      to: customer_risk_profiles.customer_id
    cardinality: one_to_one
    approved: true
    restricted: false

  Customer_to_CreditLimit:
    from_entity: Customer
    to_entity: CustomerCreditLimit
    join:
      from: customers.customer_id
      to: customer_credit_limits.customer_id
    cardinality: one_to_one
    approved: true
    restricted: false

  Customer_to_DiscountRequest:
    from_entity: Customer
    to_entity: DiscountRequest
    join:
      from: customers.customer_id
      to: discount_requests.customer_id
    cardinality: one_to_many
    approved: true
    restricted: false

  Customer_to_SensitiveCustomerData:
    from_entity: Customer
    to_entity: SensitiveCustomerData
    join:
      from: customers.customer_id
      to: sensitive_customer_data.customer_id
    cardinality: one_to_one
    approved: true
    restricted: true
    allowed_roles:
      - compliance_officer
      - finance_admin
```

Runtime validator must reject any query using a join that is not listed and approved here.

---

# 8. Artifact: metrics.yaml

Purpose:

> Centralize business calculations so agents do not invent formulas.

```yaml
metrics:
  credit_utilization_pct:
    description: Percentage of assigned credit limit already used.
    expression: "outstanding_balance / NULLIF(credit_limit, 0) * 100"
    required_entities:
      - CustomerCreditLimit
    required_attributes:
      - CustomerCreditLimit.outstanding_balance
      - CustomerCreditLimit.credit_limit
    output_type: percentage

  available_credit:
    description: Remaining available credit for a customer.
    expression: "credit_limit - outstanding_balance"
    required_entities:
      - CustomerCreditLimit
    required_attributes:
      - CustomerCreditLimit.credit_limit
      - CustomerCreditLimit.outstanding_balance
    output_type: currency

  total_discount_exposure:
    description: Total monetary exposure from requested discounts.
    expression: "SUM(order_amount * requested_discount_pct / 100)"
    required_entities:
      - DiscountRequest
      - Order
    output_type: currency

  high_value_customer_total_order_value:
    description: Total value of customer orders.
    expression: "SUM(order_details.unit_price * order_details.quantity)"
    required_entities:
      - Customer
      - Order
      - OrderDetail
    output_type: currency
```

For MVP, you can keep metric expressions as metadata and implement only the metrics needed by approved query templates.

---

# 9. Artifact: sensitivity.yaml

Purpose:

> Define field-level access and result filtering.

```yaml
sensitivity:
  SensitiveCustomerData.tax_id:
    physical_column: sensitive_customer_data.tax_id
    classification: pii
    sensitivity_level: restricted
    default_access: deny
    allowed_roles:
      - compliance_officer
    masking:
      enabled: true
      type: last_four

  SensitiveCustomerData.bank_account_hint:
    physical_column: sensitive_customer_data.bank_account_hint
    classification: financial_sensitive
    sensitivity_level: restricted
    default_access: deny
    allowed_roles:
      - finance_admin
      - compliance_officer

  CustomerCreditLimit.credit_limit:
    physical_column: customer_credit_limits.credit_limit
    classification: confidential_business
    sensitivity_level: confidential
    default_access: deny
    allowed_roles:
      - finance_agent
      - finance_manager
      - executive

  CustomerRiskProfile.risk_score:
    physical_column: customer_risk_profiles.risk_score
    classification: risk_sensitive
    sensitivity_level: confidential
    default_access: deny
    allowed_roles:
      - finance_agent
      - compliance_officer
      - executive
```

Hard rule:

> Sensitive fields must default to deny.

---

# 10. Artifact: intent_bindings.yaml

Purpose:

> Connect approved intents to semantic entities, allowed fields, metrics, templates, policies, and mandatory filters.

```yaml
intent_bindings:
  find_discount_eligible_customers:
    description: Find customers eligible for a requested discount.
    required_entities:
      - Customer
      - CustomerRiskProfile
      - CustomerCreditLimit
    optional_entities:
      - DiscountRequest
    allowed_attributes:
      - Customer.customer_id
      - Customer.name
      - Customer.region_id
      - Customer.segment
      - CustomerRiskProfile.risk_band
      - CustomerCreditLimit.available_credit
      - CustomerCreditLimit.outstanding_balance
    restricted_attributes:
      - SensitiveCustomerData.tax_id
      - SensitiveCustomerData.bank_account_hint
      - SensitiveCustomerData.personal_contact
    allowed_metrics:
      - credit_utilization_pct
    mandatory_filters:
      - semantic_filter_id: active_customers_only
        expression: "Customer.status = 'ACTIVE'"
      - semantic_filter_id: exclude_restricted_risk_band
        expression: "CustomerRiskProfile.risk_band != 'RESTRICTED'"
      - semantic_filter_id: credit_utilization_under_80
        expression: "credit_utilization_pct < 80"
    template_ids:
      - discount_eligibility_query_v1
    policies:
      - discount_approval_policy
      - credit_risk_policy
      - data_access_policy
      - regional_access_policy
    trace_required: true

  calculate_discount_exposure_by_region:
    description: Calculate aggregate discount exposure by region.
    required_entities:
      - Customer
      - DiscountRequest
      - Region
    allowed_attributes:
      - Region.region_name
      - metric.total_discount_exposure
    restricted_attributes:
      - SensitiveCustomerData.tax_id
      - SensitiveCustomerData.bank_account_hint
      - CustomerRiskProfile.risk_score
    allowed_metrics:
      - total_discount_exposure
    output_grain: region
    row_level_data_allowed: false
    template_ids:
      - discount_exposure_by_region_v1
    policies:
      - data_access_policy
      - regional_access_policy
    trace_required: true

  check_customer_credit_status:
    description: Check approved credit status for a customer.
    required_entities:
      - Customer
      - CustomerCreditLimit
      - CustomerRiskProfile
    allowed_attributes:
      - Customer.customer_id
      - Customer.name
      - CustomerCreditLimit.credit_limit
      - CustomerCreditLimit.outstanding_balance
      - CustomerCreditLimit.available_credit
      - CustomerRiskProfile.risk_band
    allowed_metrics:
      - credit_utilization_pct
    template_ids:
      - customer_credit_status_query_v1
    policies:
      - credit_risk_policy
      - data_access_policy
    trace_required: true
```

This is the most important runtime artifact in the semantic layer.

---

# 11. MCP Tool Generation

## Design Decision

Approved Prefront intents should be exposed as **MCP tools**.

The customer’s LLM can map natural language to the right tool, but Prefront controls actual execution.

Use this rule:

> Every approved Prefront intent can become an MCP tool. No raw datasource operation should become an MCP tool.

Expose tools like:

```text
find_discount_eligible_customers
check_customer_credit_status
calculate_discount_exposure_by_region
create_discount_approval_request
show_pending_approvals_for_role
get_decision_trace
```

Do **not** expose tools like:

```text
run_sql
query_database
select_from_table
join_tables
get_table_schema
execute_raw_query
```

---

# 12. Artifact: mcp_tools.yaml

Purpose:

> Define the MCP tool contracts generated from approved intents, semantic bindings, policy metadata, and query templates.

```yaml
mcp_tools:
  find_discount_eligible_customers:
    tool_version: 1.0
    source_intent: find_discount_eligible_customers
    semantic_model_id: commercerisk_semantic_model
    semantic_model_version: 1.0
    description: >
      Find customers eligible for a requested discount. Prefront enforces
      discount approval, credit risk, regional access, and sensitive-data policies.
    allowed_roles:
      - sales_agent
      - sales_manager
      - revenue_manager
    input_schema:
      type: object
      properties:
        discount_percentage:
          type: number
          description: Requested discount percentage.
          minimum: 0
          maximum: 25
        region:
          type: string
          description: Optional region filter.
      required:
        - discount_percentage
    output_schema:
      type: object
      properties:
        status:
          type: string
          enum:
            - allowed
            - blocked
            - approval_required
        trace_id:
          type: string
        rows:
          type: array
    result_shape:
      fields:
        - Customer.customer_id
        - Customer.name
        - Customer.region_id
        - Customer.segment
        - CustomerRiskProfile.risk_band
        - CustomerCreditLimit.available_credit
    policies_enforced:
      - discount_approval_policy
      - credit_risk_policy
      - data_access_policy
      - regional_access_policy
    approval_behavior:
      may_require_approval: true
      approval_condition: "discount_percentage > 15"
      approval_role: VP_SALES
    trace:
      required: true
      trace_fields:
        - intent_id
        - tool_name
        - semantic_model_version
        - template_id
        - policy_evaluations
        - parameters
        - execution_status

  check_customer_credit_status:
    tool_version: 1.0
    source_intent: check_customer_credit_status
    semantic_model_id: commercerisk_semantic_model
    semantic_model_version: 1.0
    description: >
      Check customer credit status using approved credit-risk and data-access policies.
    allowed_roles:
      - finance_agent
      - finance_manager
      - executive
    input_schema:
      type: object
      properties:
        customer_id:
          type: string
      required:
        - customer_id
    result_shape:
      fields:
        - Customer.customer_id
        - Customer.name
        - CustomerCreditLimit.credit_limit
        - CustomerCreditLimit.outstanding_balance
        - CustomerCreditLimit.available_credit
        - CustomerRiskProfile.risk_band
    policies_enforced:
      - credit_risk_policy
      - data_access_policy
    trace:
      required: true

  calculate_discount_exposure_by_region:
    tool_version: 1.0
    source_intent: calculate_discount_exposure_by_region
    semantic_model_id: commercerisk_semantic_model
    semantic_model_version: 1.0
    description: >
      Calculate aggregate discount exposure by region without exposing row-level sensitive data.
    allowed_roles:
      - executive
      - finance_manager
      - revenue_manager
    input_schema:
      type: object
      properties:
        region:
          type: string
          description: Optional region filter.
      required: []
    row_level_data_allowed: false
    result_shape:
      fields:
        - Region.region_name
        - metric.total_discount_exposure
    policies_enforced:
      - data_access_policy
      - regional_access_policy
    trace:
      required: true
```

---

# 13. Runtime Use With MCP Tools

## Runtime Flow

```text
User asks customer LLM:
"Find customers eligible for 20% discount in South region."

Customer LLM:
Calls MCP tool:
find_discount_eligible_customers

Prefront MCP Server:
Receives typed tool call.

Prefront Runtime:
1. Authenticates caller.
2. Checks role and scope.
3. Maps tool to approved intent.
4. Loads published semantic model.
5. Loads intent semantic binding.
6. Validates input schema.
7. Checks allowed entities and attributes.
8. Loads approved query template.
9. Applies policy rules.
10. Applies mandatory filters.
11. Validates SQL/query AST.
12. Executes, blocks, or pauses for approval.
13. Stores decision trace.
14. Returns result to customer LLM.
```

## Example MCP Tool Call

```json
{
  "tool": "find_discount_eligible_customers",
  "arguments": {
    "discount_percentage": 20,
    "region": "South"
  }
}
```

## Example Prefront Response

```json
{
  "status": "approval_required",
  "intent_id": "find_discount_eligible_customers",
  "tool_name": "find_discount_eligible_customers",
  "template_id": "discount_eligibility_query_v1",
  "semantic_model_version": "commercerisk_semantic_model:1.0",
  "policy_result": {
    "decision": "approval_required",
    "approval_role": "VP_SALES",
    "reason": "Discount above 15% requires VP Sales approval."
  },
  "execution": {
    "executed": false,
    "reason": "Pending approval"
  },
  "trace_id": "trace_001"
}
```

The customer’s LLM explains the result. Prefront makes the decision.

---

# 14. Query Template Binding

Query templates must declare semantic entities and policies.

```yaml
query_templates:
  discount_eligibility_query_v1:
    intent_id: find_discount_eligible_customers
    semantic_model_id: commercerisk_semantic_model
    semantic_entities:
      - Customer
      - CustomerRiskProfile
      - CustomerCreditLimit
    required_policies:
      - discount_approval_policy
      - credit_risk_policy
      - regional_access_policy
      - data_access_policy
    parameters:
      discount_percentage:
        type: number
      region:
        type: string
        optional: true
    sql_template: |
      SELECT
        c.customer_id,
        c.company_name,
        c.region_id,
        c.segment,
        cr.risk_band,
        cl.available_credit
      FROM customers c
      JOIN customer_risk_profiles cr
        ON c.customer_id = cr.customer_id
      JOIN customer_credit_limits cl
        ON c.customer_id = cl.customer_id
      WHERE c.status = 'ACTIVE'
        AND cr.risk_band != 'RESTRICTED'
        AND (cl.outstanding_balance / NULLIF(cl.credit_limit, 0) * 100) < 80
        AND (:region IS NULL OR c.region_id = :region)
```

Validator must check:

```text
1. Template maps to approved intent.
2. Template uses only approved entities.
3. Selected fields are allowed by intent binding.
4. Joins match approved semantic relationships.
5. Mandatory filters are present.
6. Restricted fields are absent.
7. Required policies are evaluated.
8. Parameters are typed and safely bound.
```

---

# 15. Runtime Decision Trace Contribution

Every trace must include semantic metadata.

```json
{
  "trace_id": "trace_001",
  "tool_name": "find_discount_eligible_customers",
  "matched_intent": "find_discount_eligible_customers",
  "semantic_model_version": "commercerisk_semantic_model:1.0",
  "entities_used": [
    "Customer",
    "CustomerRiskProfile",
    "CustomerCreditLimit"
  ],
  "relationships_used": [
    "Customer_to_RiskProfile",
    "Customer_to_CreditLimit"
  ],
  "attributes_selected": [
    "Customer.customer_id",
    "Customer.name",
    "Customer.region_id",
    "Customer.segment",
    "CustomerRiskProfile.risk_band",
    "CustomerCreditLimit.available_credit"
  ],
  "mandatory_filters_applied": [
    "Customer.status = ACTIVE",
    "CustomerRiskProfile.risk_band != RESTRICTED",
    "credit_utilization_pct < 80"
  ],
  "restricted_attributes_blocked": [
    "SensitiveCustomerData.tax_id",
    "SensitiveCustomerData.bank_account_hint"
  ],
  "policy_evaluations": [
    {
      "policy_id": "discount_approval_policy",
      "result": "approval_required",
      "reason": "Discount above 15% requires VP Sales approval."
    }
  ],
  "execution_status": "blocked_pending_approval"
}
```

This connects semantic context to the broader Prefront decision-trace value proposition. The context graph thesis is that the valuable layer is not merely rules, but durable decision traces showing what context, policy, exception, and approval path were used at decision time. 

---

# 16. Database Model

Use PostgreSQL with JSONB. Do not use a graph database for MVP.

## datasources

```sql
CREATE TABLE datasources (
  datasource_id UUID PRIMARY KEY,
  name TEXT NOT NULL,
  type TEXT NOT NULL,
  connection_ref TEXT,
  status TEXT NOT NULL DEFAULT 'active',
  created_at TIMESTAMP DEFAULT now()
);
```

## physical_tables

```sql
CREATE TABLE physical_tables (
  table_id UUID PRIMARY KEY,
  datasource_id UUID REFERENCES datasources(datasource_id),
  schema_name TEXT,
  table_name TEXT NOT NULL,
  description TEXT,
  primary_key TEXT,
  metadata JSONB NOT NULL DEFAULT '{}',
  created_at TIMESTAMP DEFAULT now()
);
```

## physical_columns

```sql
CREATE TABLE physical_columns (
  column_id UUID PRIMARY KEY,
  table_id UUID REFERENCES physical_tables(table_id),
  column_name TEXT NOT NULL,
  data_type TEXT NOT NULL,
  nullable BOOLEAN,
  description TEXT,
  metadata JSONB NOT NULL DEFAULT '{}',
  created_at TIMESTAMP DEFAULT now()
);
```

## semantic_models

```sql
CREATE TABLE semantic_models (
  semantic_model_id UUID PRIMARY KEY,
  name TEXT NOT NULL,
  domain TEXT NOT NULL,
  version TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'draft',
  approved_by TEXT,
  approved_at TIMESTAMP,
  created_at TIMESTAMP DEFAULT now()
);
```

## semantic_entities

```sql
CREATE TABLE semantic_entities (
  entity_id UUID PRIMARY KEY,
  semantic_model_id UUID REFERENCES semantic_models(semantic_model_id),
  entity_key TEXT NOT NULL,
  display_name TEXT NOT NULL,
  description TEXT,
  primary_table_id UUID REFERENCES physical_tables(table_id),
  synonyms TEXT[] DEFAULT '{}',
  metadata JSONB NOT NULL DEFAULT '{}'
);
```

## semantic_attributes

```sql
CREATE TABLE semantic_attributes (
  attribute_id UUID PRIMARY KEY,
  entity_id UUID REFERENCES semantic_entities(entity_id),
  attribute_key TEXT NOT NULL,
  display_name TEXT NOT NULL,
  physical_column_id UUID REFERENCES physical_columns(column_id),
  business_type TEXT,
  sensitivity_level TEXT DEFAULT 'normal',
  default_access TEXT DEFAULT 'allow',
  metadata JSONB NOT NULL DEFAULT '{}'
);
```

## semantic_relationships

```sql
CREATE TABLE semantic_relationships (
  relationship_id UUID PRIMARY KEY,
  semantic_model_id UUID REFERENCES semantic_models(semantic_model_id),
  relationship_key TEXT NOT NULL,
  from_entity_id UUID REFERENCES semantic_entities(entity_id),
  to_entity_id UUID REFERENCES semantic_entities(entity_id),
  join_json JSONB NOT NULL,
  cardinality TEXT,
  approved BOOLEAN DEFAULT false,
  restricted BOOLEAN DEFAULT false,
  metadata JSONB NOT NULL DEFAULT '{}'
);
```

## semantic_metrics

```sql
CREATE TABLE semantic_metrics (
  metric_id UUID PRIMARY KEY,
  semantic_model_id UUID REFERENCES semantic_models(semantic_model_id),
  metric_key TEXT NOT NULL,
  description TEXT,
  expression TEXT NOT NULL,
  required_entities TEXT[] DEFAULT '{}',
  required_attributes TEXT[] DEFAULT '{}',
  output_type TEXT,
  metadata JSONB NOT NULL DEFAULT '{}'
);
```

## intent_semantic_bindings

```sql
CREATE TABLE intent_semantic_bindings (
  binding_id UUID PRIMARY KEY,
  intent_id TEXT NOT NULL,
  semantic_model_id UUID REFERENCES semantic_models(semantic_model_id),
  required_entities TEXT[] DEFAULT '{}',
  optional_entities TEXT[] DEFAULT '{}',
  allowed_attributes TEXT[] DEFAULT '{}',
  restricted_attributes TEXT[] DEFAULT '{}',
  mandatory_filters JSONB NOT NULL DEFAULT '[]',
  allowed_metrics TEXT[] DEFAULT '{}',
  template_ids TEXT[] DEFAULT '{}',
  policy_ids TEXT[] DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'draft'
);
```

## mcp_tool_contracts

```sql
CREATE TABLE mcp_tool_contracts (
  tool_contract_id UUID PRIMARY KEY,
  tool_name TEXT NOT NULL,
  tool_version TEXT NOT NULL,
  intent_id TEXT NOT NULL,
  semantic_model_id UUID REFERENCES semantic_models(semantic_model_id),
  input_schema JSONB NOT NULL,
  output_schema JSONB NOT NULL,
  allowed_roles TEXT[] DEFAULT '{}',
  result_shape JSONB NOT NULL DEFAULT '{}',
  policy_ids TEXT[] DEFAULT '{}',
  template_ids TEXT[] DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'draft',
  approved_by TEXT,
  approved_at TIMESTAMP,
  created_at TIMESTAMP DEFAULT now()
);
```

---

# 17. APIs

## Design-Time APIs

```http
POST /design/semantic/datasources
POST /design/semantic/datasources/{datasource_id}/introspect
POST /design/semantic/documents/upload
POST /design/semantic/models/suggest
GET  /design/semantic/models/{semantic_model_id}
POST /design/semantic/entities/{entity_id}/approve
POST /design/semantic/relationships/{relationship_id}/approve
POST /design/semantic/metrics/{metric_id}/approve
POST /design/semantic/models/{semantic_model_id}/publish
```

## MCP Tool Generation APIs

```http
POST /design/semantic/mcp-tools/generate
GET  /design/semantic/mcp-tools
GET  /design/semantic/mcp-tools/{tool_name}
POST /design/semantic/mcp-tools/{tool_name}/approve
POST /design/semantic/mcp-tools/publish
```

## Runtime APIs

```http
GET  /mcp/tools
POST /mcp/tools/{tool_name}/call

POST /prefront/resolve
POST /prefront/execute
GET  /prefront/traces/{trace_id}
```

The MCP call endpoint should internally route into Prefront resolve/execute/trace logic.

---

# 18. Semantic Mapper Prompt

Use this prompt for candidate semantic mapping.

```text
You are helping build a semantic model for Prefront.

Prefront is a governed access layer between AI agents and enterprise data sources.
The runtime must use approved intents, approved query templates, policy rules,
semantic mappings, validators, and decision traces.

Your output is only a candidate semantic model.
It is not approved for runtime use.

Rules:
- Do not invent tables or columns.
- Do not create relationships without evidence.
- Mark uncertain mappings as ambiguous.
- Mark potentially sensitive fields.
- Prefer deterministic, reviewable mappings.
- Return JSON only.

Input:
- Physical schema
- Database documentation
- Known business terms
- Existing policies
- Existing intents

Return JSON:
{
  "entities": [
    {
      "entity_key": "Customer",
      "description": "...",
      "primary_table": "customers",
      "synonyms": [],
      "attributes": [
        {
          "attribute_key": "name",
          "physical_column": "customers.company_name",
          "business_type": "string",
          "sensitivity_level": "normal",
          "confidence": 0.0,
          "evidence": "..."
        }
      ],
      "ambiguities": []
    }
  ],
  "relationships": [
    {
      "relationship_key": "Customer_to_Order",
      "from_entity": "Customer",
      "to_entity": "Order",
      "join": {
        "from": "customers.customer_id",
        "to": "orders.customer_id"
      },
      "cardinality": "one_to_many",
      "confidence": 0.0,
      "evidence": "foreign key or documentation reference",
      "ambiguities": []
    }
  ],
  "metrics": [
    {
      "metric_key": "credit_utilization_pct",
      "expression": "outstanding_balance / NULLIF(credit_limit, 0) * 100",
      "required_entities": [],
      "required_attributes": [],
      "confidence": 0.0,
      "ambiguities": []
    }
  ],
  "sensitivity_candidates": [
    {
      "physical_column": "sensitive_customer_data.tax_id",
      "classification": "pii",
      "recommended_default_access": "deny",
      "confidence": 0.0,
      "evidence": "column name contains tax_id"
    }
  ]
}
```

---

# 19. Validation Rules

Reject semantic model publication if:

```text
1. Entity maps to a table that does not exist.
2. Attribute maps to a column that does not exist.
3. Relationship join references unknown columns.
4. Relationship is unapproved.
5. Metric references unknown entities or attributes.
6. Sensitive fields default to allow.
7. Intent binding has no approved query template.
8. Intent binding references unknown entities.
9. MCP tool exposes restricted attributes.
10. MCP tool has no approved intent.
11. Query template uses unapproved relationships.
12. Query template omits mandatory filters.
13. Runtime trace config is missing.
14. Semantic model has no version.
15. Semantic model has no approval record.
```

---

# 20. UI Requirements

Build these pages.

## 20.1 Datasource Catalog

Shows:

```text
Datasource
Tables
Columns
Primary keys
Foreign keys
Last introspected
Documentation coverage
```

## 20.2 Business Entity Mapper

Left side:

```text
Physical tables and columns
```

Right side:

```text
Business entities and attributes
```

Actions:

```text
Approve mapping
Edit entity name
Add synonym
Mark attribute sensitive
Map column to entity attribute
```

## 20.3 Relationship Graph

Shows:

```text
Customer → Orders
Customer → Risk Profile
Customer → Credit Limit
Customer → Sensitive Customer Data
Customer → Discount Requests
```

Actions:

```text
Approve join
Reject join
Mark join restricted
Set cardinality
Set mandatory filter
```

## 20.4 Metrics Builder

Shows:

```text
Metric name
Expression
Required entities
Required fields
Output type
Policies affected
```

## 20.5 Intent Binding Review

Shows:

```text
Intent
Required entities
Allowed attributes
Restricted attributes
Mandatory filters
Allowed metrics
Query templates
Policies
```

## 20.6 MCP Tool Registry

Shows:

```text
Tool name
Source intent
Input schema
Output schema
Allowed roles
Policies enforced
Template used
Semantic model version
Approval behavior
Trace behavior
Status
```

Actions:

```text
Generate tools
Approve tool
Reject tool
Publish tool registry
```

---

# 21. MVP Scope

Build this first:

```text
1. PostgreSQL schema introspection.
2. DB documentation upload.
3. Candidate entity mapping.
4. Candidate relationship mapping.
5. Candidate sensitivity classification.
6. Human review and publish semantic model.
7. Intent-to-semantic binding.
8. Query-template binding.
9. MCP tool generation from approved intents.
10. Runtime semantic validation.
11. Semantic trace metadata.
```

Do not build yet:

```text
Complex graph database
Automatic lineage
Multi-datasource joins
Semantic metric marketplace
Fine-grained ontology engine
Auto-remediation of schema drift
Automatic production update on schema change
```

---

# 22. Implementation Order

Give this exact order to the coding agent:

```text
1. Create semantic database tables.
2. Implement datasource registration.
3. Implement PostgreSQL schema introspection.
4. Store physical tables and columns.
5. Implement DB documentation upload.
6. Implement candidate semantic mapper using strict JSON output.
7. Implement entity/attribute review APIs.
8. Implement relationship review APIs.
9. Implement sensitivity classification review.
10. Implement semantic model publish API.
11. Implement intent_semantic_bindings storage.
12. Implement query-template binding validation.
13. Implement MCP tool contract generator.
14. Implement MCP tool registry APIs.
15. Implement runtime MCP tool list endpoint.
16. Implement runtime MCP tool call endpoint.
17. Route MCP tool call to Prefront resolve/execute/trace.
18. Implement semantic validator.
19. Add semantic metadata into decision traces.
20. Build UI pages for mapper, relationships, intent bindings, and MCP tool registry.
```

---

# 23. Hard Rules

```text
1. Do not use LLMs at runtime for semantic interpretation.
2. LLMs may only suggest semantic mappings at design time.
3. Runtime must use only published semantic model versions.
4. Every semantic entity must map to a real physical table.
5. Every semantic attribute must map to a real physical column or approved expression.
6. Every relationship must have an approved join path.
7. Sensitive fields must default to deny.
8. Every intent must declare allowed semantic entities and attributes.
9. Every query template must declare semantic entities used.
10. Every MCP tool must map to an approved intent.
11. No raw SQL MCP tool is allowed.
12. No raw datasource query MCP tool is allowed.
13. Every MCP tool must have typed input schema.
14. Every MCP tool must have result shape metadata.
15. Every runtime trace must record semantic model version.
16. Schema drift must not silently update runtime semantics.
17. Semantic mappings must be reviewable, versioned, and testable.
```

---

# 24. Final Design Statement

The Prefront Semantic Layer is:

> **A reviewed, versioned semantic contract that turns business language into safe, approved, enforceable data access.**

It powers:

```text
Customer LLM tool selection
Approved intent execution
Policy enforcement
Query-template validation
Sensitive-field blocking
MCP tool generation
Decision trace enrichment
```

This is the correct design because it lets the customer keep their LLM while Prefront remains the governed control plane.

