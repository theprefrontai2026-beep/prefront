"""The operator-facing reads: the decision journal, the estate, the config.

The PDS decided and forgot, which is defensible for a component on a 2ms
budget and useless for everyone else. These are the surfaces an operator
investigates from, an auditor generates an evidence pack out of, and a reviewer
uses to answer "what is this deployment actually enforcing?".
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from conftest import NOW, REGISTRY_DOC
from warrantservice.app import create_app
from warrantservice.auth import hash_secret, load as load_creds
from warrantservice.config import Settings, from_env, load_registry
from warrantservice.journal import Journal

from warrant import IntentBinder, SigningKey

AUTH = {"Authorization": "Bearer c.s"}


@pytest.fixture
def world(tmp_path):
    registry_path = tmp_path / "r.json"
    registry_path.write_text(json.dumps(REGISTRY_DOC))
    registry, source = load_registry(str(registry_path))
    creds = tmp_path / "c.json"
    creds.write_text(json.dumps({"clients": [{
        "client_id": "c", "secret_sha256": hash_secret("s"),
        "scopes": ["decide", "read", "mission:issue", "tree:manage",
                   "tree:revoke", "agent-key:register"],
        "description": "test client",
    }]}))
    app = create_app(Settings(
        issuer="test.authority", policy_version="p1", registry=registry,
        authority_key=SigningKey.generate("ma-1"), registry_path=source,
        authority_key_supplied=True, authenticator=load_creds(str(creds)),
        deployment_name="Test Deployment",
    ))
    client = TestClient(app)

    class World:
        def __init__(self):
            self.client = client
            self.key = SigningKey.generate("agent-1")
            client.post("/v1/agent-keys", headers=AUTH, json=self.key.verify_key().to_jwk())
            client.post("/v1/missions", headers=AUTH, json={
                "mission_id": "m1", "subject": "op@example.test",
                "instruction": "Settle the batch.",
                "action_classes": ["svc.read", "svc.update", "svc.transfer"],
                "counterparties": ["known-party"],
                "budget": {"amount_minor": 100_000, "currency": "USD"},
                "not_before": NOW - 100, "not_after": NOW + 3600})
            self.tree = client.post("/v1/trees", headers=AUTH, json={
                "tree_id": "t1", "mission_id": "m1", "root_actor": "agent"}).json()
            self.binder = IntentBinder(self.key, "t1", self.tree["root_id"])

        def call(self, action="svc.read", counterparty="", amount=0, args=None,
                 observed=None):
            args = args or {"n": amount}
            signed = self.binder.attest(action=action, args=args,
                                        counterparty=counterparty, amount_minor=amount)
            return client.post("/v1/decisions", headers=AUTH, json={
                "signed_attestation": {
                    "attestation": signed.attestation.to_payload(),
                    "key_id": signed.key_id, "signature": signed.signature,
                    "algorithm": signed.algorithm},
                "observed_args": observed if observed is not None else args,
                "node_id": self.tree["root_id"], "now": NOW,
                "token_subject": "op@example.test"}).json()

    return World()


# --- the journal ------------------------------------------------------------


def test_every_decision_is_recorded_with_enough_context_to_read_alone(world):
    world.call(action="svc.transfer", counterparty="known-party", amount=900)

    entry = world.client.get("/v1/journal", headers=AUTH).json()["entries"][0]
    assert entry["effect"] == "allow"
    assert entry["action"] == "svc.transfer"
    assert entry["counterparty"] == "known-party"
    assert entry["amount_minor"] == 900
    assert entry["subject"] == "op@example.test"
    assert entry["policy_version"] == "p1"
    assert len(entry["checks"]) == 12


def test_refusals_are_recorded_too(world):
    """Especially these — a denial nobody can find later is a denial nobody
    can explain later."""
    world.call(action="svc.update", counterparty="stranger")
    world.call(args={"n": 1}, observed={"n": 999})

    effects = [e["effect"] for e in
               world.client.get("/v1/journal", headers=AUTH).json()["entries"]]
    assert "deny" in effects and "step_up" in effects


def test_the_journal_reads_newest_first(world):
    for i in range(3):
        world.call(args={"i": i})
    entries = world.client.get("/v1/journal", headers=AUTH).json()["entries"]
    assert [e["seq"] for e in entries] == sorted((e["seq"] for e in entries), reverse=True)


@pytest.mark.parametrize("query,expect", [
    ("effect=deny", "deny"),
    ("effect=step_up", "step_up"),
    ("action=svc.update", "svc.update"),
])
def test_the_journal_filters(world, query, expect):
    world.call(action="svc.update", counterparty="stranger")
    world.call(args={"n": 1}, observed={"n": 2})
    world.call(action="svc.read")

    entries = world.client.get(f"/v1/journal?{query}", headers=AUTH).json()["entries"]
    assert entries
    field = "effect" if query.startswith("effect") else "action"
    assert all(e[field] == expect for e in entries)


def test_filtering_by_reason_code_finds_the_control_that_fired(world):
    """How an operator answers "show me everything the tripwire stopped"."""
    world.call(action="svc.update", counterparty="stranger")
    entries = world.client.get("/v1/journal?reason=pds.counterparty_scope",
                               headers=AUTH).json()["entries"]
    assert len(entries) == 1


def test_stats_answer_what_an_operator_asks(world):
    world.call(action="svc.transfer", counterparty="known-party", amount=100)
    world.call(action="svc.update", counterparty="stranger", amount=5_000)
    world.call(args={"n": 1}, observed={"n": 2})

    stats = world.client.get("/v1/journal/stats", headers=AUTH).json()
    assert stats["by_effect"]["allow"] == 1
    assert stats["by_effect"]["step_up"] == 1
    assert stats["by_effect"]["deny"] == 1
    assert stats["value_held_minor"] == 5_000
    assert stats["top_reasons"]


def test_the_journal_is_bounded_and_drops_the_oldest():
    """A fixed memory cost rather than a leak that takes the plane down at 3am.
    The newest end is the one worth keeping: investigation works backwards."""
    from warrant import ActionAttestation, Decision

    journal = Journal(capacity=2)
    att = ActionAttestation(tree_id="t", node_id="n", action="a", args_hash="h")
    for i in range(5):
        journal.record(
            decision=Decision(effect="allow", tree_id="t", node_id="n", mission_id="m"),
            attestation=att, subject="s", at=1000 + i)
    assert len(journal) == 2
    assert [e.at for e in journal.query()] == [1004, 1003]


def test_the_journal_never_stores_a_payload(world):
    """The control zone holds hashes; payloads stay in the tenant's store."""
    world.call(args={"account_number": "GB29-SECRET", "amount": 5})
    dumped = json.dumps(world.client.get("/v1/journal", headers=AUTH).json())
    assert "GB29-SECRET" not in dumped


