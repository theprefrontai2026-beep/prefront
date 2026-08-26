#!/usr/bin/env python3
"""LoanPro — the shop's own business-function API, served over the MCP protocol.

This is the app's tool surface, NOT Prefront. It exposes exactly the typed
functions in ``app_tools.py`` and runs them; there is no policy bundle, no
identity resolution against a governance contract, no decision. A call arrives,
the SQL runs, rows come back.

Why it exists: the agent should talk to its tools the way a real deployment
does — over MCP, across a process boundary — rather than calling Python
functions in the same process. Everything the ungoverned demo is meant to show
(ownership bypass, ssn/credit-score leakage, an unguarded decide_loan, the CEL
filter gateway) is unchanged; only the transport moved.

Caller identity is per-connection, supplied by the trusted session layer as an
``X-LoanPro-User`` header or ``?user_id=`` — this is the app's own notion of the
signed-in officer (it is what ``get_my_applications`` scopes to), and the agent
cannot choose it. It is NOT an authorization boundary: every other tool ignores
it, which is precisely the governance gap being demonstrated.

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

# The signed-in user for the active MCP connection (set from the header in the
# SSE handler, read by the tool call). A ContextVar because one server process
# serves many concurrent connections.
user_var: contextvars.ContextVar[int | None] = contextvars.ContextVar("loanpro_user", default=None)


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
        uid = user_var.get()
        # One span per tool call. The MCP instrumentation carries the agent's
        # context across the transport, so this nests under the agent's span.
        with _tracer.start_as_current_span(f"tool {name}") as span:
            tracing.set_attributes(span, {
                tracing.SPAN_KIND: "TOOL",
                "tool.name": name,
                "app.tool": name,
                "app.caller.user_id": uid,
                tracing.INPUT_VALUE: tracing.as_json(args),
                tracing.INPUT_MIME: tracing.JSON_MIME,
            })
            sql, result = app_tools.dispatch(name, args, uid)
            result = dict(result)
            result["sql"] = sql
            if isinstance(result.get("rows"), list) and len(result["rows"]) > app_tools.MAX_ROWS:
                result["rows"] = result["rows"][: app_tools.MAX_ROWS]
            tracing.set_attributes(span, {
                "app.sql": sql,
                "app.row_count": result.get("row_count"),
                tracing.OUTPUT_VALUE: tracing.as_json(
                    {k: v for k, v in result.items() if k != "rows"}),
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
        raw = request.query_params.get("user_id") or request.headers.get("x-loanpro-user")
        try:
            uid = int(raw) if raw not in (None, "") else None
        except (TypeError, ValueError):
            uid = None
        token = user_var.set(uid)
        try:
            async with sse.connect_sse(request.scope, request.receive, request._send) as (read, write):
                await server.run(read, write, server.create_initialization_options())
        finally:
            user_var.reset(token)
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
            "tools": [t["function"]["name"] for t in app_tools.TOOLS],
        })

    app = Starlette(routes=[
        Route("/sse", endpoint=handle_sse),
        Mount("/messages/", app=sse.handle_post_message),
        Route("/healthz", endpoint=healthz),
    ])
    print(f"LoanPro app API (MCP, ungoverned) on :{PORT}  tools="
          f"{[t['function']['name'] for t in app_tools.TOOLS]}", flush=True)
    uvicorn.run(app, host="0.0.0.0", port=PORT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
