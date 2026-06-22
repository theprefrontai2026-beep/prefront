"""MCP tool contract generation (design §11, §12) — "interfaces to tools".

Every approved intent becomes exactly one MCP tool; no raw-SQL / raw-datasource
tool is ever emitted (design §11, §23.11). Each tool carries a typed input
schema, an output schema, the result shape (never including restricted
attributes), the roles allowed to call it, the policies it enforces, approval
behavior, and a trace requirement.

The customer's LLM maps natural language to one of these tools; Prefront controls
execution. The contracts here are what the runtime MCP server (``mcp_server.py``)
exposes.
"""

from __future__ import annotations

from .policy import PolicyHints
from .schema import (
    ApprovalBehavior,
    IntentBinding,
    McpTool,
    PhysicalCatalog,
    SemanticModel,
    TraceSpec,
)

_TRACE_FIELDS = [
    "intent_id",
    "tool_name",
    "semantic_model_version",
    "policy_evaluations",
    "parameters",
    "execution_status",
]


def build_tools(
    bindings: list[IntentBinding],
    model: SemanticModel,
    hints: PolicyHints,
    catalog: PhysicalCatalog,
    *,
    metrics: dict[str, str] | None = None,
) -> list[McpTool]:
    restricted_attrs = {
        a for b in bindings for a in b.restricted_attributes
    }
    metric_names = {m.lower() for m in (metrics or {})}
    tools: list[McpTool] = []
    for b in bindings:
        approval = hints.approval_for_intent(b.intent_id)
        result_fields = [a for a in b.allowed_attributes if a not in restricted_attrs]
        tools.append(
            McpTool(
                tool_name=b.intent_id,
                source_intent=b.intent_id,
                semantic_model_id=model.semantic_model_id,
                semantic_model_version=model.version,
                description=b.description,
                allowed_roles=hints.allowed_roles_for_intent(b.intent_id),
                input_schema=_input_schema(b, model, hints, catalog, metric_names),
                output_schema=_OUTPUT_SCHEMA,
                result_shape={"fields": result_fields},
                template_ids=b.template_ids,
                policies_enforced=b.policies,
                approval_behavior=(ApprovalBehavior(**approval) if approval else None),
                trace=TraceSpec(required=True, trace_fields=_TRACE_FIELDS),
                status="published",
            )
        )
    return tools


_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["allowed", "blocked", "approval_required"]},
        "trace_id": {"type": "string"},
        "rows": {"type": "array"},
    },
}


def _input_schema(b: IntentBinding, model: SemanticModel, hints: PolicyHints,
                  catalog: PhysicalCatalog, metric_names: set[str]) -> dict:
    """Infer typed request inputs for the tool — generically.

    Request inputs = the ROOT ENTITY's primary key (for single-record intents)
    plus any policy field the intent conditions on that is NOT a stored column
    and NOT a metric (the runtime cannot read it — it must be supplied).
    Caller context is NEVER an input; Prefront injects it from identity.
    """
    props: dict[str, dict] = {}
    required: list[str] = []

    intent = b.intent_id.lower()
    # A search/list intent is scoped by the caller's session context, which
    # Prefront injects — it must NOT require a record key. A single-record intent
    # takes the root entity's primary key.
    is_lookup = any(w in intent for w in ("find", "list", "search", "by_"))
    pk = _root_pk(b, model, catalog)
    if not is_lookup and pk:
        props[pk] = {"type": _param_type(pk, catalog),
                     "description": f"Target {pk.replace('_', ' ')}."}
        required.append(pk)

    for rule in hints.rules_for_intent(b.intent_id):
        for cond in rule.data_conditions():
            name = str(cond.get("field", "")).split(".")[-1]
            low = name.lower()
            if not name or low in metric_names or low in props or low == pk:
                continue
            # Stored columns are read by the runtime, not supplied by the caller.
            if any(t.column(name) for t in catalog.tables):
                continue
            props[name] = {"type": _ptype(low), "description": f"Request value for {name}."}

    return {
        "type": "object",
        "properties": props,
        "required": required,
        "additionalProperties": False,
    }


def _root_pk(b: IntentBinding, model: SemanticModel, catalog: PhysicalCatalog) -> str | None:
    """Bare primary-key column of the intent's root entity (generic record key)."""
    if not b.required_entities:
        return None
    e = model.entity(b.required_entities[0])
    if not e:
        return None
    t = catalog.table(e.primary_table)
    return t.primary_key[0] if t and t.primary_key else None


def _param_type(name: str, catalog: PhysicalCatalog) -> str:
    """JSON type for a param that binds to a real column (e.g. customer_id -> integer)."""
    from .catalog import json_type

    for t in catalog.tables:
        col = t.column(name)
        if col:
            return json_type(col.type)
    return "string"


def _ptype(name: str) -> str:
    if any(s in name for s in ("value", "amount", "limit", "balance", "score", "pct", "percent")):
        return "number"
    return "string"
