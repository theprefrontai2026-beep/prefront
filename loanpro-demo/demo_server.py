#!/usr/bin/env python3
"""LoanPro — scenario runner (orchestrator) for the app agent.

LoanPro is an UNGOVERNED deployment: one LLM agent calling the loan shop's own
typed API over MCP, with no authorization layer. This server drives the
scenario catalogue (scenarios.py) as SESSIONS against the agent and reports
the transcript alongside ``expected_findings`` — what Prefront's out-of-band
checks should report for that session. Nothing is enforced here; this is the
harness that produces the traces the checks evaluate.

    GET  /api/scenarios            -> {families:[{id,label,scenarios:[...]}], scenarios:[...]}
    GET  /api/run?only=F2-01,F3-08&repeat=1&variant=v1
                                   -> [{id, session_id, trace_id, turns:[...], expected_findings, ...}]
    GET  /api/diff                 -> alias of /api/run (kept for the UI's existing call)
    GET  /api/agent                -> the agent's /healthz

One trace per session: this server opens a ``session <id>`` root span, and
every turn it sends carries that context (W3C traceparent), so the agent's
``turn <n>`` spans, its LLM calls and the tool server's ``tool <name>`` spans
all nest under one root. ``repeat`` runs a scenario N times (population checks
need many sessions); ``variant`` selects the agent's prompt/model variant.
"""

from __future__ import annotations

import datetime as dt
import decimal
import json
import os
import urllib.error
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import prefront_tracing as tracing
from scenarios import CALLERS, FAMILIES, get_scenarios, turns_of

_tracer = tracing.get_tracer("loanpro.demo")

HERE = Path(__file__).parent
PORT = int(os.environ.get("LOANPRO_DEMO_PORT", "8098"))
AGENT_URL = (os.environ.get("AGENT_URL")
             or os.environ.get("UNGOVERNED_URL", "http://localhost:8097")).removesuffix("/run").rstrip("/")
MAX_REPEAT = int(os.environ.get("LOANPRO_MAX_REPEAT", "25"))


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


def _agent(method: str, path: str, body: dict | None = None, timeout: int = 180) -> dict:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        AGENT_URL + path, data=data, method=method,
        # tracing.inject adds the W3C traceparent header (nothing when tracing is
        # off) so the agent's turn joins this session's trace.
        headers=tracing.inject({"Content-Type": "application/json"}))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:  # the agent answers errors as JSON
        try:
            return json.load(e)
        except Exception:  # noqa: BLE001
            return {"error": f"agent HTTP {e.code}"}
    except Exception as e:  # noqa: BLE001
        return {"error": f"agent unreachable: {type(e).__name__}: {e}"}


def _public(s: dict) -> dict:
    c = CALLERS[s["caller"]]
    turns = turns_of(s)
    return {
        "id": s["id"], "family": s["family"], "family_label": FAMILIES.get(s["family"], s["family"]),
        "title": s["title"], "checks": s.get("checks", []),
        "caller": c["name"], "role": c["role"], "user_id": c["user_id"],
        "channel": s.get("channel") or c["channel"],
        "mode": s.get("mode", "llm"), "baseline": bool(s.get("baseline")),
        "hidden": bool(s.get("hidden")), "repeat": s.get("repeat", 1),
        "variant": s.get("variant", "v1"),
        "turns": [t["content"] for t in turns],
        "steps": [[f"{st['tool']}({', '.join(f'{k}={v}' for k, v in (st.get('args') or {}).items())})"
                   for st in (t["steps"] or [])] for t in turns],
        "risk": s.get("risk", ""), "expected_findings": s.get("expected_findings", []),
        "demonstrates": s.get("demonstrates", []),
    }


def list_scenarios(only=None) -> dict:
    scns = [_public(s) for s in get_scenarios(only)]
    fams = [{"id": fid, "label": label, "scenarios": [s for s in scns if s["family"] == fid]}
            for fid, label in FAMILIES.items()]
    return {"families": [f for f in fams if f["scenarios"]], "scenarios": scns}


