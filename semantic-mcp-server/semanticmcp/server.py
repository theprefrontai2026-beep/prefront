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

import anyio
import yaml

import dataclasses as _dc

from . import db
from . import mcp_proxy
from . import prefront_tracing as tracing
from .governance import PolicyRegistry, govern, resolve_caller
from .governance import identity as identity_mod
from .governance import inline_checks
from .governance import trace as trace_mod
from .governance import writes as writes_mod

_tracer = tracing.get_tracer("prefront.governance")


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
        kind = t.get("kind", "read")
        name = t.get("intent_id") or template_id
        param_types = {p["name"]: p.get("type", "string") for p in t.get("parameters", [])}
        props, required, injected = {}, [], []
        if kind == "mcp":
            # No SQL to scan placeholders out of — the declared parameters (built
            # 1:1 from the upstream tool's own input schema at design time) ARE
            # the input schema.
            for p in t.get("parameters", []):
                pname = p["name"]
                if governed and pname.startswith("caller_"):
                    injected.append(pname)
                    continue
                props[pname] = {"type": _json_type(param_types.get(pname, "string")),
                                "description": _describe_param(pname)}
                if p.get("required", True):
                    required.append(pname)
        else:
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
            "kind": kind,
            "write_action": t.get("write_action"),
            "description": _describe(t, name),
            "sql": sql,
            "mcp_server_url": t.get("mcp_server_url", ""),
            "mcp_tool_name": t.get("mcp_tool_name", ""),
            "mcp_destructive": bool(t.get("mcp_destructive", False)),
            "injected": injected,
            "input_schema": {
                "type": "object",
                "properties": props,
                "required": required,
                "additionalProperties": False,
            },
        }
    return tools


async def call_template(tools: dict[str, dict], dsn: str, name: str, args: dict[str, Any]) -> dict:
    """Ungoverned execution (legacy POC path + the `call` CLI debug command)."""
    tool = tools.get(name)
    if tool is None:
        return {"error": f"unknown tool {name!r}"}
    if tool.get("kind") == "mcp":
        try:
            result = await mcp_proxy.call_upstream_tool(
                tool["mcp_server_url"], tool["mcp_tool_name"], args or {})
            return {"tool": name, "result": result}
        except Exception as e:
            return {"error": f"{type(e).__name__}: {e}", "tool": name}
    try:
        rows = await anyio.to_thread.run_sync(db.run_select, dsn, tool["sql"], args or {})
        return {"tool": name, "row_count": len(rows), "rows": rows, "sql": tool["sql"]}
    except Exception as e:  # surface DB / bind errors to the caller, don't crash
        return {"error": f"{type(e).__name__}: {e}", "tool": name, "sql": tool["sql"]}


async def call_governed(
    tool: dict,
    dsn: str,
    args: dict[str, Any],
    policy: PolicyRegistry,
) -> dict:
    """Run one tool call through the governance pipeline, executing only on allow.

    Emits one tracing span per governed call (a no-op unless a collector is
    configured — see ``prefront_tracing``). The span carries the DECISION, not
    the data: outcome, reasons, which rules fired, masked field names, row
    count. With the agent side instrumented too, a trace shows the LLM picking
    a tool and this decision gating it, in one tree.
    """
    with _tracer.start_as_current_span(f"govern {tool.get('intent') or tool['name']}") as span:
        try:
            result = await _call_governed(tool, dsn, args, policy)
        except Exception as e:  # a crash here is a real error, unlike a block
            tracing.record_error(span, e)
            raise
        _annotate_decision(span, tool, args, result)
        return result


