"""Artifact renderers — the Core 6 semantic-layer YAMLs (design §4–§12).

    physical_catalog.yaml   semantic_model.yaml   relationships.yaml
    sensitivity.yaml        intent_bindings.yaml  mcp_tools.yaml

Each is a reviewable, versioned YAML in the shape the runtime loads. A header
marks the model/tool artifacts as machine-suggested + auto-reviewed for the MVP
(the human-review/publish step of design §2 is represented by ``approved_by``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .pipeline import PipelineResult
from .schema import (
    IntentBinding,
    McpTool,
    PhysicalCatalog,
    QueryTemplate,
    Relationship,
    SemanticModel,
    SensitivityRule,
)


class _LiteralStr(str):
    """A string that should render as a YAML literal block scalar (``|``)."""


class _BlockDumper(yaml.SafeDumper):
    """Keeps lists block-style and strings readable; never emits &anchor/*alias
    (shared dicts like the output schema are repeated inline for portability)."""

    def ignore_aliases(self, data: Any) -> bool:  # noqa: D401
        return True


_BlockDumper.add_representer(
    _LiteralStr,
    lambda dumper, data: dumper.represent_scalar("tag:yaml.org,2002:str", str(data), style="|"),
)


def _dump(data: Any) -> str:
    return yaml.dump(
        data, Dumper=_BlockDumper, sort_keys=False,
        default_flow_style=False, allow_unicode=True, width=100,
    )


def _drop_none(d: dict) -> dict:
    return {k: v for k, v in d.items() if v not in (None, [], {})}


# --- renderers ----------------------------------------------------------------


def render_physical_catalog(cat: PhysicalCatalog) -> str:
    tables: dict[str, Any] = {}
    for t in cat.tables:
        cols = {}
        for c in t.columns:
            cols[c.name] = _drop_none({
                "type": c.type,
                "nullable": c.nullable,
                "enum_values": c.enum_values,
                "markers": c.markers,
            })
        tables[t.name] = _drop_none({
            "primary_key": (t.primary_key[0] if len(t.primary_key) == 1 else t.primary_key),
            "columns": cols,
            "foreign_keys": [
                {"from": fk.from_columns, "to_table": fk.to_table, "to_columns": fk.to_columns}
                for fk in t.foreign_keys
            ],
        })
    doc = {
        "datasource_id": cat.datasource_id,
        "type": cat.type,
        "schema_version": cat.schema_version,
        "status": cat.status,
        "tables": tables,
    }
    return _dump(doc)


def render_semantic_model(model: SemanticModel) -> str:
    entities: dict[str, Any] = {}
    for e in model.entities:
        attrs = {}
        for a in e.attributes:
            attrs[a.attribute_key] = _drop_none({
                "column": a.column,
                "type": a.type,
                "required": a.required or None,
                "sensitivity_level": None if a.sensitivity_level == "normal" else a.sensitivity_level,
            })
        entities[e.entity_key] = _drop_none({
            "description": e.description,
            "primary_table": e.primary_table,
            "primary_key": e.primary_key,
            "synonyms": e.synonyms,
            "restricted": e.restricted or None,
            "attributes": attrs,
        })
    doc = _drop_none({
        "semantic_model_id": model.semantic_model_id,
        "version": model.version,
        "status": model.status,
        "domain": model.domain,
        "approved_by": model.approved_by,
        "generated_by": model.generated_by,
        "entities": entities,
    })
    header = (
        f"# Semantic model {model.semantic_model_id} v{model.version}\n"
        f"# MACHINE-SUGGESTED by {model.generated_by}, auto-reviewed for MVP — "
        "entities/attributes are candidate mappings against the real schema.\n"
        "# Runtime loads only published versions (design §2, §23.3).\n\n"
    )
    return header + _dump(doc)


def render_relationships(rels: list[Relationship]) -> str:
    out: dict[str, Any] = {}
    for r in rels:
        out[r.relationship_key] = _drop_none({
            "from_entity": r.from_entity,
            "to_entity": r.to_entity,
            "join": {"from": r.join.from_, "to": r.join.to},
            "cardinality": r.cardinality,
            "approved": r.approved,
            "restricted": r.restricted,
            "allowed_roles": r.allowed_roles,
        })
    header = (
        "# Approved join paths only. The runtime validator rejects any query using\n"
        "# a join not listed and approved here (design §7).\n\n"
    )
    return header + _dump({"relationships": out})


def render_sensitivity(rules: list[SensitivityRule]) -> str:
    out: dict[str, Any] = {}
    for s in rules:
        out[s.key] = _drop_none({
            "physical_column": s.physical_column,
            "classification": s.classification,
            "sensitivity_level": s.sensitivity_level,
            "default_access": s.default_access,
            "allowed_roles": s.allowed_roles,
            "masking": ({"enabled": s.masking.enabled, "type": s.masking.type}
                        if s.masking and s.masking.enabled else None),
        })
    header = "# Field-level access. Hard rule: sensitive fields default to deny (design §9, §23.7).\n\n"
    return header + _dump({"sensitivity": out})


def render_intent_bindings(bindings: list[IntentBinding]) -> str:
    out: dict[str, Any] = {}
    for b in bindings:
        out[b.intent_id] = _drop_none({
            "description": b.description,
            "required_entities": b.required_entities,
            "optional_entities": b.optional_entities,
            "allowed_attributes": b.allowed_attributes,
            "restricted_attributes": b.restricted_attributes,
            "mandatory_filters": [
                {"semantic_filter_id": f.semantic_filter_id, "expression": f.expression}
                for f in b.mandatory_filters
            ],
            "template_ids": b.template_ids,
            "policies": b.policies,
            "trace_required": b.trace_required,
        })
    header = "# Intent -> semantic entities, allowed/restricted fields, filters, policies (design §10).\n\n"
    return header + _dump({"intent_bindings": out})


def render_mcp_tools(tools: list[McpTool]) -> str:
    out: dict[str, Any] = {}
    for t in tools:
        out[t.tool_name] = _drop_none({
            "tool_version": t.tool_version,
            "source_intent": t.source_intent,
            "semantic_model_id": t.semantic_model_id,
            "semantic_model_version": t.semantic_model_version,
            "description": t.description,
            "allowed_roles": t.allowed_roles,
            "input_schema": t.input_schema,
            "output_schema": t.output_schema,
            "result_shape": t.result_shape,
            "template_ids": t.template_ids,
            "policies_enforced": t.policies_enforced,
            "approval_behavior": (
                _drop_none(t.approval_behavior.model_dump()) if t.approval_behavior else None
            ),
            "trace": {"required": t.trace.required, "trace_fields": t.trace.trace_fields},
            "status": t.status,
        })
    header = (
        "# MCP tool contracts generated from approved intents (design §11, §12).\n"
        "# Every tool maps to an approved intent; no raw-SQL tool is ever emitted.\n\n"
    )
    return header + _dump({"mcp_tools": out})


def render_query_templates(templates: list[QueryTemplate]) -> str:
    out: dict[str, Any] = {}
    for t in templates:
        out[t.template_id] = _drop_none({
            "intent_id": t.intent_id,
            "description": t.description or None,  # the tool's meaning (shown to the agent)
            "semantic_model_id": t.semantic_model_id,
            "semantic_model_version": t.semantic_model_version,
            "kind": t.kind,
            "semantic_entities": t.semantic_entities,
            "read_only": t.read_only,
            "dialect": t.dialect,
            "sql": _LiteralStr(t.sql),
            "parameters": [
                {"name": p.name, "type": p.type, "required": p.required} for p in t.parameters
            ],
            "required_caller_context": t.required_caller_context,
            "result_columns": [
                {"name": rc.name, "sensitivity": rc.sensitivity} for rc in t.result_columns
            ],
            "decision_inputs": [
                {"name": rc.name, "sensitivity": rc.sensitivity} for rc in t.decision_inputs
            ],
            "write_action": (_drop_none({
                "table": t.write_action.table,
                "params": t.write_action.params,
                "column_map": t.write_action.column_map,
                "caller_columns": t.write_action.caller_columns,
                "defaults": t.write_action.defaults,
                "autofill": t.write_action.autofill,
            }) if t.write_action else None),
            "required_policies": t.required_policies,
            "runtime_policy_predicates": t.runtime_policy_predicates,
            "status": t.status,
        })
    header = (
        "# Approved, parameterized, read-only SQL templates (design §14), composed\n"
        "# deterministically from the semantic model: approved joins, allowed columns,\n"
        "# caller scope (:caller_*, injected by Prefront), and inlinable policy filters.\n\n"
    )
    return header + _dump({"query_templates": out})


# --- writer -------------------------------------------------------------------

FILES = {
    "physical_catalog.yaml": lambda r: render_physical_catalog(r.catalog),
    "semantic_model.yaml": lambda r: render_semantic_model(r.model),
    "relationships.yaml": lambda r: render_relationships(r.relationships),
    "sensitivity.yaml": lambda r: render_sensitivity(r.sensitivity),
    "intent_bindings.yaml": lambda r: render_intent_bindings(r.bindings),
    "query_templates.yaml": lambda r: render_query_templates(r.templates),
    "mcp_tools.yaml": lambda r: render_mcp_tools(r.tools),
}


def write_artifacts(result: PipelineResult, out_dir: str | Path) -> dict[str, str]:
    """Write the Core 6 YAMLs to ``out_dir``."""
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    written: dict[str, str] = {}
    for name, render in FILES.items():
        path = d / name
        path.write_text(render(result), encoding="utf-8")
        written[name] = str(path)
    return written
