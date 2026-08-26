#!/usr/bin/env python3
"""LoanPro — the shop's own business-function API, served over the MCP protocol.

This is the app's tool surface, NOT Prefront. It exposes exactly the typed
functions in ``app_tools.py`` and runs them; there is no policy bundle, no
identity resolution against a governance contract, no decision. A call arrives,
the SQL runs, rows come back.

What it DOES do is leave a complete, honest trace of every call — that is the
material Prefront's out-of-band checks consume. One ``tool <name>`` span per
call carries:

    session.id / user.id / app.user.role / app.channel   who, from where, in which session
    app.intent / app.side_effect                        the approved-catalog entry (or none)
    input.value                                          the arguments, verbatim
    output.value                                         the result INCLUDING rows (capped)
    app.sql / app.row_count                              what actually ran, how much came back
    app.trust = untrusted                                on borrower-supplied content
    status = ERROR                                       when the tool failed

Caller identity, role, channel and session id are per-connection, supplied by
the trusted session layer as ``X-LoanPro-*`` headers — the agent's LLM cannot
choose them. They are NOT an authorization boundary: only ``get_my_applications``
pays any attention to the user, which is precisely the gap being demonstrated.

    GET  /sse        MCP event stream
    POST /messages/  MCP client→server
    GET  /healthz    tool list

`mcp` must stay <2 — 2.0 removed the low-level Server.list_tools()/call_tool()
decorators this is built on (see the root CLAUDE.md).
"""

from __future__ import annotations

import contextvars
import json
import os
from typing import Any

import app_tools
import prefront_tracing as tracing

_tracer = tracing.get_tracer("loanpro.app_mcp")

PORT = int(os.environ.get("LOANPRO_APP_MCP_PORT", "8102"))
# Rows kept in the tool span's output.value. The full (MAX_ROWS-capped) result
# still goes back to the agent; the trace copy is what the evaluator reads.
TRACE_ROWS = int(os.environ.get("LOANPRO_TRACE_ROWS", "20"))

# Per-connection context (set from the headers in the SSE handler, read by the
# tool call). ContextVars because one server process serves many connections.
conn_var: contextvars.ContextVar[dict] = contextvars.ContextVar("loanpro_conn", default={})


def _conn_from_headers(query: Any, headers: Any) -> dict:
    def get(name: str, qkey: str | None = None) -> str | None:
        v = (query.get(qkey) if qkey else None) or headers.get(name)
        return v if v not in (None, "") else None
    raw_uid = get("x-loanpro-user", "user_id")
    try:
        uid = int(raw_uid) if raw_uid is not None else None
    except (TypeError, ValueError):
        uid = None
    return {
        "user_id": uid,
        "role": get("x-loanpro-role"),
        "channel": get("x-loanpro-channel"),
        "session_id": get("x-loanpro-session"),
    }


def build_server() -> Any:
    from mcp.server.lowlevel import Server
    import mcp.types as types

    server = Server("loanpro-app-api")

    @server.list_tools()
    async def list_tools() -> list[Any]:
        return [
            types.Tool(
                name=t["function"]["name"],
                description=t["function"]["description"],
                inputSchema=t["function"]["parameters"],
            )
            for t in app_tools.TOOLS
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict | None) -> list[Any]:
        args = arguments or {}
        conn = conn_var.get()
        uid = conn.get("user_id")
        meta = app_tools.INTENTS.get(name)
        # One span per tool call. The MCP instrumentation carries the agent's
        # context across the transport, so this nests under the agent's turn.
        with _tracer.start_as_current_span(f"tool {name}") as span:
            tracing.set_attributes(span, {
                tracing.SPAN_KIND: "TOOL",
                "tool.name": name,
                "app.tool": name,
                "app.intent": (meta or {}).get("intent") or "",
                "app.side_effect": (meta or {}).get("side_effect") or "",
                "app.catalog": "approved" if meta else "off_catalog",
                "app.trust": (meta or {}).get("trust") or "",
                "session.id": conn.get("session_id"),
                "user.id": uid,
                "app.caller.user_id": uid,
                "app.user.role": conn.get("role"),
                "app.channel": conn.get("channel"),
                tracing.INPUT_VALUE: tracing.as_json(args),
                tracing.INPUT_MIME: tracing.JSON_MIME,
            })
            sql, result = app_tools.dispatch(name, args, uid)
            result = dict(result)
            result["sql"] = sql
            if isinstance(result.get("rows"), list) and len(result["rows"]) > app_tools.MAX_ROWS:
                result["rows"] = result["rows"][: app_tools.MAX_ROWS]
            traced = {k: (v[:TRACE_ROWS] if k == "rows" and isinstance(v, list) else v)
                      for k, v in result.items()}
            tracing.set_attributes(span, {
                "app.sql": sql,
                "app.row_count": result.get("row_count"),
                "app.columns": result.get("columns"),
                tracing.OUTPUT_VALUE: tracing.as_json(traced),
                tracing.OUTPUT_MIME: tracing.JSON_MIME,
            })
            if result.get("error"):
                tracing.mark_error(span, str(result["error"]))
            return [types.TextContent(type="text", text=json.dumps(result, indent=2, default=str))]

    return server


def main() -> int:
    import uvicorn
    from mcp.server.sse import SseServerTransport
    from starlette.applications import Starlette
    from starlette.requests import Request
    from starlette.responses import JSONResponse, Response
    from starlette.routing import Mount, Route

    tracing.setup("loanpro-app-mcp")   # no-op unless a collector is configured
    server = build_server()
    sse = SseServerTransport("/messages/")

    async def handle_sse(request: Request):
        token = conn_var.set(_conn_from_headers(request.query_params, request.headers))
        try:
            async with sse.connect_sse(request.scope, request.receive, request._send) as (read, write):
                await server.run(read, write, server.create_initialization_options())
        finally:
            conn_var.reset(token)
        # Starlette >=1.0 does `await (await endpoint(request))(scope, receive, send)`,
        # so an endpoint that returns None dies with
        #   TypeError: 'NoneType' object is not callable
        # AFTER the stream has already been served. connect_sse has hijacked the
        # connection by now, so this response is never actually sent — it exists
        # only to satisfy the router.
        return Response()

    async def healthz(_request: Request):
        return JSONResponse({
            "status": "ok",
            "governed": False,
            "tools": app_tools.tool_names(),
            "off_catalog": [n for n, m in app_tools.INTENTS.items() if m is None],
        })

    app = Starlette(routes=[
        Route("/sse", endpoint=handle_sse),
        Mount("/messages/", app=sse.handle_post_message),
        Route("/healthz", endpoint=healthz),
    ])
    print(f"LoanPro app API (MCP, ungoverned) on :{PORT}  tools={app_tools.tool_names()}", flush=True)
    uvicorn.run(app, host="0.0.0.0", port=PORT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
