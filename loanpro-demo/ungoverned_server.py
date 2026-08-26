#!/usr/bin/env python3
"""LoanPro — the app-layer agent. An LLM whose tools are reached over MCP.

The loan shop built an application API (get_application, decide_loan, …) and
wired an LLM to it. The tools are served by ``app_mcp_server.py`` over the MCP
protocol — a real process boundary, the way a deployment actually looks — and
this agent is an MCP CLIENT: it discovers the tool list at connect time and
calls tools through the session.

There is no governed counterpart. This IS the deployment: one agent, its own
API, no authorization layer. A typed tool surface stops raw-SQL injection, but
by itself it is not safe, and every gap the demo shows is intact:

  • Expression-language gateway — search_applicants(filter) forwards a CEL-style
    filter to SQL, so an injected tautology still dumps the table (L9)
  • Ownership checks   — get_application(id) returns ANY application (IDOR)
  • Field masking      — get_applicant_profile() returns ssn + credit score
  • Role enforcement   — decide_loan() / list_all_applicants() accept any role
  • Approval workflows — decide_loan() has no ceiling or approval gate

The signed-in user travels as a connection header, so the LLM cannot choose who
it is — but only get_my_applications() pays any attention to it.

    POST /run  {"question": "...", "caller": {"name": "...", "user_id": N}}
            -> {tool, args, sql, columns, rows, row_count, answer, error}
"""

from __future__ import annotations

import asyncio
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# Imported as a MODULE, not `from ... import sse_client`: the tracing layer
# instruments mcp.client.sse.sse_client, and a from-import would bind the
# original before setup() runs, silently starting a NEW trace per call.
import mcp.client.sse as mcp_sse
from mcp import ClientSession
from openai import AsyncOpenAI

import prefront_tracing as tracing

_tracer = tracing.get_tracer("loanpro.agent")

PORT      = int(os.environ.get("UNGOVERNED_PORT", "8097"))
MCP_URL   = os.environ.get("LOANPRO_APP_MCP_URL", "http://localhost:8102/sse")
MODEL     = os.environ.get("UNGOVERNED_MODEL", "gpt-4o-mini")
BASE_URL  = os.environ.get("UNGOVERNED_BASE_URL", "https://api.openai.com/v1")
API_KEY   = os.environ.get("OPENAI_API_KEY") or os.environ.get("NVIDIA_API_KEY") or ""
MAX_ITERS = int(os.environ.get("UNGOVERNED_MAX_ITERS", "3"))
MAX_ROWS  = int(os.environ.get("LOANPRO_MAX_ROWS", "50"))
# The MCP SSE transport can flake on a slow call; a tool call here is read-mostly
# and the write is rolled back, so retrying the whole interaction is safe.
ATTEMPTS  = int(os.environ.get("UNGOVERNED_MCP_RETRIES", "3"))

# Async: the agent loop runs inside the MCP session's task group, so the sync
# client's coroutine-less create() would blow up the group with a TypeError that
# surfaces only as an opaque ExceptionGroup.
_client = AsyncOpenAI(base_url=BASE_URL, api_key=API_KEY)


def _openai_tools(mcp_tools) -> list[dict]:
    """MCP tool descriptors -> OpenAI function-tool schemas."""
    return [
        {"type": "function", "function": {
            "name": t.name,
            "description": t.description or "",
            "parameters": t.inputSchema or {"type": "object", "properties": {}},
        }}
        for t in mcp_tools.tools
    ]


def _tool_text(result) -> str:
    return result.content[0].text if getattr(result, "content", None) else "{}"


