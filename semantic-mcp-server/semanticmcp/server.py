"""Load query templates and expose each as a GOVERNED MCP tool.

One approved template -> one tool. When governance is active (a published
policy bundle and/or a configured caller identity), every call runs the
governance pipeline first — authz, facts, business-rule evaluation, decision,
masking, trace — and only an ALLOWED call executes the SQL. ``:caller_*``
placeholders are injected from the trusted identity and removed from the
agent-facing input schema (the agent cannot pass or spoof caller context).

Without a policy bundle or identity configured, falls back to the ungoverned
POC behavior (templates as plain query wrappers).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

import yaml

from . import db
from .governance import PolicyRegistry, govern, resolve_caller
from .governance import identity as identity_mod
from .governance import trace as trace_mod
from .governance import writes as writes_mod


class _Registry:
    """Loads tools from the templates file and reloads when the file changes.

    The Publish step (semantic-layer-api) rewrites query_templates.yaml with the
    approved set; this picks up the change on the next list/call — no restart.
    """

    def __init__(self, templates_path: str | Path, *, governed: bool = False) -> None:
        self.path = str(templates_path)
        self.governed = governed
        self._mtime: float | None = None
        self.tools: dict[str, dict] = {}
        self.refresh()

    def refresh(self) -> None:
        try:
            mtime = os.path.getmtime(self.path)
        except OSError:
            self.tools = {}
            self._mtime = None
            return
        if mtime != self._mtime:
            self.tools = load_templates(self.path, governed=self.governed)
            self._mtime = mtime


def load_templates(path: str | Path, *, governed: bool = False) -> dict[str, dict]:
    """Read query_templates.yaml -> {tool_name: tool_spec}.

    With ``governed=True``, ``:caller_*`` placeholders are NOT exposed as tool
    inputs — they are recorded under ``injected`` and bound from the trusted
    caller identity at call time.
    """
    doc = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    templates = doc.get("query_templates", doc)  # tolerate a bare mapping too
    tools: dict[str, dict] = {}
    for template_id, t in templates.items():
        sql = t.get("sql", "")
        name = t.get("intent_id") or template_id
        param_types = {p["name"]: p.get("type", "string") for p in t.get("parameters", [])}
        props, required, injected = {}, [], []
        for ph in db.placeholders(sql):
            if governed and ph.startswith("caller_"):
                injected.append(ph)
                continue
            props[ph] = {"type": _json_type(param_types.get(ph, "string")),
                         "description": _describe_param(ph)}
            required.append(ph)
        # A write intent's request params (write_action) are governance facts —
        # the agent must supply them even though the precheck SQL doesn't bind them.
        for wp in (t.get("write_action") or {}).get("params", []) or []:
            if wp in props or wp.startswith("caller_"):
                continue
            props[wp] = {"type": _json_type(param_types.get(wp, _guess_type(wp))),
                         "description": f"Requested value for {wp} (evaluated by policy)."}
            required.append(wp)
        tools[name] = {
            "name": name,
            "template_id": template_id,
            "intent": t.get("intent_id") or name,
            "kind": t.get("kind", "read"),
            "write_action": t.get("write_action"),
            "description": _describe(t, name),
            "sql": sql,
            "injected": injected,
            "input_schema": {
                "type": "object",
                "properties": props,
                "required": required,
                "additionalProperties": False,
            },
        }
    return tools


def call_template(tools: dict[str, dict], dsn: str, name: str, args: dict[str, Any]) -> dict:
    """Ungoverned execution (legacy POC path + the `call` CLI debug command)."""
    tool = tools.get(name)
    if tool is None:
        return {"error": f"unknown tool {name!r}"}
    try:
        rows = db.run_select(dsn, tool["sql"], args or {})
        return {"tool": name, "row_count": len(rows), "rows": rows, "sql": tool["sql"]}
    except Exception as e:  # surface DB / bind errors to the caller, don't crash
        return {"error": f"{type(e).__name__}: {e}", "tool": name, "sql": tool["sql"]}


def call_governed(
    tool: dict,
    dsn: str,
    args: dict[str, Any],
    policy: PolicyRegistry,
) -> dict:
    """Run one tool call through the governance pipeline, executing only on allow."""
    intent, kind = tool["intent"], tool.get("kind", "read")
    caller = resolve_caller(dsn)
    bundle = policy.bundle

    def respond(decision, execution_status, **extra) -> dict:
        t = trace_mod.build_trace(
            intent=intent, tool=tool["name"], caller=caller, args=args,
            decision=decision, execution_status=execution_status,
            template_id=tool.get("template_id"),
        )
        trace_mod.persist(t)
        return {"tool": tool["name"], "status": decision.status,
                "reasons": decision.reasons or None,
                "approver_roles": decision.approver_roles or None,
                **extra, "governance": t}

    from .governance.context import Decision

    if caller is None:
        return respond(
            Decision(status="blocked",
                     reasons=["no_caller_identity: configure ACT_AS (or CALLER_ROLE/"
                              "CALLER_REGION) — caller context cannot come from the agent"]),
            "not_executed",
        )

    # Bind: agent args + injected caller context. :caller_<attr> is filled from
    # the caller's attribute of the same name — generic, no assumed identity shape.
    binds = dict(args or {})
    for ph in tool.get("injected", []):
        binds[ph] = caller.attrs.get(ph[len("caller_"):])

    # Facts row: a write intent's own template IS its precheck SELECT.
    row = None
    if kind == "precheck":
        try:
            rows = db.run_select(dsn, tool["sql"], binds)
        except Exception as e:
            return respond(Decision(status="blocked",
                                    reasons=[f"precheck_failed: {type(e).__name__}: {e}"]),
                           "error")
        if not rows:
            return respond(
                Decision(status="blocked",
                         reasons=["target_not_found_or_out_of_region: the precheck "
                                  "returned no row for this caller's scope"]),
                "not_executed",
            )
        row = rows[0]

    # Fields the write would touch (param names + their mapped columns + caller-
    # filled columns, all from the DECLARATIVE spec) — restricted-field rules
    # only block a write that actually touches them.
    write_fields: set[str] = set()
    if kind == "precheck":
        wa = tool.get("write_action") or {}
        cmap = wa.get("column_map") or {}
        for p in (wa.get("params") or []):
            write_fields.add(p)
            write_fields.add(cmap.get(p, p))
        write_fields.update((wa.get("caller_columns") or {}).keys())

    ctx = govern(intent=intent, kind=kind, args=args or {}, caller=caller,
                 row=row, bundle=bundle, write_fields=write_fields)
    decision = ctx.decision

    if decision.status != "allowed":
        return respond(decision, "not_executed")

    if kind == "precheck":
        wa = tool.get("write_action") or {}
        if not wa:
            # Guarded read: a precheck with no write_action returns its row(s) on
            # allow (the precheck SELECT IS the read), masking restricted fields for
            # this caller. Because the precheck row's columns were facts, a rule can
            # gate on them — e.g. block when the row's owner != the caller — which a
            # plain read, having no row at decision time, cannot do.
            masked = [m.split(".")[-1] for m in decision.mask_fields]
            out_rows = rows
            if masked:
                out_rows = [{k: ("***" if k in masked else v) for k, v in r.items()}
                            for r in rows]
            return respond(decision, "executed",
                           row_count=len(out_rows), rows=out_rows,
                           masked_fields=masked or None)
        write_params = {k: args.get(k) for k in (wa.get("params") or []) if k in (args or {})}
        result = writes_mod.perform(dsn, wa, write_params, caller)
        status = {"executed": "write_executed", "dry_run": "write_dry_run"}.get(
            result.get("mode"), "write_error")
        return respond(decision, status, write=result)

    # Read: execute, then mask restricted fields for this caller.
    try:
        rows = db.run_select(dsn, tool["sql"], binds)
    except Exception as e:
        return respond(Decision(status="blocked",
                                reasons=[f"query_failed: {type(e).__name__}: {e}"]),
                       "error")
    masked = [m.split(".")[-1] for m in decision.mask_fields]
    if masked:
        rows = [{k: ("***" if k in masked else v) for k, v in r.items()} for r in rows]
    return respond(decision, "executed",
                   row_count=len(rows), rows=rows,
                   masked_fields=masked or None)


def build_server(dsn: str, templates_path: str | Path):
    """Construct an MCP Server exposing the templates (requires the `mcp` SDK)."""
    try:
        from mcp.server import Server
        import mcp.types as types
    except ImportError as e:  # pragma: no cover
        raise RuntimeError("the `mcp` package is required to serve; `uv pip install mcp`") from e

    policy = PolicyRegistry(_policy_path(templates_path))
    registry = _Registry(templates_path,
                         governed=policy.active or identity_mod.configured())
    server = Server("prefront-semantic-mcp")

    def _sync() -> None:
        """Reload templates/policy; re-shape input schemas if governance flipped."""
        policy.refresh()
        governed = policy.active or identity_mod.configured()
        if governed != registry.governed:
            registry.governed = governed
            registry._mtime = None   # force template re-load with the new shape
        registry.refresh()

    @server.list_tools()
    async def list_tools() -> list[Any]:
        _sync()  # pick up a freshly published set / governance flip
        return [
            types.Tool(name=t["name"], description=t["description"], inputSchema=t["input_schema"])
            for t in registry.tools.values()
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict | None) -> list[Any]:
        _sync()
        tool = registry.tools.get(name)
        if tool is None:
            result: dict = {"error": f"unknown tool {name!r}"}
        elif policy.active or identity_mod.configured():
            result = call_governed(tool, dsn, arguments or {}, policy)
        else:
            result = call_template(registry.tools, dsn, name, arguments or {})
        return [types.TextContent(type="text", text=json.dumps(result, indent=2, default=str))]

    return server, registry, policy


def _policy_path(templates_path: str | Path) -> Optional[str]:
    """POLICY_PATH env, else policy.yaml next to the templates file."""
    env = os.environ.get("POLICY_PATH")
    if env:
        return env
    sibling = Path(templates_path).parent / "policy.yaml"
    return str(sibling)


def serve(dsn: str, templates_path: str | Path) -> None:
    """Serve over stdio (a client launches this process)."""
    import asyncio

    from mcp.server.stdio import stdio_server

    server, _registry, _policy = build_server(dsn, templates_path)

    async def _run() -> None:
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())

    asyncio.run(_run())


def serve_http(dsn: str, templates_path: str | Path, *, host: str = "0.0.0.0", port: int = 8090) -> None:
    """Serve over HTTP using the MCP SSE transport (for containers / networked clients).

    Endpoints: GET /sse (event stream), POST /messages/ (client→server), GET /healthz.
    """
    import uvicorn
    from mcp.server.sse import SseServerTransport
    from starlette.applications import Starlette
    from starlette.requests import Request
    from starlette.responses import JSONResponse
    from starlette.routing import Mount, Route

    server, registry, policy = build_server(dsn, templates_path)
    sse = SseServerTransport("/messages/")

    async def handle_sse(request: Request):
        # The caller identity for THIS connection is established by the trusted
        # session layer (an ?act_as= query param or an X-Prefront-Act-As header),
        # never by the agent/LLM. It is resolved server-side via IDENTITY_QUERY.
        # In production this stands in for an authenticated session token.
        act_as = request.query_params.get("act_as") or request.headers.get("x-prefront-act-as")
        token = identity_mod.act_as_var.set(act_as) if act_as else None
        try:
            async with sse.connect_sse(request.scope, request.receive, request._send) as (read, write):
                await server.run(read, write, server.create_initialization_options())
        finally:
            if token is not None:
                identity_mod.act_as_var.reset(token)

    async def healthz(_request: Request):
        registry.refresh()
        policy.refresh()
        return JSONResponse({
            "status": "ok",
            "tools": list(registry.tools),
            "governed": policy.active or identity_mod.configured(),
            "policy_rules": len((policy.bundle or {}).get("rules", [])),
        })

    app = Starlette(routes=[
        Route("/sse", endpoint=handle_sse),
        Mount("/messages/", app=sse.handle_post_message),
        Route("/healthz", endpoint=healthz),
    ])
    uvicorn.run(app, host=host, port=port)


# --- small presentation helpers ----------------------------------------------


def _json_type(t: str) -> str:
    return {"number": "number", "integer": "integer", "boolean": "boolean"}.get(t, "string")


def _guess_type(name: str) -> str:
    n = name.lower()
    if any(s in n for s in ("value", "amount", "limit", "balance", "pct", "percent", "score")):
        return "number"
    return "string"


def _describe(t: dict, name: str) -> str:
    # Prefer a human description authored on the template — it's what the agent
    # sees when choosing a tool. Fall back to a generic line.
    desc = (t.get("description") or "").strip()
    if desc:
        return desc
    kind = t.get("kind", "read")
    base = name.replace("_", " ").strip()
    return f"Run the approved '{name}' query ({kind}). {base.capitalize()}.".strip()


def _describe_param(ph: str) -> str:
    if ph.startswith("caller_"):
        return f"Caller context: {ph[len('caller_'):]} (e.g. region code)."
    return f"Value for :{ph}."
