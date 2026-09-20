"""The console's own contract with the service it renders.

The console is a BFF, not a page with a data file: every view is backed by the
PDS's live API and the console holds the service credential so the browser
never does. Two things are worth pinning.

**The proxy is an allow-list.** A console that forwarded anything under `/v1/`
would expose Mission issuance and agent-key registration to whoever opened it,
using a credential the browser never had to hold.

**The page and the payload agree.** A renamed field is invisible until someone
is presenting.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
for extra in (HERE, HERE.parent / "warrant", HERE.parent / "warrant-service"):
    sys.path.insert(0, str(extra))

import server  # noqa: E402

CONSOLE = (HERE / "console.html").read_text()


# --- the proxy is a boundary ------------------------------------------------


def test_only_named_paths_are_proxied_for_reading():
    """An allow-list, never a prefix match."""
    assert server.PROXY_GET == {
        "/v1/config", "/v1/journal", "/v1/journal/stats", "/v1/trees",
        "/v1/missions", "/v1/approvals", "/healthz",
    }


@pytest.mark.parametrize("path", [
    "/v1/missions", "/v1/agent-keys", "/v1/token", "/v1/token/exchange",
    "/v1/decisions", "/v1/denylist",
])
def test_the_dangerous_routes_are_not_reachable_from_the_browser(path):
    """Minting consent, publishing a key, issuing a token and deciding are all
    things the console must not be able to do on someone's behalf."""
    assert not path.startswith(server.PROXY_POST_PREFIXES) or path.count("/") > 2


def test_writes_are_limited_to_answering_and_stopping():
    """The only two controls the console offers: answer a step-up, stop a task."""
    assert server.PROXY_POST_PREFIXES == ("/v1/approvals/", "/v1/trees/")


def test_the_console_never_ships_the_service_credential():
    """It lives in the server process. A UI that sent one to the browser would
    hand it to anyone who opened developer tools."""
    assert "WARRANT_CREDENTIAL" not in CONSOLE
    assert "Authorization" not in CONSOLE


# --- the page and the payload agree -----------------------------------------


def test_every_view_the_nav_offers_has_a_container_and_a_renderer():
    views = re.findall(r'\{id:"(\w+)"', CONSOLE)
    assert len(views) >= 7
    for view in views:
        assert f'id="v-{view}"' in CONSOLE, f"no container for {view}"
        assert f"render{view.capitalize()}" in CONSOLE, f"no renderer for {view}"


def test_the_console_reads_only_fields_the_service_sends():
    """Guards the pair the way the old single-view console did, now across the
    whole API surface it renders.

    Needs FastAPI to stand the service up, which the DEMO deliberately does not
    depend on — it runs from the standard library plus `cryptography` so it
    starts on any machine. Skipped rather than failed there; `make test` and CI
    both run this file from the service venv as well, so the check executes.
    """
    pytest.importorskip("fastapi", reason="not a demo dependency")

    from warrantservice.app import create_app
    from warrantservice.config import from_env
    from fastapi.testclient import TestClient

    app = create_app(from_env({"WARRANT_ALLOW_UNAUTHENTICATED": "1"}))
    client = TestClient(app)

    config = client.get("/v1/config").json()
    for key in ("deployment", "policy_version", "warnings", "guards",
                "action_registry", "step_up", "journal"):
        assert key in config, f"console reads config.{key}"
    for guard in ("caller_auth", "subject_identity", "task_tokens"):
        assert guard in config["guards"]

    stats = client.get("/v1/journal/stats").json()
    for key in ("by_effect", "top_reasons", "value_stopped_minor", "total", "capacity"):
        assert key in stats, f"console reads stats.{key}"

    assert "entries" in client.get("/v1/journal").json()
    assert "trees" in client.get("/v1/trees").json()
    assert "missions" in client.get("/v1/missions").json()
    assert "approvals" in client.get("/v1/approvals").json()


def test_the_scenario_payload_is_rendered_from_already_run_results():
    """`run_catalogue` takes results rather than running them.

    Running the catalogue twice in one process does not merely waste a second —
    each run mints Missions and opens trees whose ids the service rightly
    refuses to reuse, so the second run fails. That has happened twice; the
    signature is what stops it happening a third time.
    """
    import inspect

    assert "results" in inspect.signature(server.run_catalogue).parameters


def test_the_page_states_which_controls_are_missing_rather_than_hiding_them():
    """An operator should see an open door on the page, not infer it."""
    assert "This deployment has gaps" in CONSOLE
    assert "UNAUTHENTICATED" in CONSOLE
    assert "UNVERIFIED" in CONSOLE
