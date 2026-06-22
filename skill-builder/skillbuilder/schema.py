"""Canonical schema for the Skill Builder pipeline.

The shapes here follow ``skill-builder/design.md`` (rule_key / single condition /
effect.decision / applies_to_intents / requires_trace / confidence /
source_evidence). They are the contract between the *untrusted* LLM extractor
and every downstream consumer.

Hard rules enforced by these models:
  * LLM output is always *candidate* output (`CandidateRule`, review_status
    defaults to ``pending``). It cannot be an `ApprovedRule` without an explicit,
    human-supplied approval.
  * Every rule must cite its source clause (`source_clause_id` on the candidate;
    a full `Source` block once published).
  * Operators, rule types and decisions are constrained to known enums — an
    unknown value is a hard validation failure, because it is not enforceable.
"""

from __future__ import annotations

import re
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

# A value that is essentially a number wearing currency/percent/unit decoration:
# "USD 50,000", "100,000", "15%", "90 days". Captures the numeric core so it can
# be compared at runtime. Plain strings ("watch", "order creator") never match.
_NUMERICISH = re.compile(
    r"^\s*(?:USD|EUR|GBP|\$|€|£)?\s*"
    r"([0-9][0-9,]*(?:\.[0-9]+)?)\s*"
    r"(?:%|percent|usd|days?|weeks?|months?|years?)?\s*$",
    re.I,
)


def normalize_value(v: Any) -> Any:
    """Coerce numeric-looking strings to int/float; recurse into lists.

    Leaves genuine strings, booleans, and None untouched.
    """
    if isinstance(v, list):
        return [normalize_value(x) for x in v]
    if isinstance(v, str):
        s = v.strip()
        low = s.lower()
        if low in ("true", "false"):
            return low == "true"
        if low in ("null", "none", ""):
            return None
        m = _NUMERICISH.match(s)
        if m:
            num = m.group(1).replace(",", "")
            try:
                return int(num) if "." not in num else float(num)
            except ValueError:
                return v
    return v

# --- Constrained vocabularies -------------------------------------------------

Operator = Literal["==", "!=", ">", "<", ">=", "<=", "in", "not_in"]

# Rule types from the design.md extraction prompt.
RuleType = Literal[
    "approval_threshold",
    "data_access",
    "regional_access",
    "restriction",
    "exception",
    "audit_requirement",
    "mandatory_filter",
]

# Runtime decisions a rule can produce.
Decision = Literal[
    "allow",
    "approval_required",
    "block",
    "mask",
    "escalate",
]

ReviewStatus = Literal["pending", "approved", "rejected", "needs_clarification"]

# Clause types the segmenter labels (design.md "Clause Segmenter").
ClauseType = Literal[
    "definition",
    "eligibility_rule",
    "approval_threshold",
    "restriction",
    "exception",
    "role_permission",
    "data_access_rule",
    "regional_rule",
    "audit_requirement",
    "fallback_or_escalation",
    "explanatory",
]


# --- Document / clause structures --------------------------------------------


class Section(BaseModel):
    """A heading-delimited region of the normalized markdown."""

    section_id: str
    section_path: str  # e.g. "3.1 Discount Thresholds"
    heading: str
    page_start: Optional[int] = None
    page_end: Optional[int] = None
    markdown: str


# Disposition assigned to every clause (design.md §7 step 5 / §11 clause ledger).
# No clause may remain unprocessed.
Disposition = Literal[
    "rule_candidate_required",
    "atom_candidate_required",
    "definition_only",
    "related_policy_reference",
    "unresolved",
    "non_enforceable_context",
    "duplicate",
    "needs_human_review",
    "rule_extracted",
    "atom_extracted",
]


class Clause(BaseModel):
    """One atomic policy statement carved out of a section."""

    clause_id: str
    document_id: str
    section_id: Optional[str] = None
    section_path: str = ""
    page_number: Optional[int] = None
    paragraph_ref: Optional[str] = None
    clause_type: ClauseType = "explanatory"
    disposition: Optional[Disposition] = None
    source_text: str


# --- Rule structures ----------------------------------------------------------


class Condition(BaseModel):
    """A single field/operator/value test (design.md uses one condition obj)."""

    field: str
    operator: Operator
    value: Any

    @field_validator("field")
    @classmethod
    def _field_nonempty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("condition.field must be a non-empty field name")
        return v