def run_session(s: dict, variant: str | None = None, repeat_index: int = 0) -> dict:
    """One scenario = one session = one trace."""
    c = CALLERS[s["caller"]]
    channel = s.get("channel") or c["channel"]
    variant = variant or s.get("variant") or "v1"
    session_id = f"sess_{s['id'].lower().replace('-', '')}_{uuid.uuid4().hex[:8]}"
    pub = _public(s)
    turns_out: list[dict] = []
    error = None
    tools: list[str] = []
    with _tracer.start_as_current_span(f"session {s['id']}") as span:
        tracing.set_attributes(span, {
            tracing.SPAN_KIND: "CHAIN",
            tracing.INPUT_VALUE: (pub["turns"] or [""])[0] or "",
            "session.id": session_id,
            "user.id": c["user_id"],
            "app.user.name": c["name"],
            "app.user.role": c["role"],
            "app.channel": channel,
            "app.variant": variant,
            "scenario.id": s["id"],
            "scenario.family": s["family"],
            "scenario.title": s["title"],
            "scenario.checks": s.get("checks", []),
            "scenario.policy": sorted({p.strip() for f in s.get("expected_findings", [])
                                       for p in str(f.get("policy", "")).split(",") if p.strip()}),
            "scenario.mode": s.get("mode", "llm"),
            "scenario.baseline": bool(s.get("baseline")),
            "scenario.repeat_index": repeat_index,
            "scenario.caller": c["name"],
            "scenario.role": c["role"],
        })
        created = _agent("POST", "/sessions", {
            "session_id": session_id, "scenario_id": s["id"], "variant": variant,
            "channel": channel,
            "caller": {"name": c["name"], "user_id": c["user_id"], "role": c["role"]},
        })
        if created.get("error"):
            error = created["error"]
        else:
            for t in turns_of(s):
                if t["steps"] is not None:
                    rec = _agent("POST", f"/sessions/{session_id}/replay",
                                 {"content": t["content"], "steps": t["steps"], "answer": t["answer"]})
                else:
                    rec = _agent("POST", f"/sessions/{session_id}/messages", {"content": t["content"]})
                turns_out.append(rec)
                if rec.get("error") and not rec.get("turn"):
                    error = rec["error"]
                    break
        tools = [tc["tool"] for t in turns_out for tc in (t.get("tool_calls") or [])]
        tracing.set_attributes(span, {
            "app.tools_called": tools,
            "scenario.turns": len(turns_out),
            "scenario.tool_calls": len(tools),
            tracing.OUTPUT_VALUE: (turns_out[-1].get("answer") if turns_out else None),
        })
        if error:
            tracing.mark_error(span, error)
        trace_id = _trace_id(span)
    return _clean({**pub, "session_id": session_id, "trace_id": trace_id, "variant": variant,
                   "repeat_index": repeat_index, "turns": turns_out,
                   "tools_called": tools, "error": error})


def _trace_id(span) -> str | None:
    try:
        ctx = span.get_span_context()
        return format(ctx.trace_id, "032x") if ctx and ctx.trace_id else None
    except Exception:  # noqa: BLE001
        return None


def build_run(only=None, repeat: int | None = None, variant: str | None = None) -> list[dict]:
    out = []
    for s in get_scenarios(only):
        n = min(MAX_REPEAT, max(1, repeat or s.get("repeat", 1)))
        for i in range(n):
            out.append(run_session(s, variant, i))
    return out


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
        q = parse_qs(parsed.query)
        only = q.get("only", [None])[0]
        only = only.split(",") if only else None
        if parsed.path in ("/", "/index.html"):
            html = (HERE / "web" / "index.html").read_text(encoding="utf-8")
            return self._send(200, html, "text/html; charset=utf-8")
        if parsed.path == "/api/scenarios":
            return self._send(200, json.dumps(list_scenarios(only)), "application/json")
        if parsed.path == "/api/agent":
            return self._send(200, json.dumps(_agent("GET", "/healthz")), "application/json")
        if parsed.path in ("/api/run", "/api/diff"):
            try:
                repeat = int(q.get("repeat", [0])[0] or 0) or None
            except ValueError:
                repeat = None
            variant = q.get("variant", [None])[0] or None
            try:
                return self._send(200, json.dumps(build_run(only, repeat, variant)), "application/json")
            except Exception as e:  # surface to the page
                return self._send(500, json.dumps({"error": f"{type(e).__name__}: {e}"}),
                                  "application/json")
        self._send(404, "not found", "text/plain")


def main() -> int:
    tracing.setup("loanpro-orchestrator")  # no-op unless a collector is configured
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"LoanPro demo → http://localhost:{PORT}  agent {AGENT_URL}  (Ctrl-C to stop)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        srv.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
