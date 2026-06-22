"""Publish-time semantic validator (design §19, §23).

The gate between a candidate semantic layer and a publishable one. It re-checks
the hard rules against the *final* artifacts (not just the LLM output), so a bad
hand-edit fails just like a bad model. ``build`` refuses to stamp the model
``published`` while any error stands.

Implemented checks (the subset relevant to the Core 6 artifacts):
  1.  entity maps to a real table
  2.  attribute maps to a real column
  3.  relationship join references real columns
  4.  relationship is approved
  5.  sensitive field defaults to deny (§23.7)
  6.  intent binding references known entities
  7.  intent binding references known attributes
  8.  MCP tool maps to an approved intent
  9.  MCP tool does not expose a restricted attribute
  10. MCP tool has a typed input schema
  11. semantic model has a version and an approval record
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .schema import (
    IntentBinding,
    McpTool,
    PhysicalCatalog,
    QueryTemplate,
    Relationship,
    SemanticModel,
    SensitivityRule,
)


@dataclass
class ValidationResult:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def validate(
    catalog: PhysicalCatalog,
    model: SemanticModel,
    relationships: list[Relationship],
    sensitivity: list[SensitivityRule],
    bindings: list[IntentBinding],
    templates: list[QueryTemplate],
    tools: list[McpTool],
) -> ValidationResult:
    res = ValidationResult()
    err = res.errors.append

    # 11. model versioning + approval
    if not model.version:
        err("semantic model has no version (§19.14)")
    if not model.approved_by:
        err("semantic model has no approval record (§19.15)")

    # 1-2. entities / attributes map to real tables / columns
    attr_keys: set[str] = set()
    for e in model.entities:
        if not catalog.table(e.primary_table):
            err(f"entity {e.entity_key!r} maps to unknown table {e.primary_table!r} (§19.1)")
        for a in e.attributes:
            attr_keys.add(f"{e.entity_key}.{a.attribute_key}")
            if not catalog.has_column(a.column):
                err(f"attribute {e.entity_key}.{a.attribute_key} maps to unknown "
                    f"column {a.column!r} (§19.2)")

    # 3-4. relationships: real join columns + approved
    for r in relationships:
        if not catalog.has_column(r.join.from_) or not catalog.has_column(r.join.to):
            err(f"relationship {r.relationship_key!r} joins unknown columns "
                f"{r.join.from_!r}->{r.join.to!r} (§19.3)")
        if not r.approved:
            err(f"relationship {r.relationship_key!r} is not approved (§19.4)")

    # 5. sensitive fields default to deny
    for s in sensitivity:
        if s.default_access != "deny":
            err(f"sensitive field {s.key!r} defaults to {s.default_access!r}, must be 'deny' (§19.6)")
        if not catalog.has_column(s.physical_column):
            err(f"sensitivity rule {s.key!r} maps to unknown column {s.physical_column!r}")

    # 6-7. intent bindings reference known entities + attributes, and never
    # allow an attribute whose physical column is restricted (sensitive -> deny).
    entity_keys = {e.entity_key for e in model.entities}
    restricted_cols = {s.physical_column.lower() for s in sensitivity}
    attr_to_col = {f"{e.entity_key}.{a.attribute_key}": a.column.lower()
                   for e in model.entities for a in e.attributes}
    for b in bindings:
        for ent in b.required_entities + b.optional_entities:
            if ent not in entity_keys:
                err(f"intent {b.intent_id!r} references unknown entity {ent!r} (§19.8)")
        for a in b.allowed_attributes:
            if a not in attr_keys:
                err(f"intent {b.intent_id!r} allows unknown attribute {a!r}")
            elif attr_to_col.get(a) in restricted_cols:
                err(f"intent {b.intent_id!r} allows restricted attribute {a!r} (§19.6/§23.7)")

    # 7b/12. query templates: each binding has one; templates expose no restricted
    # column and reference only known entities (joins are FK-backed by construction).
    tpl_by_intent = {t.intent_id: t for t in templates}
    entity_to_table = {e.entity_key: e.primary_table for e in model.entities}
    table_to_entity = {v.lower(): k for k, v in entity_to_table.items()}
    for b in bindings:
        if b.intent_id not in tpl_by_intent:
            err(f"intent {b.intent_id!r} has no approved query template (§19.7)")
    for t in templates:
        binding = next((b for b in bindings if b.intent_id == t.intent_id), None)
        restricted = set(binding.restricted_attributes) if binding else set()
        for rc in t.result_columns:
            full = next((a for a in (binding.allowed_attributes if binding else [])
                         if a.split(".")[-1] == rc.name), None)
            if full and full in restricted:
                err(f"template {t.template_id!r} selects restricted attribute {full!r} (§19.6/§14)")
        for ent in t.semantic_entities:
            if ent not in entity_keys and ent not in table_to_entity:
                err(f"template {t.template_id!r} references unknown entity {ent!r}")

    # 8-10. MCP tools
    intent_to_binding = {b.intent_id: b for b in bindings}
    for t in tools:
        binding = intent_to_binding.get(t.source_intent)
        if binding is None:
            err(f"MCP tool {t.tool_name!r} has no approved intent binding (§19.10)")
            continue
        restricted = set(binding.restricted_attributes)
        leaked = [f for f in t.result_shape.get("fields", []) if f in restricted]
        if leaked:
            err(f"MCP tool {t.tool_name!r} exposes restricted attributes {leaked} (§19.9)")
        if "properties" not in t.input_schema:
            err(f"MCP tool {t.tool_name!r} has no typed input schema (§23.13)")

    # §14: parse each template's SQL and assert it conforms to the contract.
    from .sqlcheck import check_templates

    sql_errors, sql_warnings = check_templates(
        catalog, model, relationships, sensitivity, templates
    )
    res.errors.extend(sql_errors)
    res.warnings.extend(sql_warnings)

    return res
