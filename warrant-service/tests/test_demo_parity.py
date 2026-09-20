"""The service must decide exactly as the library does.

This is the test that makes the service interface trustworthy. A PDS behind
HTTP that quietly disagreed with the embedded engine would be the worst class
of bug in this system: invisible until an audit, and wrong in the direction of
permission.

So it runs the whole Arcadia catalogue twice — once with the engine in-process,
once against a real uvicorn server over real sockets, with real JSON
serialization — and compares every decision, every reason code and every
per-control result.

It lives here rather than in `warrant-demo/tests` because it needs FastAPI and
uvicorn, which the demo deliberately does not depend on: the demo runs from the
standard library plus `cryptography` so it can be started on a stranger's
laptop, and that property is worth protecting.
"""

from __future__ import annotations

import json
import socket
import sys
import threading
import time
from pathlib import Path

import pytest
import uvicorn

ROOT = Path(__file__).resolve().parents[2]
for p in (ROOT / "warrant", ROOT / "warrant-service", ROOT / "warrant-demo"):
    sys.path.insert(0, str(p))

import remote  # noqa: E402  (warrant-demo)
import runner  # noqa: E402  (warrant-demo)
import scenarios  # noqa: E402  (warrant-demo)
from warrantservice.app import create_app  # noqa: E402
from warrantservice.config import Settings, load_registry  # noqa: E402

from warrant import SigningKey  # noqa: E402


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def service_url(tmp_path_factory):
    """A real server on a real port.

    Deliberately not FastAPI's TestClient: that short-circuits the transport,
    and half of what is under test here is that the wire format survives a
    round trip. An in-process client would pass even if the codec were broken.
    """
    registry_path = tmp_path_factory.mktemp("cfg") / "registry.json"
    registry_path.write_text(json.dumps(remote.registry_document()))
    registry, source = load_registry(str(registry_path))

    app = create_app(
        Settings(
            issuer="arcadia.treasury.authority",
            policy_version="arcadia-ap-policy-7",
            registry=registry,
            authority_key=SigningKey.generate("arcadia-ma-1"),
            registry_path=source,
            authority_key_supplied=True,
        )
    )

    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    url = f"http://127.0.0.1:{port}"
    deadline = time.time() + 15
    while time.time() < deadline and not server.started:
        time.sleep(0.05)
    if not server.started:
        pytest.fail("the service never started")

    yield url

    server.should_exit = True
    thread.join(timeout=10)


@pytest.fixture(scope="module")
def both_runs(service_url):
    return {
        "embedded": runner.run_all(),
        "remote": runner.run_all(pds_url=service_url),
    }


def _by_key(results):
    return {r.scenario.key: r for r in results}


@pytest.mark.parametrize("key", [s.key for s in scenarios.CATALOGUE])
def test_every_situation_decides_identically_over_http(both_runs, key):
    embedded = _by_key(both_runs["embedded"])[key]
    remote_result = _by_key(both_runs["remote"])[key]

    assert [s.effect for s in remote_result.governed.steps] == [
        s.effect for s in embedded.governed.steps
    ], f"{key} decides differently through the service"

    assert [s.reasons for s in remote_result.governed.steps] == [
        s.reasons for s in embedded.governed.steps
    ], f"{key} gives different reason codes through the service"


@pytest.mark.parametrize("key", [s.key for s in scenarios.CATALOGUE])
def test_every_control_result_survives_the_wire(both_runs, key):
    """Not just the outcome — the full per-control record, which is what an
    evidence pack is made of."""
    embedded = _by_key(both_runs["embedded"])[key]
    remote_result = _by_key(both_runs["remote"])[key]

    for e_step, r_step in zip(embedded.governed.steps, remote_result.governed.steps):
        assert [(c.check_id, c.status, c.on_violation) for c in r_step.checks] == [
            (c.check_id, c.status, c.on_violation) for c in e_step.checks
        ]


def test_the_business_outcome_is_the_same_either_way(both_runs):
    embedded = runner.summary(both_runs["embedded"])
    remote_summary = runner.summary(both_runs["remote"])
    assert remote_summary == embedded
    assert remote_summary["all_as_expected"] is True


def test_the_service_charged_the_budget_for_the_calls_that_executed(both_runs, service_url):
    """The ledger lives in the service in remote mode, so this confirms the
    reserve/settle round trip actually moved it — a no-op adapter would still
    pass the decision tests above."""
    from warrantservice import RemotePolicyDecisionService

    client = RemotePolicyDecisionService(service_url)
    # Ids are namespaced per process run, because the service outlives any one
    # run and the engine refuses to reuse a tree id.
    budget = client.tree(remote.run_scoped("base-01-governed"))["budget"]
    assert budget["committed"] > 0
    assert budget["calls"] > 0


def test_the_demo_never_sends_the_agents_private_key(service_url):
    """The PDS is a separate party precisely because it does not trust whatever
    holds the signing key. Only the JWK should ever cross."""
    from warrantservice import RemotePolicyDecisionService

    published = RemotePolicyDecisionService(service_url).jwks()
    for jwk in RemotePolicyDecisionService(service_url)._call("GET", "/v1/agent-keys")["keys"]:
        assert set(jwk) <= {"kty", "crv", "kid", "x", "alg", "use"}
        assert "d" not in jwk  # the private scalar's JWK name
