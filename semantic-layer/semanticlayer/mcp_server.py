"""Runtime MCP server — exposes the generated tool contracts (design §13).

Loads ``mcp_tools.yaml`` (+ ``intent_bindings.yaml`` for policy context) from a
published artifact directory and serves each contract as an MCP tool over stdio.

On a tool call it:
  1. validates the arguments against the tool's typed ``input_schema``
     (rejects unknown args and missing required ones — design §13 step 6),
  2. maps tool -> intent -> binding, and
  3. returns a **decision-trace stub** (design §15): the matched intent, semantic
     model version, policies that would be enforced, approval behavior, and the
     parameters — without executing any query.

Query execution is intentionally out of scope: the design routes the call into a
Prefront runtime (resolve/execute/trace) that is not part of this builder. This
server is the governed *interface* plus the trace contract — never raw SQL.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import yaml


def _section(path: Path, key: str) -> dict:
    if not path.exists():
        return {}
    return (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get(key, {})


def _load(in_dir: str | Path) -> tuple[dict, dict, dict]:
    d = Path(in_dir)
    tools = _section(d / "mcp_tools.yaml", "mcp_tools")
    bindings = _section(d / "intent_bindings.yaml", "intent_bindings")
    # Index query templates by the intent they serve.
    templates = {
        spec.get("intent_id"): {**spec, "template_id": tid}
        for tid, spec in _section(d / "query_templates.yaml", "query_templates").items()
    }
    return tools, bindings, templates


def validate_arguments(schema: dict, arguments: dict) -> list[str]:
    """Minimal JSON-Schema-ish check: required present, no unknown props, coarse types."""
    errors: list[str] = []
    props = schema.get("properties", {})
    for req in schema.get("required", []):
        if req not in arguments:
            errors.append(f"missing required argument {req!r}")
    if schema.get("additionalProperties") is False:
        for k in arguments:
            if k not in props:
                errors.append(f"unknown argument {k!r}")
    _types = {"string": str, "number": (int, float), "integer": int, "boolean": bool}
    for k, v in arguments.items():
        spec = props.get(k)
        if spec and (py := _types.get(spec.get("type"))) and not isinstance(v, py):
            errors.append(f"argument {k!r} must be {spec.get('type')}")
    return errors


def bind_query(template: dict, arguments: dict) -> dict:
    """Show how the approved template's :placeholders would bind for this call."""
    if not template:
        return {}
    params = {p["name"] for p in template.get("parameters", [])}
    caller = set(template.get("required_caller_context", []))
    binding = {p: arguments.get(p, "<missing>") for p in params}
    binding.update({f"caller_{c}": "<injected-by-prefront>" for c in caller})
    out = {
        "template_id": template.get("template_id"),
        "kind": template.get("kind", "read"),
        "sql": template.get("sql", ""),
        "parameters_bound": binding,
        "runtime_policy_predicates": template.get("runtime_policy_predicates", []),
    }
    if template.get("kind") == "precheck":
        out["decision_inputs"] = [c.get("name") for c in template.get("decision_inputs", [])]
        out["write_action"] = template.get("write_action")
    return out


def decide(tool: dict, intent: str, arguments: dict, template: dict | None = None) -> dict:
    """Build the governance decision-trace stub for one call."""
    approval = tool.get("approval_behavior") or {}
    status = "approval_required" if approval.get("may_require_approval") else "allowed"
    trace_id = "trace_" + hashlib.sha256(
        (intent + json.dumps(arguments, sort_keys=True)).encode()
    ).hexdigest()[:12]
    trace = {
        "trace_id": trace_id,
        "tool_name": tool.get("tool_name", intent),
        "matched_intent": intent,
        "semantic_model_version": (
            f"{tool.get('semantic_model_id')}:{tool.get('semantic_model_version')}"
        ),
        "parameters": arguments,
        "result_shape": tool.get("result_shape", {}),
        "query": bind_query(template or {}, arguments),
        "policy_evaluations": [
            {"policy_id": p, "result": "enforced_at_runtime"}
            for p in tool.get("policies_enforced", [])
        ],
        "status": status,
        "execution_status": "not_executed:design_time_stub",
        "note": "Prefront runtime execution is out of scope; this is the governed "
                "tool interface + approved query template + decision-trace stub.",
    }
    if approval.get("may_require_approval"):
        trace["approval"] = {
            "approval_role": approval.get("approval_role"),
            "approval_condition": approval.get("approval_condition"),
        }
    return trace


def build_server(in_dir: str | Path):
    """Construct an MCP Server exposing the published tools (requires the `mcp` SDK)."""
    try:
        from mcp.server import Server
        import mcp.types as types
    except ImportError as e:  # pragma: no cover
        raise RuntimeError("the `mcp` package is required to serve; `uv pip install mcp`") from e

    tools, _bindings, templates = _load(in_dir)
    # tool_name -> contract (inject the name so decide() can read it).
    contracts = {name: {**spec, "tool_name": name} for name, spec in tools.items()}

    server = Server("prefront-semantic-layer")

    @server.list_tools()
    async def list_tools() -> list[Any]:
        return [
            types.Tool(
                name=name,
                description=spec.get("description", name).strip(),
                inputSchema=spec.get("input_schema", {"type": "object", "properties": {}}),
            )
            for name, spec in contracts.items()
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict | None) -> list[Any]:
        arguments = arguments or {}
        tool = contracts.get(name)
        if tool is None:
            return [types.TextContent(type="text",
                                      text=json.dumps({"error": f"unknown tool {name!r}"}))]
        errs = validate_arguments(tool.get("input_schema", {}), arguments)
        if errs:
            return [types.TextContent(type="text",
                                      text=json.dumps({"status": "blocked", "errors": errs}))]
        intent = tool.get("source_intent", name)
        trace = decide(tool, intent, arguments, templates.get(intent))
        return [types.TextContent(type="text", text=json.dumps(trace, indent=2))]

    return server, types


def serve(in_dir: str | Path) -> None:
    """Run the MCP server over stdio."""
    import asyncio

    from mcp.server.stdio import stdio_server

    server, _types = build_server(in_dir)

    async def _run() -> None:
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())

    asyncio.run(_run())