class Effect(BaseModel):
    """What the runtime does when the condition holds."""

    decision: Decision
    approval_required: Optional[bool] = None
    approver_role: Optional[str] = None
    # Fields masked/restricted, for data_access / mask decisions.
    restricted_fields: Optional[list[str]] = None
    message: Optional[str] = None


def _coerce_conditions(data: Any) -> Any:
    """Accept legacy singular ``condition`` and normalize all condition values.

    Runs before field validation so both the LLM (which may emit ``condition``
    or ``conditions``) and hand-edited rules land on the canonical
    ``conditions: [...]`` list with numeric-looking values coerced.
    """
    if not isinstance(data, dict):
        return data
    conds = data.get("conditions")
    if conds is None and data.get("condition") is not None:
        conds = [data["condition"]]
    if isinstance(conds, dict):  # a single object given under "conditions"
        conds = [conds]
    if isinstance(conds, list):
        for c in conds:
            if isinstance(c, dict) and "value" in c:
                c["value"] = normalize_value(c["value"])
        data = {**data, "conditions": conds}
        data.pop("condition", None)
    return data


class CandidateRule(BaseModel):
    """LLM-extracted rule. NEVER directly enforceable — must be approved first.

    ``source_clause_id`` links back to the clause it was extracted from; the
    full :class:`Source` block is materialized at publish time from that clause.
    Conditions are AND-combined.
    """

    rule_key: str
    rule_type: RuleType
    conditions: list[Condition] = Field(min_length=1)
    effect: Effect
    applies_to_intents: list[str] = Field(default_factory=list)
    requires_trace: bool = True
    confidence: float = Field(ge=0.0, le=1.0, default=0.0)
    ambiguities: list[str] = Field(default_factory=list)
    source_evidence: str = ""
    # Populated by the pipeline, not the LLM:
    source_clause_id: Optional[str] = None
    review_status: ReviewStatus = "pending"

    _coerce = model_validator(mode="before")(staticmethod(_coerce_conditions))

    @field_validator("rule_key")
    @classmethod
    def _snake_case(cls, v: str) -> str:
        if not v or v != v.lower() or not v.replace("_", "").isalnum():
            raise ValueError(f"rule_key must be lower_snake_case: {v!r}")
        return v

    @property
    def condition(self) -> Condition:
        """First condition — convenience for single-condition rules."""
        return self.conditions[0]


class Source(BaseModel):
    """Full provenance block, materialized on the published rule."""

    document_id: str
    file_name: Optional[str] = None
    page: Optional[int] = None
    section: str = ""
    paragraph_ref: Optional[str] = None
    evidence: str = ""


class ApprovedRule(BaseModel):
    """A candidate that a human has approved. This is runtime-shaped output."""

    rule_key: str
    rule_type: RuleType
    version: str
    status: Literal["active", "draft", "retired", "superseded"] = "active"
    conditions: list[Condition] = Field(min_length=1)
    effect: Effect
    source: Source

    _coerce = model_validator(mode="before")(staticmethod(_coerce_conditions))
    applies_to_intents: list[str] = Field(default_factory=list)
    trace_required: bool = True
    approved_by: Optional[str] = None
    approved_at: Optional[str] = None
    effective_from: Optional[str] = None
    effective_to: Optional[str] = None


class Conflict(BaseModel):
    """A problem detected across candidate rules, surfaced before review."""

    conflict_id: str
    severity: Literal["low", "medium", "high"]
    type: str
    rules: list[str]
    message: str
    recommended_action: str


# --- unresolved items ---------------------------------------------------------

Severity = Literal["low", "medium", "high", "critical"]

# Each type ties to a concrete cause (design.md §10). Types that cannot occur
# given the flat IR (e.g. unsupported expression trees) are intentionally absent.
UnresolvedType = Literal[
    "unmappable_symbol",          # fails the four-namespace binding pre-check
    "unknown_role",               # role not in the domain pack / not caller.role
    "unknown_action",             # action with no intent mapping
    "missing_metric",             # arithmetic needs a metric the deployment lacks
    "vague_condition",
    "missing_threshold",
    "missing_related_policy",
    "conflicting_policy_statement",
    "ambiguous_approver",
    "non_executable_language",
    "missing_exception_expiry",
    "missing_audit_detail",
    "llm_output_invalid",
    "unconverted_clause",         # a non-boilerplate clause produced no rule
]


class UnresolvedSource(BaseModel):
    document_id: Optional[str] = None
    clause_id: Optional[str] = None
    section: str = ""
    evidence: str = ""


