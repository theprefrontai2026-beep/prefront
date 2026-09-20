"""The demo server: runs the catalogue for real, serves the console over it.

Deliberately the standard library and nothing else. This is the artefact most
likely to be run on a stranger's laptop ten minutes before a meeting, and a
demo that first needs a virtualenv, a package index and a working network is a
demo that does not get run. The only third-party import in the whole demo is
`cryptography`, which `warrant` needs to sign anything at all.

Results are computed once at startup and held. They are deterministic — the
agent is scripted and the clock is fixed — so recomputing per request would
burn CPU to produce identical bytes, and a presenter clicking through the rail
gets instant responses instead of a spinner.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "warrant"))

import deployment  # noqa: E402
import runner  # noqa: E402
import scenarios  # noqa: E402
from world import APPROVED_COUNTERPARTIES, SUPPLIERS, usd  # noqa: E402

# Which situation a cold visitor lands on. The injection case rather than the
# baseline: someone opening a shared link alone needs the reason this exists
# before they need the reassurance that it does not block everything, and the
# baseline is one click away in the rail, named so it is obvious.
OPEN_ON = "INJ-02"

# When set, every governed decision goes to a warrant-service over HTTP instead
# of the engine embedded in this process. The scenarios are untouched by the
# choice and decide identically either way — `warrant-service/tests/
# test_demo_parity.py` asserts that across all twelve — so this changes WHERE
# a decision is made, never what it is.
PDS_URL = os.environ.get("WARRANT_PDS_URL", "")


def build_payload(results=None) -> dict:
    """The console's whole data set.

    Takes `results` so a caller that has already run the catalogue does not run
    it again. That used to be a wasted CPU second; against a SHARED service it
    is a correctness bug, because each run mints Missions and trees whose ids
    the engine rightly refuses to reuse.
    """
    results = runner.run_all(pds_url=PDS_URL) if results is None else results
    mission = deployment.build().mission
    return {
        "open_on": OPEN_ON,
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
        "decided_by": f"warrant-service at {PDS_URL}" if PDS_URL else "embedded engine",
        "identity_from": (
            os.environ.get("WARRANT_IDP_NAME", "the identity provider")
            if os.environ.get("WARRANT_IDP_TOKEN_URL") else ""
        ),
        "scenarios": [runner.result_json(r) for r in results],
    }


class Handler(BaseHTTPRequestHandler):
    payload: bytes = b"{}"
    console: bytes = b""

    def _send(self, body: bytes, content_type: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        # No caching: a presenter who edits a scenario and restarts must see
        # the new run, not a copy their browser kept from the rehearsal.
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802  (stdlib's spelling)
        route = self.path.split("?", 1)[0]
        if route in ("/", "/index.html"):
            self._send(self.console, "text/html; charset=utf-8")
        elif route == "/api/results":
            self._send(self.payload, "application/json")
        elif route == "/healthz":
            self._send(b'{"ok":true}', "application/json")
        else:
            self._send(b"not found", "text/plain", status=404)

    def log_message(self, fmt: str, *args) -> None:
        """Quiet. A terminal scrolling request logs behind a live demo is noise
        the audience can read, and reads as something going wrong."""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Arcadia Capital treasury demo")
    parser.add_argument("--port", type=int, default=8140)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument(
        "--check", action="store_true",
        help="run the catalogue, print a one-line verdict, exit non-zero on drift",
    )
    args = parser.parse_args(argv)

    # Run the catalogue ONCE, here, and thread it through everything below.
    results = runner.run_all(pds_url=PDS_URL)
    ok = all(r.matched_expectation for r in results)
    total = runner.summary(results)

    if args.check:
        drift = [r.scenario.key for r in results if not r.matched_expectation]
        print(
            f"{len(results)} situations · {total['prevented']} prevented · "
            + ("every decision as documented" if ok else f"DRIFT: {', '.join(drift)}")
        )
        return 0 if ok else 1

    Handler.payload = json.dumps(build_payload(results)).encode()
    Handler.console = (HERE / "console.html").read_bytes()

    where = f"http://localhost:{args.port}"
    print(f"Arcadia treasury demo  ->  {where}")
    print("  decisions from: "
          + (f"warrant-service at {PDS_URL}" if PDS_URL else "the embedded engine"))
    print(f"  {len(results)} situations · {total['prevented']} prevented · "
          f"{total['incidents_ungoverned']} incidents without the control, "
          f"{total['incidents_governed']} with it")
    if not ok:
        print("  WARNING: at least one decision no longer matches its documented outcome")
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
