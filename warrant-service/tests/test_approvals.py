"""Step-up: pause one branch, ask a human, resume on approval.

Before this, `step_up` was a deny with better wording — the delta was returned
and nothing carried it to a person. These tests cover the loop, and one rule
that matters more than the rest: an approval can lift a step-up and can never
lift a deny.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from conftest import NOW, REGISTRY_DOC
from warrantservice.app import create_app
from warrantservice.approvals import (
    ApprovalError,
    ApprovalService,
    LogDelivery,
    WebhookDelivery,
    summarize,
)
from warrantservice.auth import hash_secret, load as load_creds
from warrantservice.config import Settings, load_registry

from warrant import IntentBinder, SigningKey

AUTH = {"Authorization": "Bearer c.s"}
OPERATOR = "operator@example.test"


@pytest.fixture
def world(tmp_path):
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(json.dumps(REGISTRY_DOC))
    registry, source = load_registry(str(registry_path))
    creds = tmp_path / "credentials.json"
    creds.write_text(json.dumps({"clients": [{
        "client_id": "c", "secret_sha256": hash_secret("s"),
        "scopes": ["decide", "read", "mission:issue", "tree:manage",
                   "tree:revoke", "agent-key:register", "approval:decide"],
    }]}))

    app = create_app(Settings(
        issuer="test.authority", policy_version="p1", registry=registry,
        authority_key=SigningKey.generate("ma-1"), registry_path=source,
        authority_key_supplied=True, authenticator=load_creds(str(creds)),
        approval_ttl_seconds=900,
    ))
    client = TestClient(app)

    class World:
        def __init__(self):
            self.client = client
            self.approvals = app.state.approvals
            self.key = SigningKey.generate("agent-1")
            client.post("/v1/agent-keys", headers=AUTH, json=self.key.verify_key().to_jwk())
            client.post("/v1/missions", headers=AUTH, json={
                "mission_id": "m1", "subject": OPERATOR,
                "instruction": "Settle the March batch.",
                "action_classes": ["svc.read", "svc.update", "svc.transfer"],
                "counterparties": ["known-party"],
                "budget": {"amount_minor": 100_000, "currency": "USD"},
                "not_before": NOW - 100, "not_after": NOW + 3600,
            })
            self.tree = client.post("/v1/trees", headers=AUTH, json={
                "tree_id": "t1", "mission_id": "m1", "root_actor": "agent"}).json()
            self.binder = IntentBinder(self.key, "t1", self.tree["root_id"])

        def decide(self, signed, args):
            return client.post("/v1/decisions", headers=AUTH, json={
                "signed_attestation": {
                    "attestation": signed.attestation.to_payload(),
                    "key_id": signed.key_id, "signature": signed.signature,
                    "algorithm": signed.algorithm,
                },
                "observed_args": args, "node_id": self.tree["root_id"],
                "now": NOW, "token_subject": OPERATOR,
            }).json()

        def new_counterparty_call(self, amount=500):
            args = {"amount": amount}
            return self.binder.attest(action="svc.update", args=args,
                                      counterparty="stranger"), args

    return World()


# --- the loop ---------------------------------------------------------------


def test_a_step_up_creates_a_pending_request_for_the_right_person(world):
    signed, args = world.new_counterparty_call()
    body = world.decide(signed, args)

    assert body["effect"] == "step_up"
    assert "approval.pending" in body["reasons"]

    pending = world.client.get("/v1/approvals", headers=AUTH).json()["approvals"]
    assert len(pending) == 1
    assert pending[0]["approver"] == OPERATOR
    assert pending[0]["status"] == "pending"
    assert pending[0]["counterparty"] == "stranger"
    assert any("stranger" in d for d in pending[0]["delta"])


def test_approving_lets_that_call_through(world):
    signed, args = world.new_counterparty_call()
    assert world.decide(signed, args)["effect"] == "step_up"

    approval_id = world.client.get("/v1/approvals", headers=AUTH).json()["approvals"][0]["approval_id"]
    world.client.post(f"/v1/approvals/{approval_id}/approve", headers=AUTH,
                      json={"note": "spoke to the supplier"})

    after = world.decide(signed, args)
    assert after["effect"] == "allow"
    assert "approval.granted" in after["reasons"]


def test_the_record_still_shows_the_control_that_objected(world):
    """An auditor must see that a human allowed this OVER a control, not that
    no control objected."""
    signed, args = world.new_counterparty_call()
    world.decide(signed, args)
    approval_id = world.client.get("/v1/approvals", headers=AUTH).json()["approvals"][0]["approval_id"]
    world.client.post(f"/v1/approvals/{approval_id}/approve", headers=AUTH, json={})

    after = world.decide(signed, args)
    by_id = {c["check_id"]: c for c in after["checks"]}
    assert by_id["pds.counterparty_scope"]["status"] == "violated"
    assert by_id["approval.granted"]["status"] == "satisfied"
    assert "spoke" not in by_id["approval.granted"]["detail"]  # no note given
    assert "approved this call" in by_id["approval.granted"]["detail"]


def test_the_record_distinguishes_who_approved_from_how(world):
    """Without an IdP a credential stands in for the operator. The answer is
    recorded against the operator with the credential noted beside it, so the
    audit trail never loses that a component pressed the button."""
    signed, args = world.new_counterparty_call()
    world.decide(signed, args)
    approval_id = world.client.get("/v1/approvals", headers=AUTH).json()["approvals"][0]["approval_id"]
    decided = world.client.post(f"/v1/approvals/{approval_id}/approve",
                                headers=AUTH, json={}).json()
    assert decided["decided_by"] == OPERATOR
    assert decided["decided_via"] == "credential:c"


def test_denying_keeps_the_branch_paused(world):
    signed, args = world.new_counterparty_call()
    world.decide(signed, args)
    approval_id = world.client.get("/v1/approvals", headers=AUTH).json()["approvals"][0]["approval_id"]
    world.client.post(f"/v1/approvals/{approval_id}/deny", headers=AUTH,
                      json={"note": "not a supplier we use"})

    after = world.decide(signed, args)
    assert after["effect"] == "step_up"
    # A denied request must not be reusable as cover, and asking again creates
    # a fresh one rather than resurrecting the answered one.
    assert world.client.get(f"/v1/approvals/{approval_id}", headers=AUTH).json()["status"] == "denied"


def test_an_approval_covers_one_call_only(world):
    """Approving a payment authorizes that payment, not that counterparty."""
    first, first_args = world.new_counterparty_call(amount=500)
    world.decide(first, first_args)
    approval_id = world.client.get("/v1/approvals", headers=AUTH).json()["approvals"][0]["approval_id"]
    world.client.post(f"/v1/approvals/{approval_id}/approve", headers=AUTH, json={})

    assert world.decide(first, first_args)["effect"] == "allow"

    # A DIFFERENT amount to the same counterparty is a different call.
    second, second_args = world.new_counterparty_call(amount=99_000)
    assert world.decide(second, second_args)["effect"] == "step_up"


def test_an_approval_is_single_use(world):
    signed, args = world.new_counterparty_call()
    world.decide(signed, args)
    approval_id = world.client.get("/v1/approvals", headers=AUTH).json()["approvals"][0]["approval_id"]
    world.client.post(f"/v1/approvals/{approval_id}/approve", headers=AUTH, json={})

    assert world.decide(signed, args)["effect"] == "allow"
    assert world.decide(signed, args)["effect"] == "step_up"   # spent


def test_retrying_while_a_human_decides_does_not_notify_again(world):
    """Forty messages for one payment is how an approver stops reading them."""
    signed, args = world.new_counterparty_call()
    for _ in range(5):
        world.decide(signed, args)
    assert len(world.client.get("/v1/approvals", headers=AUTH).json()["approvals"]) == 1


# --- the rule that matters --------------------------------------------------


def test_an_approval_can_never_lift_a_deny(world):
    """The absolute one. A forged signature, an injected instruction, a
    sub-agent past its grant: none of those are things a human should be
    offered a button for, because offering it gets it pressed."""
    attested = {"amount": 500}
    signed = world.binder.attest(action="svc.update", args=attested,
                                 counterparty="stranger")
    # Arguments rewritten in flight -> deny, alongside the step-up-worthy
    # counterparty. Precedence puts deny first.
    denied = world.decide(signed, {"amount": 999})
    assert denied["effect"] == "deny"

    # No approval was even created for it.
    assert world.client.get("/v1/approvals", headers=AUTH).json()["approvals"] == []


def test_no_approval_is_created_for_an_allowed_call(world):
    args = {"x": 1}
    signed = world.binder.attest(action="svc.read", args=args)
    assert world.decide(signed, args)["effect"] == "allow"
    assert world.client.get("/v1/approvals", headers=AUTH).json()["approvals"] == []


# --- who may answer ---------------------------------------------------------


def test_only_the_missions_own_operator_may_approve_when_an_idp_is_configured(tmp_path):
    """With an IdP, the approver presents their own token — and the agent
    cannot mint one, so it cannot approve its own request."""
    from test_oidc import AUDIENCE, Issuer, SUBJECT

    issuer = Issuer()
    try:
        registry_path = tmp_path / "r.json"
        registry_path.write_text(json.dumps(REGISTRY_DOC))
        registry, source = load_registry(str(registry_path))
        creds = tmp_path / "c.json"
        creds.write_text(json.dumps({"clients": [{
            "client_id": "c", "secret_sha256": hash_secret("s"),
            "scopes": ["decide", "read", "mission:issue", "tree:manage",
                       "agent-key:register"],
        }]}))
        from warrantservice.oidc import OidcSettings

        app = create_app(Settings(
            issuer="test.authority", policy_version="p1", registry=registry,
            authority_key=SigningKey.generate("ma-1"), registry_path=source,
            authority_key_supplied=True, authenticator=load_creds(str(creds)),
            oidc=OidcSettings(issuer=issuer.url, audience=AUDIENCE,
                              jwks_url=f"{issuer.url}/.well-known/jwks.json"),
        ))
        client = TestClient(app)
        key = SigningKey.generate("agent-1")
        client.post("/v1/agent-keys", headers=AUTH, json=key.verify_key().to_jwk())
        client.post("/v1/missions", headers=AUTH, json={
            "mission_id": "m1", "subject_token": issuer.token(),
            "instruction": "Do it.", "action_classes": ["svc.update"],
            "counterparties": ["known-party"],
            "not_before": NOW - 10, "not_after": NOW + 3600})
        tree = client.post("/v1/trees", headers=AUTH, json={
            "tree_id": "t1", "mission_id": "m1", "root_actor": "a"}).json()

        binder = IntentBinder(key, "t1", tree["root_id"])
        args = {"amount": 1}
        signed = binder.attest(action="svc.update", args=args, counterparty="stranger")
        body = client.post("/v1/decisions", headers=AUTH, json={
            "signed_attestation": {
                "attestation": signed.attestation.to_payload(), "key_id": signed.key_id,
                "signature": signed.signature, "algorithm": signed.algorithm},
            "observed_args": args, "node_id": tree["root_id"], "now": NOW,
            "subject_token": issuer.token()}).json()
        assert body["effect"] == "step_up"

        approval_id = client.get("/v1/approvals", headers=AUTH).json()["approvals"][0]["approval_id"]

        # Somebody else's genuine token: refused.
        other = client.post(f"/v1/approvals/{approval_id}/approve", headers=AUTH,
                            json={"subject_token": issuer.token(sub="mallory@example.test")})
        assert other.status_code == 403

        # No token at all: refused, not silently accepted.
        assert client.post(f"/v1/approvals/{approval_id}/approve", headers=AUTH,
                           json={}).status_code == 401

        # The right person.
        assert client.post(f"/v1/approvals/{approval_id}/approve", headers=AUTH,
                           json={"subject_token": issuer.token()}).status_code == 200
    finally:
        issuer.stop()


def test_answering_twice_is_refused(world):
    signed, args = world.new_counterparty_call()
    world.decide(signed, args)
    approval_id = world.client.get("/v1/approvals", headers=AUTH).json()["approvals"][0]["approval_id"]
    assert world.client.post(f"/v1/approvals/{approval_id}/approve", headers=AUTH,
                             json={}).status_code == 200
    second = world.client.post(f"/v1/approvals/{approval_id}/deny", headers=AUTH, json={})
    assert second.status_code == 409
    assert "already approved" in second.json()["detail"]


# --- delivery ---------------------------------------------------------------


def test_delivery_happens_off_the_call_path(world):
    """A decision must not wait on Slack. A channel that blocks for a second
    must not add a second to a 2ms budget."""
    import time

    class Slow(LogDelivery):
        name = "slow"

        def send(self, approval, link):
            time.sleep(0.75)
            return "slow"

    world.approvals.channels = [Slow(lambda s: None)]
    signed, args = world.new_counterparty_call()
    started = time.time()
    world.decide(signed, args)
    assert time.time() - started < 0.4


def test_a_failing_channel_loses_no_approval(world):
    """The record is stored before anything is sent, so a dead webhook costs a
    notification, never an approval."""
    class Broken(LogDelivery):
        name = "broken"

        def send(self, approval, link):
            raise RuntimeError("no route to host")

    world.approvals.channels = [Broken(lambda s: None)]
    world.approvals.deliver_async = False
    signed, args = world.new_counterparty_call()
    world.decide(signed, args)

    pending = world.client.get("/v1/approvals", headers=AUTH).json()["approvals"]
    assert len(pending) == 1
    assert any("failed" in note for note in pending[0]["delivery"])


def test_a_webhook_records_a_bad_status_rather_than_raising():
    service = ApprovalService(channels=[WebhookDelivery("http://127.0.0.1:1/hook")],
                              deliver_async=False)
    approval = service.request(tree_id="t", node_id="n", mission_id="m",
                               approver="op", action="a", args_hash="h",
                               delta=["x"], reasons=["r"])
    assert any("failed" in note for note in service.get(approval.approval_id).delivery)


def test_the_message_says_what_changed_and_under_whose_instruction():
    """A notification reading 'approval required for tree-7f3a node 2' trains
    people to approve without reading."""
    service = ApprovalService(deliver_async=False)
    approval = service.request(
        tree_id="t", node_id="n", mission_id="m", approver="op@x",
        action="ap.payment.release", args_hash="h",
        delta=["counterparty BRIGHTWATER is not one the user approved for this task"],
        reasons=["pds.counterparty_scope"], counterparty="BRIGHTWATER",
        amount_minor=2_390_000, currency="USD")
    text = summarize(approval, "https://warrant.example/v1/approvals/x")
    assert "BRIGHTWATER" in text
    assert "23,900.00 USD" in text
    assert "What changed" in text
    assert "https://warrant.example" in text


# --- expiry -----------------------------------------------------------------


def test_an_unanswered_request_expires():
    """A pending request that lived forever would become a permission somebody
    granted months ago and forgot."""
    service = ApprovalService(ttl_seconds=60, deliver_async=False)
    approval = service.request(tree_id="t", node_id="n", mission_id="m",
                               approver="op", action="a", args_hash="h",
                               delta=[], reasons=[], now=1000)
    assert service.get(approval.approval_id, now=1030).status == "pending"
    assert service.get(approval.approval_id, now=1100).status == "expired"
    assert service.find_for_call("t", "n", "a", "h", now=1100) is None

    with pytest.raises(ApprovalError, match="expired"):
        service.decide(approval.approval_id, approved=True, by="op", now=1100)


def test_an_expired_request_can_be_asked_again():
    service = ApprovalService(ttl_seconds=60, deliver_async=False)
    first = service.request(tree_id="t", node_id="n", mission_id="m", approver="op",
                            action="a", args_hash="h", delta=[], reasons=[], now=1000)
    second = service.request(tree_id="t", node_id="n", mission_id="m", approver="op",
                             action="a", args_hash="h", delta=[], reasons=[], now=2000)
    assert second.approval_id != first.approval_id
    assert second.status == "pending"
