"""Canonical pydantic models for the semantic layer.

Two families of shapes:

  * **Candidate** models (``Candidate*``) — what the LLM mapper returns. Always
    ``review_status='pending'``; carry ``confidence`` + ``evidence`` so a human
    can review them. They are NOT runtime-usable until promoted.
  * **Published** models (``PhysicalCatalog``, ``SemanticModel``, ``Relationship``,
    ``SensitivityRule``, ``IntentBinding``, ``McpTool``) — the reviewed contract
    the runtime loads. These mirror the artifact shapes in
    ``prefront_semantic_layer_design.md`` §5–§12.

Constrained vocabularies (cardinality, sensitivity_level, default_access,
decision) are ``Literal`` enums: an unknown value is a hard validation failure,
because it is not enforceable. This is the LLM↔runtime trust boundary.
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator


def _as_str_list(v: Any) -> list[str]:
    """Coerce a value into a list of strings.

    The mapper LLM sometimes returns ``ambiguities``/``synonyms`` as objects
    (e.g. ``{"ambiguity": "...", "description": "..."}``) instead of plain
    strings. Flatten those to strings rather than rejecting the whole payload.
    """
    if v is None:
        return []
    if not isinstance(v, list):
        v = [v]
    out: list[str] = []
    for x in v:
        if isinstance(x, str):
            out.append(x)
        elif isinstance(x, dict):
            out.append("; ".join(f"{k}: {val}" for k, val in x.items()))
        else:
            out.append(str(x))
    return out

# --- Constrained vocabularies -------------------------------------------------

Cardinality = Literal["one_to_one", "one_to_many", "many_to_one", "many_to_many"]
SensitivityLevel = Literal["normal", "confidential", "restricted"]
AccessDefault = Literal["allow", "deny"]
ReviewStatus = Literal["pending", "approved", "rejected", "needs_clarification"]
ToolStatus = Literal["draft", "approved", "published"]


# --- Physical catalog (design §5) --------------------------------------------


class PhysicalColumn(BaseModel):
    name: str
    type: str
    nullable: bool = True
    is_primary_key: bool = False
    enum_values: Optional[list[str]] = None
    # Markers lifted from DDL comments ([SENSITIVE], [GOVERNED]).
    markers: list[str] = Field(default_factory=list)


class ForeignKey(BaseModel):
    from_columns: list[str]
    to_table: str
    to_columns: list[str]


class PhysicalTable(BaseModel):
    name: str
    primary_key: list[str] = Field(default_factory=list)
    columns: list[PhysicalColumn] = Field(default_factory=list)
    foreign_keys: list[ForeignKey] = Field(default_factory=list)

    def column(self, name: str) -> Optional[PhysicalColumn]:
        return next((c for c in self.columns if c.name.lower() == name.lower()), None)


class PhysicalCatalog(BaseModel):
    datasource_id: str
    type: str = "postgresql"
    schema_version: str = "1.0"
    status: str = "published"
    tables: list[PhysicalTable] = Field(default_factory=list)

    def table(self, name: str) -> Optional[PhysicalTable]:
        return next((t for t in self.tables if t.name.lower() == name.lower()), None)

    def has_column(self, qualified: str) -> bool:
        """``qualified`` is 'table.column'."""
        if "." not in qualified:
            return False
        t, c = qualified.split(".", 1)
        tbl = self.table(t)
        return bool(tbl and tbl.column(c))


# --- Semantic model (design §6) ----------------------------------------------


class SemanticAttribute(BaseModel):
    attribute_key: str
    column: str  # 'table.column'
    type: str = "string"
    required: bool = False
    sensitivity_level: SensitivityLevel = "normal"


class SemanticEntity(BaseModel):
    entity_key: str
    description: str = ""
    primary_table: str
    primary_key: str = ""  # 'table.column'
    synonyms: list[str] = Field(default_factory=list)
    restricted: bool = False
    attributes: list[SemanticAttribute] = Field(default_factory=list)

    def attribute(self, key: str) -> Optional[SemanticAttribute]:
        return next((a for a in self.attributes if a.attribute_key == key), None)


class SemanticModel(BaseModel):
    semantic_model_id: str
    version: str = "1.0"
    status: str = "draft"
    domain: str = "general"
    approved_by: Optional[str] = None
    generated_by: str = ""
    entities: list[SemanticEntity] = Field(default_factory=list)

    def entity(self, key: str) -> Optional[SemanticEntity]:
        return next((e for e in self.entities if e.entity_key == key), None)


# --- Relationships (design §7) -----------------------------------------------


class Join(BaseModel):
    from_: str = Field(alias="from")  # 'table.column'
    to: str  # 'table.column'

    model_config = {"populate_by_name": True}


class Relationship(BaseModel):
    relationship_key: str
    from_entity: str
    to_entity: str
    join: Join
    cardinality: Cardinality = "one_to_many"
    approved: bool = False
    restricted: bool = False
    allowed_roles: list[str] = Field(default_factory=list)


# --- Sensitivity (design §9) -------------------------------------------------


class Masking(BaseModel):
    enabled: bool = False
    type: Optional[str] = None  # e.g. 'last_four'


class SensitivityRule(BaseModel):
    key: str  # 'Entity.attribute'
    physical_column: str  # 'table.column'
    classification: str = "confidential_business"
    sensitivity_level: SensitivityLevel = "confidential"
    default_access: AccessDefault = "deny"  # hard rule §23.7: sensitive -> deny
    allowed_roles: list[str] = Field(default_factory=list)
    masking: Optional[Masking] = None


# --- Intent bindings (design §10) --------------------------------------------


class MandatoryFilter(BaseModel):
    semantic_filter_id: str
    expression: str


class IntentBinding(BaseModel):
    intent_id: str
    description: str = ""
    required_entities: list[str] = Field(default_factory=list)
    optional_entities: list[str] = Field(default_factory=list)
    allowed_attributes: list[str] = Field(default_factory=list)
    restricted_attributes: list[str] = Field(default_factory=list)
    mandatory_filters: list[MandatoryFilter] = Field(default_factory=list)
    template_ids: list[str] = Field(default_factory=list)
    policies: list[str] = Field(default_factory=list)
    trace_required: bool = True


# --- MCP tools (design §12) --------------------------------------------------


class ApprovalBehavior(BaseModel):
    may_require_approval: bool = False
    approval_condition: Optional[str] = None
    approval_role: Optional[str] = None


class TraceSpec(BaseModel):
    required: bool = True
    trace_fields: list[str] = Field(default_factory=list)


class McpTool(BaseModel):
    tool_name: str
    tool_version: str = "1.0"
    source_intent: str
    semantic_model_id: str
    semantic_model_version: str = "1.0"
    description: str = ""
    allowed_roles: list[str] = Field(default_factory=list)
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    result_shape: dict[str, Any] = Field(default_factory=dict)
    template_ids: list[str] = Field(default_factory=list)
    policies_enforced: list[str] = Field(default_factory=list)
    approval_behavior: Optional[ApprovalBehavior] = None
    trace: TraceSpec = Field(default_factory=TraceSpec)
    status: ToolStatus = "draft"


# --- Query templates (design §14) --------------------------------------------


class TemplateParameter(BaseModel):
    name: str
    type: str = "string"
    required: bool = True


class ResultColumn(BaseModel):
    name: str
    sensitivity: SensitivityLevel = "normal"


class WriteAction(BaseModel):
    """The mutation a write intent performs once its precheck clears.

    Fully DECLARATIVE: the runtime write executor is pure mechanism, so every
    piece of application vocabulary lives here, generated at design time against
    the catalog and human-reviewed with the template:
      * column_map      request param -> physical column (order_value -> order_total)
      * caller_columns  physical column -> trusted caller attribute (rep_id <- rep_id)
      * defaults        literal values for NOT NULL enum columns without a DB default
      * autofill        generic tokens: next_int (max+1 for an int PK), current_date
    """

    table: str
    # The mutation shape. 'insert' = create (column_map/defaults/autofill apply);
    # 'update'/'delete' target an existing row matched by key_columns (+ caller
    # scope) — never an unbounded mutation.
    kind: Literal["insert", "update", "delete"] = "insert"
    params: list[str] = Field(default_factory=list)
    column_map: dict[str, str] = Field(default_factory=dict)
    caller_columns: dict[str, str] = Field(default_factory=dict)
    defaults: dict[str, Any] = Field(default_factory=dict)
    autofill: dict[str, Literal["next_int", "current_date"]] = Field(default_factory=dict)
    # WHERE keys for update/delete (the row identity — primary key columns).
    key_columns: list[str] = Field(default_factory=list)


class QueryTemplate(BaseModel):
    template_id: str
    intent_id: str
    description: str = ""  # the tool's meaning, surfaced to the agent by the MCP server
    semantic_model_id: str
    semantic_model_version: str = "1.0"
    # 'read' = the SELECT is the answer; 'precheck' = a read-only SELECT that
    # gathers the governed inputs a *write* intent's policies need before the
    # write runs (the write itself stays a runtime action).
    kind: Literal["read", "precheck"] = "read"
    semantic_entities: list[str] = Field(default_factory=list)
    read_only: bool = True
    dialect: str = "postgres"
    sql: str = ""
    parameters: list[TemplateParameter] = Field(default_factory=list)
    required_caller_context: list[str] = Field(default_factory=list)
    result_columns: list[ResultColumn] = Field(default_factory=list)
    # Governed columns a precheck fetches for the gateway to evaluate policies on.
    # These are gateway-internal decision inputs — NOT returned to the agent —
    # so they may include restricted columns.
    decision_inputs: list[ResultColumn] = Field(default_factory=list)
    write_action: Optional[WriteAction] = None
    required_policies: list[str] = Field(default_factory=list)
    # Block/approval rules the runtime evaluates against decision inputs +
    # request params (kept human-readable; not inlined as SQL).
    runtime_policy_predicates: list[str] = Field(default_factory=list)
    status: ToolStatus = "published"


# --- Candidate models (LLM mapper output, design §18) ------------------------


class CandidateAttribute(BaseModel):
    attribute_key: str
    physical_column: str  # 'table.column'
    business_type: str = "string"
    sensitivity_level: SensitivityLevel = "normal"
    confidence: float = Field(ge=0.0, le=1.0, default=0.0)
    evidence: str = ""


class CandidateEntity(BaseModel):
    entity_key: str
    description: str = ""
    primary_table: str
    synonyms: list[str] = Field(default_factory=list)
    attributes: list[CandidateAttribute] = Field(default_factory=list)
    ambiguities: list[str] = Field(default_factory=list)

    @field_validator("ambiguities", "synonyms", mode="before")
    @classmethod
    def _coerce_str_list(cls, v: Any) -> list[str]:
        return _as_str_list(v)


class CandidateRelationship(BaseModel):
    relationship_key: str
    from_entity: str
    to_entity: str
    join: Join
    cardinality: Cardinality = "one_to_many"
    confidence: float = Field(ge=0.0, le=1.0, default=0.0)
    evidence: str = ""
    ambiguities: list[str] = Field(default_factory=list)

    @field_validator("ambiguities", mode="before")
    @classmethod
    def _coerce_amb(cls, v: Any) -> list[str]:
        return _as_str_list(v)


class CandidateSensitivity(BaseModel):
    physical_column: str  # 'table.column'
    classification: str = "confidential_business"
    recommended_default_access: AccessDefault = "deny"
    sensitivity_level: SensitivityLevel = "confidential"
    confidence: float = Field(ge=0.0, le=1.0, default=0.0)
    evidence: str = ""


class CandidateSemanticModel(BaseModel):
    """The full strict-JSON payload the mapper LLM must return."""

    entities: list[CandidateEntity] = Field(default_factory=list)
    relationships: list[CandidateRelationship] = Field(default_factory=list)
    sensitivity_candidates: list[CandidateSensitivity] = Field(default_factory=list)
    review_status: ReviewStatus = "pending"