def _annotate_decision(span: Any, tool: dict, args: dict[str, Any], result: dict) -> None:
    """Project the governance trace onto span attributes (engine vocabulary only)."""
    t = result.get("governance") or {}
    rules = t.get("rules_evaluated") or []
    caller = t.get("caller") or {}
    execution_status = t.get("execution_status")
    tracing.set_attributes(span, {
        tracing.SPAN_KIND: "TOOL",
        tracing.INPUT_VALUE: tracing.as_json(args or {}),
        tracing.INPUT_MIME: tracing.JSON_MIME,
        tracing.OUTPUT_VALUE: tracing.as_json({
            "status": result.get("status"),
            "execution_status": execution_status,
            "row_count": result.get("row_count"),
            "reasons": result.get("reasons"),
            "masked_fields": result.get("masked_fields"),
        }),
        tracing.OUTPUT_MIME: tracing.JSON_MIME,
        "tool.name": tool.get("name"),
        "prefront.intent": t.get("matched_intent") or tool.get("intent"),
        "prefront.template_id": tool.get("template_id"),
        "prefront.template_kind": tool.get("kind", "read"),
        "prefront.trace_id": t.get("trace_id"),
        "prefront.decision": result.get("status"),
        "prefront.execution_status": execution_status,
        "prefront.reasons": result.get("reasons"),
        "prefront.approver_roles": result.get("approver_roles"),
        "prefront.masked_fields": result.get("masked_fields"),
        "prefront.row_count": result.get("row_count"),
        # Contract attributes only — the full caller bag can hold PII and stays
        # in the durable trace (TRACE_PATH), not in the exported span.
        "prefront.caller.role": caller.get("role"),
        "prefront.caller.region": caller.get("region"),
        "prefront.rules.evaluated": len(rules),
        "prefront.rules.fired": [r.get("rule_key") for r in rules if r.get("fired")],
        "prefront.rules.indeterminate": [
            r.get("rule_key") for r in rules if r.get("indeterminate")
        ],
    })
    # Inline reuse of eval-engine's single-call-safe checks (autonomous_build.md
    # step 18: family3.call pre-execution, family1.content post-execution -
    # see governance/inline_checks.py). Same list-of-dicts-under-`t`-then-
    # span-attribute pattern as the native rules above.
    inline = t.get("inline_checks") or []
    if inline:
        tracing.set_attributes(span, {
            "prefront.rule.satisfied": [v["check_id"] for v in inline if v["status"] == "satisfied"],
            "prefront.rule.violated": [v["check_id"] for v in inline if v["status"] == "violated"],
            "prefront.rule.clause": [
                v["source"]["section"] for v in inline if v.get("source") and v["source"].get("section")
            ],
        })
    # A block / approval_required is a CORRECT outcome, not a span error. Only a
    # failed precheck, query, or write marks the span failed.
    if execution_status in ("error", "write_error"):
        tracing.mark_error(span, "; ".join(result.get("reasons") or []) or execution_status)


