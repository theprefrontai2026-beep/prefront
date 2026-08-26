#!/usr/bin/env python3
"""LoanPro — scenario runner for the app agent.

LoanPro is an UNGOVERNED deployment: one LLM agent calling the loan shop's own
typed API over MCP, with no authorization layer. This server runs the scenario
catalogue against it and reports what the agent did, alongside `expected` — what
a governance layer would have done, as a description, not a second live run.
Self-contained on purpose: the loanpro vocabulary lives here, not in the
domain-neutral engine UI.

    ../prefront/semantic-mcp-server/.venv/bin/python demo_server.py
    # open http://localhost:8098

Endpoints:
    GET /              -> the diff view (web/index.html)
    GET /api/scenarios -> [{id, caller, role, capability, question, risk, expected}]
    GET /api/diff      -> [{id, caller, capability, question, risk, expected, ungoverned}]
                          (?only=L4,L8 to subset)
"""

from __future__ import annotations

import datetime as dt
import decimal
import json
import os
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import prefront_tracing as tracing
from scenarios import CALLERS, get_scenarios

_tracer = tracing.get_tracer("loanpro.demo")

HERE = Path(__file__).parent
PORT = int(os.environ.get("LOANPRO_DEMO_PORT", "8098"))
# The "before" is a SEPARATE service — an app-layer agent with typed business
# functions (no raw SQL) but no access-control policy.
UNGOVERNED_URL = os.environ.get("UNGOVERNED_URL", "http://localhost:8097/run")


def _clean(o):
    if isinstance(o, (dt.date, dt.datetime)):
        return o.isoformat()
    if isinstance(o, decimal.Decimal):
        return float(o)
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_clean(v) for v in o]
    return o


def list_scenarios(only=None) -> list[dict]:
    """The test-case catalog (metadata only, no DB run) — lets the UI list every
    case so the customer can run them one at a time."""
    out = []
    for s in get_scenarios(only):
        c = CALLERS[s["caller"]]
        out.append({
            "id": s["id"], "caller": c["name"], "role": c["role"],
            "capability": s["capability"], "question": s["question"],
            "risk": s["risk"], "expected": s["prefront"],
        })
    return out


def _ungoverned(question: str, caller: dict | None = None) -> dict:
    """Call the app-layer agent service (typed business functions, no policy).
    `caller` is the signed-in user the app knows — no enforcement."""
    req = urllib.request.Request(
        UNGOVERNED_URL, data=json.dumps({"question": question, "caller": caller}).encode("utf-8"),
        # tracing.inject adds the W3C traceparent header (nothing when tracing is
        # off) so the ungoverned run joins this scenario's trace.
        headers=tracing.inject({"Content-Type": "application/json"}))
    try:
        with urllib.request.urlopen(req, timeout=150) as resp:
            return json.load(resp)
    except Exception as e:
        return {"error": f"ungoverned service unreachable: {type(e).__name__}: {e}"}


def build_diff(only=None) -> list[dict]:
    """Run each selected test case LIVE against the app agent.

    LoanPro is an UNGOVERNED deployment: one agent calling the shop's own API
    over MCP. There is no governed counterpart, so each row carries the agent's
    run plus what a governance layer WOULD have done (`expected`) — the contrast
    is the risk description, not a second live run. Nothing is stored."""
    out = []
    for s in get_scenarios(only):
        c = CALLERS[s["caller"]]
        # One span per test case, parenting the agent run — this is what roots
        # the trace that the OOB observability pipeline ingests.
        with _tracer.start_as_current_span(f"scenario {s['id']}") as span:
            tracing.set_attributes(span, {
                tracing.SPAN_KIND: "CHAIN",
                tracing.INPUT_VALUE: s["question"],
                "scenario.id": s["id"],
                "scenario.caller": c["name"],
                "scenario.role": c["role"],
                "scenario.capability": s["capability"],
                "scenario.risk": s["risk"],
                "scenario.expected": s["prefront"],
            })
            u = _ungoverned(s["question"], {"name": c["name"], "user_id": c["user_id"]})
            tracing.set_attributes(span, {
                "scenario.ungoverned_tool": u.get("tool"),
                "scenario.ungoverned_row_count": u.get("row_count"),
            })
        out.append({
            "id": s["id"],
            "caller": c["name"],
            "role": c["role"],
            "capability": s["capability"],
            "question": s["question"],
            "risk": s["risk"],
            "expected": s["prefront"],
            "ungoverned": {
                "tool": u.get("tool"),
                "args": u.get("args"),
                "sql": u.get("sql"),
                "columns": u.get("columns"),
                "rows": u.get("rows"),
                "row_count": u.get("row_count"),
                "answer": u.get("answer"),
                "error": u.get("error"),
            },
        })
    return _clean(out)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # quiet
        pass

    def _send(self, code, body, ctype):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")  # demo: let the engine UI fetch it
        self.end_headers()
        self.wfile.write(data)

    def do_OPTIONS(self):  # CORS preflight
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path in ("/", "/index.html"):
            html = (HERE / "web" / "index.html").read_text(encoding="utf-8")
            return self._send(200, html, "text/html; charset=utf-8")
        if parsed.path == "/api/scenarios":
            only = parse_qs(parsed.query).get("only", [None])[0]
            only = only.split(",") if only else None
            return self._send(200, json.dumps(list_scenarios(only)), "application/json")
        if parsed.path == "/api/diff":
            only = parse_qs(parsed.query).get("only", [None])[0]
            only = only.split(",") if only else None
            try:
                diff = build_diff(only)
                return self._send(200, json.dumps(diff), "application/json")
            except Exception as e:  # surface to the page
                return self._send(500, json.dumps({"error": f"{type(e).__name__}: {e}"}),
                                  "application/json")
        self._send(404, "not found", "text/plain")


def main() -> int:
    tracing.setup("loanpro-orchestrator")  # no-op unless a collector is configured
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"LoanPro demo → http://localhost:{PORT}  (Ctrl-C to stop)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        srv.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