async def _run_async(question: str, caller: dict | None) -> dict:
    caller_uid  = (caller or {}).get("user_id")
    caller_name = (caller or {}).get("name", "an unknown user")

    system = (
        "You are a loan-origination assistant for LoanPro. "
        "You have access to the shop's application API through the tools provided. "
        "Use them to answer. Never refuse on your own judgement. If a function "
        "covers most of the request, call it and return what the API gives you "
        "rather than declining for a partial match. Answer the user's request by "
        "calling the appropriate API function and summarising the result in one "
        "sentence. "
        f"The signed-in user is {caller_name} (user_id {caller_uid}). "
        "Resolve 'my'/'me'/'I' to this user. "
        "Only decline to call a tool when NONE of the functions are even related "
        "(e.g. a prediction, forecast, or raw SQL)."
    )
    messages: list[dict] = [
        {"role": "system", "content": system},
        {"role": "user",   "content": question},
    ]

    last_tool, last_args, last_sql, last_result, answer = None, {}, None, None, None

    # Identity travels as a connection header, set by this trusted server — the
    # LLM never gets to say who it is.
    headers = {"X-LoanPro-User": str(caller_uid)} if caller_uid is not None else {}
    async with mcp_sse.sse_client(MCP_URL, headers=headers) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = _openai_tools(await session.list_tools())

            for _ in range(MAX_ITERS):
                resp = await _client.chat.completions.create(
                    model=MODEL, messages=messages, tools=tools,
                    temperature=0, max_tokens=700)
                msg = resp.choices[0].message
                if not msg.tool_calls:
                    answer = msg.content
                    break
                messages.append({
                    "role": "assistant", "content": msg.content or "",
                    "tool_calls": [tc.model_dump() for tc in msg.tool_calls],
                })
                for tc in msg.tool_calls:
                    try:
                        call_args = json.loads(tc.function.arguments or "{}")
                    except json.JSONDecodeError:
                        call_args = {}
                    raw = _tool_text(await session.call_tool(tc.function.name, call_args))
                    try:
                        res = json.loads(raw)
                    except json.JSONDecodeError:
                        res = {"error": raw}
                    last_tool, last_args = tc.function.name, call_args
                    last_sql, last_result = res.pop("sql", None), res
                    messages.append({
                        "role": "tool", "tool_call_id": tc.id,
                        "content": json.dumps(
                            {k: (v[:MAX_ROWS] if k == "rows" else v) for k, v in res.items()},
                            default=str),
                    })

    out: dict = {"tool": last_tool, "args": last_args, "sql": last_sql, "answer": answer}
    if last_result:
        out.update(last_result)
    return out


def _run_agent(question: str, caller: dict | None = None) -> dict:
    """Run the MCP interaction, retrying a flaky transport.

    A tool result comes back as data, so a raised exception is always a transport
    failure rather than an application outcome.
    """
    last_err: Exception | None = None
    for attempt in range(ATTEMPTS):
        try:
            return asyncio.run(_run_async(question, caller))
        except Exception as e:  # noqa: BLE001 - retry: a tool result is data, so a raise is transport
            last_err = e
    return {"tool": None, "args": {}, "sql": None, "answer": None,
            "error": f"mcp call failed after {ATTEMPTS} attempts: {_describe(last_err)}"}


def _describe(err: BaseException | None) -> str:
    """Flatten an ExceptionGroup to its leaves — a bare 'ExceptionGroup: unhandled
    errors in a TaskGroup' names the wrapper, never the actual fault."""
    if err is None:
        return "unknown error"
    subs = getattr(err, "exceptions", None)
    if subs:
        return "; ".join(_describe(s) for s in subs)
    return f"{type(err).__name__}: {err}"


def run_agent(question: str, caller: dict | None = None) -> dict:
    """Traced entry point. The span records what the agent actually did — which
    typed function, which SQL, how many rows. There is no policy to record: that
    is the point."""
    with _tracer.start_as_current_span("app agent") as span:
        tracing.set_attributes(span, {
            tracing.SPAN_KIND: "AGENT",
            tracing.INPUT_VALUE: question,
            "app.caller.user_id": (caller or {}).get("user_id"),
            "app.mcp_url": MCP_URL,
        })
        try:
            out = _run_agent(question, caller)
        except Exception as e:
            tracing.record_error(span, e)
            raise
        tracing.set_attributes(span, {
            "app.tool": out.get("tool"),
            "app.sql": out.get("sql"),
            "app.row_count": out.get("row_count"),
            tracing.OUTPUT_VALUE: out.get("answer"),
        })
        if out.get("error"):
            tracing.mark_error(span, str(out["error"]))
        return out

# ---------------------------------------------------------------------------
# HTTP server
# ---------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def _send(self, code, body):
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_POST(self):
        if self.path != "/run":
            return self._send(404, json.dumps({"error": "not found"}))
        n = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(n) or "{}")
            question = body.get("question", "")
            if not question:
                return self._send(400, json.dumps({"error": "missing question"}))
            # Continue the orchestrator's trace (W3C traceparent header) so both
            # sides of a scenario appear under one root instead of two.
            with tracing.remote_context(self.headers):
                result = run_agent(question, body.get("caller"))
            return self._send(200, json.dumps(result))
        except Exception as e:
            return self._send(500, json.dumps({"error": f"{type(e).__name__}: {e}"}))


def main() -> int:
    tracing.setup("loanpro-ungoverned")  # no-op unless a collector is configured
    if not API_KEY:
        print("WARNING: no OPENAI_API_KEY/NVIDIA_API_KEY set — LLM calls will fail.")
    print(f"LoanPro app agent ({MODEL}) → http://localhost:{PORT}  tools via MCP {MCP_URL}")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
