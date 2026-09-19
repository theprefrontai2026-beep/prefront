"""Bake the run into a single file that needs no server.

For the case where the audience is not at a terminal: a link, an email
attachment, a laptop with no Python. The page already prefers `window.__RUN__`
over fetching `/api/results`, so the static build and the live server share one
implementation of the console rather than drifting into two — which is the
whole reason that branch exists in `console.html`.

    python3 build_static.py --out arcadia.html

The output is self-contained apart from the webfont, which falls back to the
system sans if the machine is offline.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "warrant"))

import runner  # noqa: E402
import server  # noqa: E402

# The console's own boot comment; the bake goes immediately before it so the
# global exists by the time the script runs.
MARKER = "<script>\n/* One file, two hosts."

OFFLINE_ERROR = (
    '`<div class="head"><h3>This run could not be loaded</h3>\n'
    '     <p class="q">Reload the page. (${esc(e.message)})</p></div>`'
)
SERVER_ERROR = (
    '`<div class="head"><h3>The demo server is not answering</h3>\n'
    '     <p class="q">Start it with <b>python server.py</b> from <b>warrant-demo/</b>, then reload.\n'
    '     (${esc(e.message)})</p></div>`'
)


def build() -> str:
    html = (HERE / "console.html").read_text()
    if MARKER not in html:
        raise SystemExit(
            "console.html no longer contains the boot marker this script splices "
            "against. Re-point MARKER rather than loosening the match — a silent "
            "miss would ship a static page that tries to fetch a server."
        )
    payload = server.build_payload()
    baked = "<script>window.__RUN__ = " + json.dumps(payload, separators=(",", ":")) + ";</script>\n"
    html = html.replace(MARKER, baked + MARKER, 1)
    # A shared link has no server to blame, so the failure copy changes.
    return html.replace(SERVER_ERROR, OFFLINE_ERROR)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default="arcadia.html")
    args = parser.parse_args(argv)

    results = runner.run_all()
    drift = [r.scenario.key for r in results if not r.matched_expectation]
    if drift:
        # Refusing rather than warning: a static build is the copy that gets
        # shared and outlives the session, so shipping one whose decisions no
        # longer match its own copy is the worst version of this failure.
        print(f"refusing to build: {', '.join(drift)} no longer decide as documented")
        return 1

    out = Path(args.out)
    out.write_text(build())
    total = runner.summary(results)
    print(f"{out} · {len(results)} situations · {total['prevented']} prevented · "
          f"{out.stat().st_size // 1024} KB, no server needed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