async def _call_governed(
    tool: dict,
    dsn: str,
    args: dict[str, Any],
    policy: PolicyRegistry,
) -> dict:
    intent, kind = tool["intent"], tool.get("kind", "read")
    caller = resolve_caller(dsn)
    bundle = policy.bundle
    # Populated once inline_checks has run (see below); `respond` closes over
    # this same list object, so it sees whatever's in it by the time it's
    # actually called, however early that first call happens.
    inline_checks_trace: list[dict] = []

    def respond(decision, execution_status, **extra) -> dict:
        t = trace_mod.build_trace(
            intent=intent, tool=tool["name"], caller=caller, args=args,
            decision=decision, execution_status=execution_status,
            template_id=tool.get("template_id"),
        )
        if inline_checks_trace:
            t["inline_checks"] = inline_checks_trace
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

    # Facts row: a write intent's own template IS its precheck SELECT. An 'mcp'
    # tool has no local SELECT to gather one from — facts are args + caller only,
    # exactly like a plain 'read' tool already gets when it has no row either.
    row = None
    if kind == "precheck":
        try:
            rows = await anyio.to_thread.run_sync(db.run_select, dsn, tool["sql"], binds)
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

    # Fields the write would touch — restricted-field rules only block a write
    # that actually touches them. For SQL: param names + their mapped columns +
    # caller-filled columns, from the declarative write_action. For a destructive
    # MCP tool there's no column-level mapping to a local schema, so the tool's
    # own declared parameter names are the closest equivalent signal.
    write_fields: set[str] = set()
    if kind == "precheck":
        wa = tool.get("write_action") or {}
        cmap = wa.get("column_map") or {}
        for p in (wa.get("params") or []):
            write_fields.add(p)
            write_fields.add(cmap.get(p, p))
        write_fields.update((wa.get("caller_columns") or {}).keys())
    elif kind == "mcp" and tool.get("mcp_destructive"):
        write_fields.update(tool.get("input_schema", {}).get("properties", {}).keys())

    # decide.aggregate() only special-cases the literal "read" kind (masking vs.
    # blocking a restricted field); a destructive mcp call should get the same
    # "does it touch a restricted field" treatment as a SQL write, so it is
    # governed under any kind string other than "read".
    govern_kind = "read" if kind != "mcp" or not tool.get("mcp_destructive") else "mcp_write"
    ctx = govern(intent=intent, kind=govern_kind, args=args or {}, caller=caller,
                 row=row, bundle=bundle, write_fields=write_fields)
    decision = ctx.decision

    # Inline reuse of eval-engine's single-call-safe checks (autonomous_build.md
    # step 18): catalog_membership / entitlement / version_conformance /
    # side_effect_class, over an intent_catalog.yaml the native policy.yaml
    # rules know nothing about. No-op (empty verdicts, "allow") when
    # PREFRONT_INTENT_CATALOG_PATH isn't configured. A "flag" (schema drift)
    # is recorded for the span but never gates the call - only block /
    # approval_required fold into the native decision, and only ever
    # escalate it (never downgrade a block the native rules already made).
    # The TRUE side effect, for side_effect_class - distinct from govern_kind
    # above (that's the native engine's own "read vs mcp_write" masking
    # concept, not a general read/write signal: it's "read" for every SQL
    # precheck-write, since decide.aggregate() masks those like reads).
    if kind == "precheck":
        inline_side_effect = "write" if tool.get("write_action") else "read"
    elif kind == "mcp":
        inline_side_effect = "write" if tool.get("mcp_destructive") else "read"
    else:
        inline_side_effect = "read"
    inline_pre_effect, inline_pre_verdicts = inline_checks.evaluate_pre_execution(
        intent, tool["name"], args or {}, caller.role or "",
        caller.attrs.get("channel", "") or "", inline_side_effect,
    )
    inline_checks_trace.extend(_dc.asdict(v) for v in inline_pre_verdicts)
    if inline_pre_effect == "block" and decision.status != "blocked":
        decision.status = "blocked"
        decision.reasons = [*decision.reasons, "inline_check_blocked: an intent_catalog.yaml check failed"]
    elif inline_pre_effect == "approval_required" and decision.status == "allowed":
        decision.status = "approval_required"
        decision.reasons = [*decision.reasons, "inline_check_approval_required: an intent_catalog.yaml check requires approval"]

    if decision.status != "allowed":
        return respond(decision, "not_executed")

    def inline_post_masked(result_obj: Any) -> set[str]:
        """field_restriction (family1/content.py) over the executed result -
        the read already happened, so this can only ever ADD masked field
        names, never block (autonomous_build.md step 18). No-op set when
        PREFRONT_RULE_PACK_PATH isn't configured."""
        _effect, verdicts = inline_checks.evaluate_post_execution(
            intent, tool["name"], args or {}, result_obj,
            caller.role or "", caller.attrs.get("channel", "") or "",
        )
        inline_checks_trace.extend(_dc.asdict(v) for v in verdicts)
        return inline_checks.restricted_field_names(result_obj)

    if kind == "precheck":
        wa = tool.get("write_action") or {}
        if not wa:
            # Guarded read: a precheck with no write_action returns its row(s) on
            # allow (the precheck SELECT IS the read), masking restricted fields for
            # this caller. Because the precheck row's columns were facts, a rule can
            # gate on them — e.g. block when the row's owner != the caller — which a
            # plain read, having no row at decision time, cannot do.
            masked = sorted({m.split(".")[-1] for m in decision.mask_fields} | inline_post_masked(rows))
            out_rows = rows
            if masked:
                out_rows = [{k: ("***" if k in masked else v) for k, v in r.items()}
                            for r in rows]
            return respond(decision, "executed",
                           row_count=len(out_rows), rows=out_rows,
                           masked_fields=masked or None)
        write_params = {k: args.get(k) for k in (wa.get("params") or []) if k in (args or {})}
        result = await anyio.to_thread.run_sync(writes_mod.perform, dsn, wa, write_params, caller)
        status = {"executed": "write_executed", "dry_run": "write_dry_run"}.get(
            result.get("mode"), "write_error")
        return respond(decision, status, write=result)

    if kind == "mcp":
        if tool.get("mcp_destructive") and not writes_mod.enabled():
            return respond(decision, "write_dry_run",
                           write={"mode": "dry_run", "would_call": tool["mcp_tool_name"],
                                  "args": args, "note": "set ENABLE_WRITES=1 to execute"})
        try:
            result = await mcp_proxy.call_upstream_tool(
                tool["mcp_server_url"], tool["mcp_tool_name"], args or {},
            )
        except Exception as e:
            return respond(Decision(status="blocked",
                                    reasons=[f"upstream_call_failed: {type(e).__name__}: {e}"]),
                           "error")
        masked = sorted({m.split(".")[-1] for m in decision.mask_fields} | inline_post_masked(result))
        if masked:
            result = _mask_result(result, masked)
        status = "write_executed" if tool.get("mcp_destructive") else "executed"
        return respond(decision, status, result=result, masked_fields=masked or None)

    # Read: execute, then mask restricted fields for this caller.
    try:
        rows = await anyio.to_thread.run_sync(db.run_select, dsn, tool["sql"], binds)
    except Exception as e:
        return respond(Decision(status="blocked",
                                reasons=[f"query_failed: {type(e).__name__}: {e}"]),
                       "error")
    masked = sorted({m.split(".")[-1] for m in decision.mask_fields} | inline_post_masked(rows))
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
            result = await call_governed(tool, dsn, arguments or {}, policy)
        else:
            result = await call_template(registry.tools, dsn, name, arguments or {})
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
    from starlette.responses import JSONResponse, Response
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
        # Starlette >=1.0 does `await (await endpoint(request))(scope, receive, send)`,
        # so an endpoint returning None dies with
        #     TypeError: 'NoneType' object is not callable
        # AFTER the stream has been served — which is what the "MCP SSE transport
        # flakes on slower calls" symptom actually was: the exchange completes, then
        # the connection tears down dirty and the client sees an ExceptionGroup.
        # connect_sse has already hijacked the connection, so this response is never
        # sent; it exists only to satisfy the router.
        return Response()

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


def _mask_result(result: Any, masked: list[str]) -> Any:
    """Redact restricted field names from an upstream MCP tool's JSON result.

    Unlike a SQL row (a flat dict of known columns), a tool's result shape is
    arbitrary — best-effort: mask top-level dict keys, and within any top-level
    list of dicts (the common "rows"-style shape), never elsewhere."""
    if isinstance(result, dict):
        out = {}
        for k, v in result.items():
            if k in masked:
                out[k] = "***"
            elif isinstance(v, list):
                out[k] = [{ik: ("***" if ik in masked else iv) for ik, iv in item.items()}
                          if isinstance(item, dict) else item for item in v]
            else:
                out[k] = v
        return out
    return result


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
