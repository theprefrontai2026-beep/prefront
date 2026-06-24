#!/usr/bin/env python3
"""SecureBank — before/after demo server.

Serves a single page that shows each scenario WITHOUT Prefront (a realistic
app-layer agent with typed business functions but no access-control policy)
next to WITH Prefront (the governed decision), computed live. Self-contained
on purpose: the securebank vocabulary lives here, not in the domain-neutral
engine UI.

    ../prefront/semantic-mcp-server/.venv/bin/python demo_server.py
    # open http://localhost:8095

Endpoints:
    GET /              -> the diff view (web/index.html)
    GET /api/diff      -> [{id, caller, capability, question, ungoverned, governed}]
                          (?only=B4,B8 to subset)
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

import governed_agent  # the governed "after" — LLM → real Prefront pipeline, in-process
from scenarios import CALLERS, get_scenarios

HERE = Path(__file__).parent
PORT = int(os.environ.get("SECUREBANK_DEMO_PORT", "8095"))
# The "before" is a SEPARATE service — an app-layer agent with typed business
# functions (no raw SQL) but no access-control policy.
UNGOVERNED_URL = os.environ.get("UNGOVERNED_URL", "http://localhost:8096/run")


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
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=150) as resp:
            return json.load(resp)
    except Exception as e:
        return {"error": f"ungoverned service unreachable: {type(e).__name__}: {e}"}


def build_diff(only=None) -> list[dict]:
    """For each selected test case, run it LIVE both ways and merge:
    ungoverned = app-layer agent (typed functions, no policy);
    governed = LLM→Prefront agent. Nothing is stored — computed on each call."""
    out = []
    for s in get_scenarios(only):
        c = CALLERS[s["caller"]]
        u = _ungoverned(s["question"], {"name": c["name"], "user_id": c["user_id"]})
        g = governed_agent.run_agent(s["question"], s["caller"])  # caller key → identity over MCP
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
            "governed": {
                "intent": g.get("intent"),
                "args": g.get("args"),
                "outcome": g.get("outcome"),
                "status": g.get("status"),
                "reasons": g.get("reasons"),
                "approver_roles": g.get("approver_roles"),
                "masked_fields": g.get("masked_fields"),
                "rows": g.get("rows"),
                "row_count": g.get("row_count"),
                "answer": g.get("answer"),
                "error": g.get("error"),
                "governance": g.get("governance"),  # the deterministic decision trace
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
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"SecureBank demo → http://localhost:{PORT}  (Ctrl-C to stop)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        srv.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