def test_reading_the_journal_needs_the_read_scope(world, tmp_path):
    assert world.client.get("/v1/journal").status_code == 401


# --- the estate -------------------------------------------------------------


def test_tasks_can_be_listed_with_their_budget(world):
    world.client.post("/v1/trees/t1/reservations", headers=AUTH,
                      json={"amount_minor": 25_000})
    trees = world.client.get("/v1/trees", headers=AUTH).json()["trees"]
    assert len(trees) == 1
    assert trees[0]["tree_id"] == "t1"
    assert trees[0]["budget"]["outstanding"] == 25_000
    assert trees[0]["revoked"] is False


def test_a_revoked_task_shows_why(world):
    world.client.post("/v1/trees/t1/revoke", headers=AUTH,
                      json={"reason": "operator pressed stop"})
    tree = world.client.get("/v1/trees", headers=AUTH).json()["trees"][0]
    assert tree["revoked"] is True
    assert tree["revoked_reason"] == "operator pressed stop"


def test_missions_can_be_listed_with_their_bounds(world):
    missions = world.client.get("/v1/missions", headers=AUTH).json()["missions"]
    assert missions[0]["mission_id"] == "m1"
    assert missions[0]["current"] is True
    assert missions[0]["counterparties"] == ["known-party"]


def test_a_superseded_mission_is_visible_as_superseded(world):
    world.client.post("/v1/missions", headers=AUTH, json={
        "mission_id": "m2", "subject": "op@example.test",
        "instruction": "Revised.", "supersedes": "m1"})
    by_id = {m["mission_id"]: m for m in
             world.client.get("/v1/missions", headers=AUTH).json()["missions"]}
    assert by_id["m1"]["current"] is False
    assert by_id["m1"]["superseded_by"] == "m2"


def test_a_listed_mission_carries_no_instruction_text(world):
    """Only the hash. The instruction is user content and stays in the tenant's
    zone — it is exactly the text that carries a name or an account number."""
    mission = world.client.get("/v1/missions", headers=AUTH).json()["missions"][0]
    assert mission["instruction_hash"]
    assert "instruction" not in mission


# --- configuration ----------------------------------------------------------


def test_config_reports_what_is_enforced_in_one_request(world):
    body = world.client.get("/v1/config", headers=AUTH).json()
    assert body["deployment"] == "Test Deployment"
    assert body["guards"]["caller_auth"]["enabled"] is True
    assert [a["name"] for a in body["action_registry"]["actions"]]
    assert body["step_up"]["channels"] == ["log"]


def test_config_names_the_gaps_rather_than_hiding_them(world):
    """An operator should be able to see an open door in one request rather
    than infer it from what is absent."""
    warnings = world.client.get("/v1/config", headers=AUTH).json()["warnings"]
    assert any("identity provider" in w for w in warnings)
    assert any("task tokens" in w for w in warnings)


def test_a_fully_guarded_deployment_reports_no_warnings(tmp_path):
    from warrantservice.oidc import OidcSettings

    registry_path = tmp_path / "r.json"
    registry_path.write_text(json.dumps(REGISTRY_DOC))
    registry, source = load_registry(str(registry_path))
    creds = tmp_path / "c.json"
    creds.write_text(json.dumps({"clients": [{
        "client_id": "c", "secret_sha256": hash_secret("s"), "scopes": ["read"]}]}))
    app = create_app(Settings(
        issuer="i", policy_version="p", registry=registry,
        authority_key=SigningKey.generate("k"), registry_path=source,
        authority_key_supplied=True, authenticator=load_creds(str(creds)),
        oidc=OidcSettings(issuer="https://idp.test", audience="a"),
        task_tokens_required=True, token_key=SigningKey.generate("t"),
        token_key_supplied=True))
    assert TestClient(app).get("/v1/config", headers=AUTH).json()["warnings"] == []


def test_config_exposes_no_secret(world):
    """It lists which credentials exist and what they may do — never a secret,
    not even its hash.

    Asserted on the STRUCTURE rather than by scanning the JSON for the word
    "secret": the first version of this test did the latter and failed on
    pytest's own temp directory, which is named after the test. A substring
    check over a document containing filesystem paths proves very little.
    """
    body = world.client.get("/v1/config", headers=AUTH).json()
    clients = body["guards"]["caller_auth"]["clients"]
    assert clients
    for client in clients:
        assert set(client) == {"client_id", "scopes", "description"}

    dumped = json.dumps(clients)
    assert hash_secret("s") not in dumped