class UnresolvedItem(BaseModel):
    """First-class record of something the system could not safely resolve."""

    unresolved_id: str
    type: UnresolvedType
    severity: Severity
    status: Literal["open", "resolved", "waived"] = "open"
    source: UnresolvedSource = Field(default_factory=UnresolvedSource)
    issue: str = ""
    impact: str = ""
    recommended_action: str = ""
    blocks_publication: bool = False
    blocks_related_rules: list[str] = Field(default_factory=list)
    # rule this item was raised against (if any), for UI linking.
    rule_key: Optional[str] = None


# --- validation report --------------------------------------------------------


class RuleValidation(BaseModel):
    """Per-rule validator verdict (design.md §13)."""

    rule_key: str
    schema_valid: bool = True
    source_grounded: bool = True
    semantic_valid: bool = True
    executable: bool = True
    testable: bool = True
    consistency_valid: bool = True
    publishable: bool = False
    publish_blockers: list[str] = Field(default_factory=list)


class ValidationReport(BaseModel):
    summary: dict[str, Any] = Field(default_factory=dict)
    rule_results: list[RuleValidation] = Field(default_factory=list)
    unresolved_items: list[UnresolvedItem] = Field(default_factory=list)
    conflicts: list[Conflict] = Field(default_factory=list)


# --- domain pack --------------------------------------------------------------

# Which of the four runtime binding namespaces a field is expected to resolve to.
BindsTo = Literal["column", "request_param", "metric", "caller"]


class PackField(BaseModel):
    type: Optional[str] = None
    binds_to: Optional[BindsTo] = None
    allowed_values: Optional[list[Any]] = None
    aliases: list[str] = Field(default_factory=list)


class PackRole(BaseModel):
    aliases: list[str] = Field(default_factory=list)


class PackAction(BaseModel):
    intent: Optional[str] = None
    aliases: list[str] = Field(default_factory=list)


class PackReasonCode(BaseModel):
    message: str = ""


# --- document profile ---------------------------------------------------------


class ProfileWarning(BaseModel):
    code: str
    message: str = ""


class DocumentProfile(BaseModel):
    schema_version: str = "prefront.document_profile.v1"
    detected_source_type: str = "business_policy"
    detected_domain: Optional[str] = None
    domain_confidence: float = 0.0
    structural_features: dict[str, bool] = Field(default_factory=dict)
    extraction_strategy: list[str] = Field(default_factory=list)
    warnings: list[ProfileWarning] = Field(default_factory=list)


# --- policy atoms (intermediate IR) -------------------------------------------

AtomType = Literal[
    "prohibition",
    "permission",
    "obligation",
    "approval_requirement",
    "authority_assignment",
    "threshold",
    "exception",
    "waiver",
    "segregation_of_duties",
    "audit_requirement",
    "retention_requirement",
    "data_access_permission",
    "data_access_restriction",
    "routing_requirement",
    "definition",
    "related_policy_reference",
]


class PolicyAtom(BaseModel):
    """Domain-neutral semantic unit between a clause and a candidate rule.

    Explanatory/auditable. Must be *lowerable* to the flat CandidateRule IR —
    it carries no construct that IR forbids (no expression trees, no reason_code).
    """

    atom_id: str
    clause_id: Optional[str] = None
    atom_type: AtomType
    actor: Optional[str] = None
    action: list[str] = Field(default_factory=list)
    object: Optional[str] = None
    condition: Optional[dict[str, Any]] = None
    effect: Optional[dict[str, Any]] = None
    source_evidence: str = ""
    confidence: float = 0.0


# --- clause ledger ------------------------------------------------------------


class ClauseLedgerEntry(BaseModel):
    clause_id: str
    section: str = ""
    disposition: Optional[Disposition] = None
    reason: str = ""
    generated_atoms: list[str] = Field(default_factory=list)
    generated_rules: list[str] = Field(default_factory=list)
    unresolved_items: list[str] = Field(default_factory=list)


class DomainPack(BaseModel):
    """A design-time vocabulary + alias map (NOT the runtime binding authority).

    Mirrors the four binding namespaces so unmapped symbols surface as unresolved
    items before publish. Loaded from config YAML; never hardcoded in engine code.
    """

    schema_version: str = "prefront.domain_pack.v1"
    domain: str
    version: str = "1.0"
    status: str = "active"
    fields: dict[str, PackField] = Field(default_factory=dict)
    roles: dict[str, PackRole] = Field(default_factory=dict)
    actions: dict[str, PackAction] = Field(default_factory=dict)
    reason_codes: dict[str, PackReasonCode] = Field(default_factory=dict)
