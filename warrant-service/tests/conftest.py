"""Fixtures for the service tests.

The action classes here are invented for the tests and belong to no
deployment. The service itself ships no vocabulary at all — a registry arrives
as a file a deployment writes — so a suite exercising it has to invent one.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "warrant"))
sys.path.insert(0, str(ROOT / "warrant-service"))

from warrant import IntentBinder, SigningKey  # noqa: E402
from warrantservice.config import Settings, load_registry  # noqa: E402

NOW = 1_800_000_000

REGISTRY_DOC = {
    "version": "test-registry-1",
    "actions": [
        {"name": "svc.read", "blast_radius": "low", "side_effect": False},
        {"name": "svc.update", "blast_radius": "medium", "side_effect": True},
        {"name": "svc.transfer", "blast_radius": "high", "side_effect": True},
    ],
}


@pytest.fixture
def registry_file(tmp_path: Path) -> Path:
    path = tmp_path / "registry.json"
    path.write_text(json.dumps(REGISTRY_DOC))
    return path


@pytest.fixture
def settings(registry_file: Path) -> Settings:
    registry, source = load_registry(str(registry_file))
    return Settings(
        issuer="test.authority",
        policy_version="test-policy-1",
        registry=registry,
        authority_key=SigningKey.generate("ma-1"),
        registry_path=source,
        authority_key_supplied=True,
    )


@pytest.fixture
def client(settings) -> TestClient:
    from warrantservice.app import create_app

    return TestClient(create_app(settings))


@pytest.fixture
def agent_key() -> SigningKey:
    return SigningKey.generate("agent-1")


@pytest.fixture
def world(client, agent_key):
    """A registered agent, a signed Mission and an open tree.

    Built through the HTTP surface rather than by reaching into app state, so
    the fixture itself exercises the setup path every integrator walks.
    """
    client.post("/v1/agent-keys", json=agent_key.verify_key().to_jwk()).raise_for_status()
    mission = client.post("/v1/missions", json={
        "mission_id": "m1",
        "subject": "operator@example.test",
        "instruction": "Do the scheduled batch and stop at the ceiling.",
        "action_classes": ["svc.read", "svc.update", "svc.transfer"],
        "resources": ["batch-a"],
        "counterparties": ["known-party"],
        "budget": {"amount_minor": 100_000, "currency": "USD", "max_calls": 20},
        "not_before": NOW - 100,
        "not_after": NOW + 3600,
        "max_depth": 1,
        "issued_at": NOW - 100,
    }).json()
    tree = client.post("/v1/trees", json={
        "tree_id": "t1", "mission_id": "m1", "root_actor": "root-agent",
    }).json()

    class World:
        def __init__(self):
            self.client = client
            self.agent_key = agent_key
            self.mission = mission
            self.tree = tree
            self.subject = "operator@example.test"
            self.binder = IntentBinder(agent_key, "t1", tree["root_id"])

        def decide(self, signed, observed=None, node_id=None, subject=None, now=NOW):
            return client.post("/v1/decisions", json={
                "signed_attestation": {
                    "attestation": signed.attestation.to_payload(),
                    "key_id": signed.key_id,
                    "signature": signed.signature,
                    "algorithm": signed.algorithm,
                },
                "observed_args": observed if observed is not None else {},
                "node_id": node_id or signed.attestation.node_id,
                "now": now,
                "token_subject": self.subject if subject is None else subject,
            })

    return World()
