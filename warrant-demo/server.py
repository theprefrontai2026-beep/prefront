"""The Arcadia console: a operator surface over a live Warrant deployment.

This is a BFF, not a page with a data file. Every view is backed by the PDS's
own API, and the console holds the service credential so the browser never
does — a UI that shipped a credential to the client would hand one to anyone
who opened developer tools.

Two things it adds on top of proxying.

**It attaches the operator's identity when they answer a step-up.** In a real
deployment that is the signed-in user's session; here it is fetched from the
stand-in IdP, because the point being shown is that an approval carries a
VERIFIED identity rather than a name the console typed. The agent cannot mint
one, which is what stops an agent approving its own request.

**It runs the scenario catalogue once at startup** and serves it as one view
among several. That used to be the whole product; it is now the evidence page.

Still the standard library only. This is the process most likely to be started
on a stranger's laptop before a meeting.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "warrant"))
sys.path.insert(0, str(HERE.parent / "warrant-service"))

import deployment  # noqa: E402
import runner  # noqa: E402
import scenarios  # noqa: E402
from world import APPROVED_COUNTERPARTIES, SUPPLIERS, usd  # noqa: E402

PDS_URL = os.environ.get("WARRANT_PDS_URL", "").rstrip("/")
CREDENTIAL = os.environ.get("WARRANT_CREDENTIAL", "")
IDP_TOKEN_URL = os.environ.get("WARRANT_IDP_TOKEN_URL", "")
OPEN_ON = "INJ-02"

# Which upstream paths the browser may reach, and with which method. An
# allow-list rather than a prefix match: a console that proxied anything under
# /v1/ would quietly expose Mission issuance and key registration to whoever
# opened it, using a credential the browser never had to hold.
PROXY_GET = {
    "/v1/config", "/v1/journal", "/v1/journal/stats", "/v1/trees",
    "/v1/missions", "/v1/approvals", "/healthz",
}
PROXY_POST_PREFIXES = ("/v1/approvals/", "/v1/trees/")


class Upstream:
    """The PDS, as this console talks to it."""

    @staticmethod
    def call(path: str, method: str = "GET", body: dict | None = None,
             timeout: float = 10.0) -> tuple[int, object]:
        if not PDS_URL:
            return 503, {"error": "no_pds", "detail": "WARRANT_PDS_URL is not set"}
        request = urllib.request.Request(
            f"{PDS_URL}{path}", method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={
                "Content-Type": "application/json",
                **({"Authorization": f"Bearer {CREDENTIAL}"} if CREDENTIAL else {}),
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.status, json.loads(response.read() or b"null")
        except urllib.error.HTTPError as exc:
            try:
                return exc.code, json.loads(exc.read())
            except Exception:
                return exc.code, {"error": "upstream", "detail": str(exc)}
        except Exception as exc:
            return 502, {"error": "unreachable", "detail": str(exc)}


def operator_token(subject: str) -> str:
    """The approver's own identity, from the identity provider.

    Fetched server-side per answer rather than held: a long-lived operator
    token sitting in the console would be exactly the credential this whole
    design is trying not to create.
    """
    if not IDP_TOKEN_URL:
        return ""
    request = urllib.request.Request(
        IDP_TOKEN_URL, data=json.dumps({"subject": subject}).encode(),
        method="POST", headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read())["subject_token"]


# --- the scenario catalogue, run once ---------------------------------------

SCENARIOS: dict = {}


def run_catalogue(results) -> dict:
    """Render an ALREADY-RUN catalogue.

    Takes the results rather than running them, because each run mints
    Missions and opens trees whose ids the service rightly refuses to reuse —
    so running the catalogue twice in one process is not merely wasteful, it
    fails. (It has now caused this exact failure twice; hence the parameter
    rather than a comment asking the next caller to remember.)
    """
    mission = deployment.build().mission
    return {
        "open_on": OPEN_ON,
        "decided_by": f"warrant-service at {PDS_URL}" if PDS_URL else "embedded engine",
        "identity_from": (os.environ.get("WARRANT_IDP_NAME", "the identity provider")
                          if IDP_TOKEN_URL else ""),
        "mission": {
            "operator": mission.subject,
            "instruction": deployment.INSTRUCTION,
            "actions": list(mission.action_classes),
            "counterparties": [SUPPLIERS[c].name for c in APPROVED_COUNTERPARTIES],
            "budget": usd(mission.budget.amount_minor),
            "window": "01:00 – 06:00",
            "max_depth": mission.max_depth,
        },
        "summary": runner.summary(results),
        "scenarios": [runner.result_json(r) for r in results],
    }


class Handler(BaseHTTPRequestHandler):
    console: bytes = b""

    def _send(self, body: bytes, content_type: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload: object, status: int = 200) -> None:
        self._send(json.dumps(payload).encode(), "application/json", status)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        route, query = parsed.path, parsed.query

        if route in ("/", "/index.html"):
            self._send(self.console, "text/html; charset=utf-8")
        elif route == "/healthz":
            self._json({"ok": True, "pds": bool(PDS_URL)})
        elif route == "/api/scenarios":
            self._json(SCENARIOS)
        elif route.startswith("/api/"):
            upstream = route[len("/api"):]
            if upstream not in PROXY_GET:
                self._json({"error": "not_proxied", "detail": upstream}, 404)
                return
            status, body = Upstream.call(f"{upstream}?{query}" if query else upstream)
            self._json(body, status)
        else:
            self._json({"error": "not_found"}, 404)

    def do_POST(self) -> None:  # noqa: N802
        route = urllib.parse.urlparse(self.path).path
        if not route.startswith("/api/"):
            self._json({"error": "not_found"}, 404)
            return
        upstream = route[len("/api"):]
        if not upstream.startswith(PROXY_POST_PREFIXES):
            self._json({"error": "not_proxied", "detail": upstream}, 404)
            return

        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            self._json({"error": "invalid_json"}, 400)
            return

        # Answering a step-up carries the operator's verified identity. The
        # browser never sees it, and never could mint one.
        if upstream.endswith(("/approve", "/deny")):
            approval_id = upstream.split("/")[3]
            status, approval = Upstream.call(f"/v1/approvals/{approval_id}")
            if status != 200:
                self._json(approval, status)
                return
            try:
                token = operator_token(approval.get("approver", ""))
            except Exception as exc:
                self._json({"error": "idp_unreachable", "detail": str(exc)}, 502)
                return
            if token:
                body["subject_token"] = token

        status, out = Upstream.call(upstream, method="POST", body=body)
        self._json(out, status)

    def log_message(self, *args) -> None:
        """Quiet: a terminal scrolling request logs behind a live demo reads as
        something going wrong."""


def main(argv: list[str] | None = None) -> int:
    global SCENARIOS

    parser = argparse.ArgumentParser(description="Arcadia Capital treasury console")
    parser.add_argument("--port", type=int, default=8140)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--check", action="store_true",
                        help="run the catalogue, print a one-line verdict, exit non-zero on drift")
    args = parser.parse_args(argv)

    results = runner.run_all(pds_url=PDS_URL)
    ok = all(r.matched_expectation for r in results)
    total = runner.summary(results)

    if args.check:
        drift = [r.scenario.key for r in results if not r.matched_expectation]
        print(f"{len(results)} situations · {total['prevented']} prevented · "
              + ("every decision as documented" if ok else f"DRIFT: {', '.join(drift)}"))
        return 0 if ok else 1

    SCENARIOS = run_catalogue(results)
    Handler.console = (HERE / "console.html").read_bytes()

    print(f"Arcadia console  ->  http://localhost:{args.port}")
    print(f"  decisions from: {'warrant-service at ' + PDS_URL if PDS_URL else 'the embedded engine'}")
    print(f"  {len(results)} situations · {total['prevented']} prevented")
    if not ok:
        print("  WARNING: at least one decision no longer matches its documented outcome")
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
