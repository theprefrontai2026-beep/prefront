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
import prefront_tracing as tracing
from scenarios import CALLERS, get_scenarios

_tracer = tracing.get_tracer("securebank.demo")

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


# --- Verdict-compatible surface -------------------------------------------
# Verdict speaks ONE catalogue shape — `/api/scenarios` -> {families:[…]} and
# `/api/run` -> a list of session-shaped runs — which LoanPro's orchestrator
# already emits. SecureBank emitted neither (a bare list, and /api/diff), so
# Verdict could not drive it at all: "Run all" was inert.
#
# Adapting HERE rather than teaching Verdict a second shape. Verdict is the
# generic evaluator; an orchestrator is the thing that knows its own demo. One
# consumer speaking two protocols would mean every future subject app picks a
# dialect, and Verdict growing a branch per app.
#
# /api/diff is untouched: securebank-demo/inline_regression.py grades against
# it, and its before/after shape is the point of that comparison.

_FAMILIES = {
    "B": "Gate — governance as a block, mask, or approval",
    "C": "Decision support — governance as grounded context",
}


def _family_of(scenario_id: str) -> str:
    return (scenario_id or "?")[:1].upper()


def _scenario_public(s: dict) -> dict:
    """One scenario in the shape Verdict's catalogue renders."""
    c = CALLERS[s["caller"]]
    fam = _family_of(s["id"])
    return {
        "id": s["id"], "family": fam, "family_label": _FAMILIES.get(fam, fam),
        "title": s["capability"], "checks": [],
        "caller": c["name"], "role": c["role"], "user_id": c["user_id"],
        "channel": "", "mode": "llm", "baseline": False, "hidden": False,
        "repeat": 1, "variant": "", "turns": [s["question"]], "steps": [],
        "risk": s["risk"],
        # SecureBank states its expectation as prose, not as the structured
        # findings LoanPro's out-of-band catalogue carries. Surfaced as a single
        # entry so Verdict shows it, rather than dropped for not fitting.
        "expected_findings": [{"check": "governed decision", "evidence": s["prefront"]}],
    }


def list_catalogue(only=None) -> dict:
    """`{families:[…], scenarios:[…]}` — the shape Verdict loads."""
    scenarios = [_scenario_public(s) for s in get_scenarios(only)]
    by_family: dict[str, list] = {}
    for sc in scenarios:
        by_family.setdefault(sc["family"], []).append(sc)
    families = [{"id": f, "label": _FAMILIES.get(f, f), "scenarios": rows}
                for f, rows in sorted(by_family.items())]
    return {"families": families, "scenarios": scenarios}


def build_run(only=None) -> list[dict]:
    """The catalogue as Verdict-shaped runs, from the GOVERNED side.

    Verdict evaluates what Prefront decided, so the governed call is the run:
    one scenario is one single-turn session whose only tool call is the
    governed intent, carrying the decision and the rows the caller actually
    received (masked ones included — that IS the result here).

    `session_id` is deliberately EMPTY. SecureBank is governed in-band and has
    no out-of-band tap, so no session exists to inspect; an id would send
    Verdict's flyout to /oob/sessions/<id> for a trace that will never arrive,
    and its "not ingested yet" message would be a lie rather than a wait.
    """
    out = []
    for s in get_scenarios(only):
        c = CALLERS[s["caller"]]
        pub = _scenario_public(s)
        with _tracer.start_as_current_span(f"scenario {s['id']}") as span:
            tracing.set_attributes(span, {
                tracing.SPAN_KIND: "CHAIN",
                tracing.INPUT_VALUE: s["question"],
                "scenario.id": s["id"], "scenario.caller": c["name"],
                "scenario.role": c["role"], "scenario.capability": s["capability"],
            })
            g = governed_agent.run_agent(s["question"], s["caller"])
        result = {"rows": g.get("rows") or [], "row_count": g.get("row_count")}
        if g.get("error"):
            result["error"] = g["error"]
        intent = g.get("intent") or ""
        out.append(_clean({
            **pub,
            "session_id": "",          # no OOB session — see the docstring
            "trace_id": None,
            "repeat_index": 0,
            "tools_called": [intent] if intent else [],
            "error": g.get("error"),
            "turns": [{
                "turn": 0, "mode": "llm", "user": s["question"],
                "answer": g.get("answer"),
                "tool_calls": ([{"tool": intent, "args": g.get("args") or {}, "result": result}]
                               if intent else []),
                "llm_calls": 1,
                "error": g.get("error"),
            }],
            # The decision itself, which is the whole point for an inline app.
            "governed": {
                "intent": intent, "outcome": g.get("outcome"), "status": g.get("status"),
                "reasons": g.get("reasons") or [], "masked_fields": g.get("masked_fields") or [],
                "approver_roles": g.get("approver_roles") or [],
            },
        }))
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
    """For each selected test case, run it LIVE both ways and merge:
    ungoverned = app-layer agent (typed functions, no policy);
    governed = LLM→Prefront agent. Nothing is stored — computed on each call."""
    out = []
    for s in get_scenarios(only):
        c = CALLERS[s["caller"]]
        # One span per test case, parenting BOTH runs — the before/after contrast
        # the demo is built around, readable as a single trace.
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
            g = governed_agent.run_agent(s["question"], s["caller"])  # caller key → identity over MCP
            tracing.set_attributes(span, {
                "scenario.ungoverned_tool": u.get("tool"),
                "scenario.ungoverned_row_count": u.get("row_count"),
                "scenario.governed_intent": g.get("intent"),
                "scenario.governed_outcome": g.get("outcome"),
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
            # {families:[…], scenarios:[…]} — a SUPERSET of the bare list this
            # used to return, so a caller reading `scenarios` is unaffected
            # while Verdict gets the `families` it groups by.
            return self._send(200, json.dumps(list_catalogue(only)), "application/json")
        if parsed.path == "/api/run":
            only = parse_qs(parsed.query).get("only", [None])[0]
            only = only.split(",") if only else None
            try:
                return self._send(200, json.dumps(build_run(only)), "application/json")
            except Exception as e:  # surface to the page
                return self._send(500, json.dumps({"error": f"{type(e).__name__}: {e}"}),
                                  "application/json")
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
    tracing.setup("securebank-orchestrator")  # no-op unless a collector is configured
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"SecureBank demo → http://localhost:{PORT}  (Ctrl-C to stop)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        srv.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
