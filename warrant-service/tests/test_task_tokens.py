"""Task-tree tokens, and the escalation they close.

The gap: an attestation is signed by a registered agent key and NAMES its node.
The signature binds the arguments honestly, but node identity was self-asserted
— so agent B, holding a perfectly valid key, could attest as a node belonging
to agent A and have every downstream control evaluate it against A's grant.

A token says, signed by the control zone, which key holds which node. These
tests cover the four properties the spec asks for by name: one tree_id across a
task, downstream tokens only by exchange, a capped depth, and proof-of-
possession binding so a stolen token is useless.
"""

from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

from conftest import NOW, REGISTRY_DOC
from warrantservice.app import create_app
from warrantservice.auth import hash_secret, load as load_creds
from warrantservice.config import Settings, load_registry
from warrantservice.dpop import (
    DpopError,
    ProofVerifier,
    key_thumbprint,
    make_proof,
    thumbprint,
)
from warrantservice.tokens import TokenError

from warrant import IntentBinder, SigningKey

AUTH = {"Authorization": "Bearer c.s"}


def _app(tmp_path, *, required=True):
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(json.dumps(REGISTRY_DOC))
    registry, source = load_registry(str(registry_path))
    creds = tmp_path / "credentials.json"
    creds.write_text(json.dumps({"clients": [{
        "client_id": "c", "secret_sha256": hash_secret("s"),
        "scopes": ["decide", "read", "mission:issue", "tree:manage",
                   "tree:revoke", "agent-key:register", "token:issue"],
    }]}))
    return create_app(Settings(
        issuer="test.authority", policy_version="p1", registry=registry,
        authority_key=SigningKey.generate("ma-1"), registry_path=source,
        authority_key_supplied=True, authenticator=load_creds(str(creds)),
        task_tokens_required=required, token_key=SigningKey.generate("tok-1"),
        token_key_supplied=True,
    ))


@pytest.fixture
def world(tmp_path):
    client = TestClient(_app(tmp_path))

    class World:
        def __init__(self):
            self.client = client
            self.agent_key = SigningKey.generate("agent-1")
            client.post("/v1/agent-keys", headers=AUTH,
                        json=self.agent_key.verify_key().to_jwk())
            client.post("/v1/missions", headers=AUTH, json={
                "mission_id": "m1", "subject": "operator@example.test",
                "instruction": "Run the batch.",
                "action_classes": ["svc.read", "svc.update", "svc.transfer"],
                "counterparties": ["known-party"],
                "budget": {"amount_minor": 100_000, "currency": "USD"},
                "not_before": NOW - 100, "not_after": NOW + 3600, "max_depth": 1,
            })
            self.tree = client.post("/v1/trees", headers=AUTH, json={
                "tree_id": "t1", "mission_id": "m1", "root_actor": "root-agent",
            }).json()
            self.root = client.post("/v1/token", headers=AUTH, json={
                "tree_id": "t1", "jkt": key_thumbprint(self.agent_key),
            }).json()
            self.binder = IntentBinder(self.agent_key, "t1", self.tree["root_id"])

        def decide(self, signed, args, token=None, key=None, url=None):
            raw = token if token is not None else self.root["task_token"]
            k = key or self.agent_key
            u = url or "https://tools.example/call"
            return client.post("/v1/decisions", headers=AUTH, json={
                "signed_attestation": {
                    "attestation": signed.attestation.to_payload(),
                    "key_id": signed.key_id, "signature": signed.signature,
                    "algorithm": signed.algorithm,
                },
                "observed_args": args, "now": NOW,
                "task_token": raw,
                "dpop_proof": make_proof(k, method="POST", url=u, access_token=raw),
                "htm": "POST", "htu": u,
            })

    return World()


# --- the escalation this closes ---------------------------------------------


def test_a_valid_agent_key_cannot_act_as_a_node_it_was_not_issued(world):
    """The whole point. Agent B holds a registered key and a genuine token for
    its OWN node, and attests as the root's node."""
    child = world.client.post("/v1/token/exchange", json={
        "task_token": world.root["task_token"],
        "dpop_proof": make_proof(
            world.agent_key, method="POST",
            url="http://testserver/v1/token/exchange",
            access_token=world.root["task_token"]),
        "actor": "helper", "grant": {}, "jkt": key_thumbprint(world.agent_key),
    }).json()

    # The helper signs an attestation naming the ROOT's node.
    impostor = IntentBinder(world.agent_key, "t1", world.tree["root_id"])
    args = {"x": 1}
    body = world.decide(impostor.attest(action="svc.read", args=args), args,
                        token=child["task_token"]).json()

    assert body["effect"] == "deny"
    assert body["reasons"] == ["token.node_binding"]


def test_the_matching_node_is_allowed(world):
    args = {"x": 1}
    signed = world.binder.attest(action="svc.read", args=args)
    assert world.decide(signed, args).json()["effect"] == "allow"


# --- proof of possession: a stolen token is useless -------------------------


def test_a_stolen_token_without_its_key_is_useless(world):
    """The property that makes a token worth more than a bearer string."""
    thief = SigningKey.generate("thief")
    args = {"x": 1}
    signed = world.binder.attest(action="svc.read", args=args)

    response = world.decide(signed, args, key=thief)
    assert response.status_code == 401
    assert response.json()["error"] == "proof_not_valid"


def test_a_token_presented_with_no_proof_is_refused(world):
    args = {"x": 1}
    signed = world.binder.attest(action="svc.read", args=args)
    response = world.client.post("/v1/decisions", headers=AUTH, json={
        "signed_attestation": {
            "attestation": signed.attestation.to_payload(),
            "key_id": signed.key_id, "signature": signed.signature,
            "algorithm": signed.algorithm,
        },
        "observed_args": args, "now": NOW,
        "task_token": world.root["task_token"], "dpop_proof": "",
        "htm": "POST", "htu": "https://tools.example/call",
    })
    assert response.status_code == 401


def test_a_proof_cannot_be_replayed(world):
    """Single use within its acceptance window."""
    args = {"x": 1}
    signed = world.binder.attest(action="svc.read", args=args)
    raw = world.root["task_token"]
    url = "https://tools.example/call"
    proof = make_proof(world.agent_key, method="POST", url=url, access_token=raw)

    def send():
        return world.client.post("/v1/decisions", headers=AUTH, json={
            "signed_attestation": {
                "attestation": signed.attestation.to_payload(),
                "key_id": signed.key_id, "signature": signed.signature,
                "algorithm": signed.algorithm,
            },
            "observed_args": args, "now": NOW, "task_token": raw,
            "dpop_proof": proof, "htm": "POST", "htu": url,
        })

    assert send().json()["effect"] == "allow"
    assert send().status_code == 401


def test_a_proof_for_one_endpoint_cannot_be_used_at_another(world):
    """So a proof captured at a harmless endpoint cannot be presented at a
    dangerous one."""
    args = {"x": 1}
    signed = world.binder.attest(action="svc.read", args=args)
    raw = world.root["task_token"]
    proof = make_proof(world.agent_key, method="POST",
                       url="https://tools.example/harmless", access_token=raw)

    response = world.client.post("/v1/decisions", headers=AUTH, json={
        "signed_attestation": {
            "attestation": signed.attestation.to_payload(),
            "key_id": signed.key_id, "signature": signed.signature,
            "algorithm": signed.algorithm,
        },
        "observed_args": args, "now": NOW, "task_token": raw,
        "dpop_proof": proof, "htm": "POST", "htu": "https://tools.example/dangerous",
    })
    assert response.status_code == 401
    assert "made for" in response.json()["detail"]


def test_a_proof_bound_to_another_token_is_refused(world):
    """`ath` binds a proof to one specific token, so the two must have
    travelled together."""
    other = world.client.post("/v1/token", headers=AUTH, json={
        "tree_id": "t1", "jkt": key_thumbprint(world.agent_key)}).json()
    args = {"x": 1}
    signed = world.binder.attest(action="svc.read", args=args)
    url = "https://tools.example/call"
    proof = make_proof(world.agent_key, method="POST", url=url,
                       access_token=other["task_token"])

    response = world.client.post("/v1/decisions", headers=AUTH, json={
        "signed_attestation": {
            "attestation": signed.attestation.to_payload(),
            "key_id": signed.key_id, "signature": signed.signature,
            "algorithm": signed.algorithm,
        },
        "observed_args": args, "now": NOW, "task_token": world.root["task_token"],
        "dpop_proof": proof, "htm": "POST", "htu": url,
    })
    assert response.status_code == 401
    assert "ath" in response.json()["detail"]


# --- exchange is the only path downstream -----------------------------------


def test_an_exchanged_token_is_narrower_and_one_level_deeper(world):
    child = world.client.post("/v1/token/exchange", json={
        "task_token": world.root["task_token"],
        "dpop_proof": make_proof(world.agent_key, method="POST",
                                 url="http://testserver/v1/token/exchange",
                                 access_token=world.root["task_token"]),
        "actor": "helper",
        "grant": {"action_classes": ["svc.read", "svc.delete"]},
        "jkt": key_thumbprint(world.agent_key),
    }).json()

    assert child["depth"] == 1
    assert child["actor_chain"] == ["root-agent", "helper"]
    # `svc.delete` was asked for and is not the parent's: dropped, not added.
    assert child["grant"]["action_classes"] == ["svc.read"]


def test_exchange_cannot_exceed_the_missions_depth_cap(world):
    def exchange(parent):
        return world.client.post("/v1/token/exchange", json={
            "task_token": parent,
            "dpop_proof": make_proof(world.agent_key, method="POST",
                                     url="http://testserver/v1/token/exchange",
                                     access_token=parent),
            "actor": "deeper", "grant": {}, "jkt": key_thumbprint(world.agent_key),
        })

    child = exchange(world.root["task_token"]).json()
    refused = exchange(child["task_token"])
    # 409, not 401: the token was valid, the request exceeded the Mission's
    # delegation depth. Telling an agent its good token is invalid sends it to
    # re-authenticate, which will not help.
    assert refused.status_code == 409
    assert refused.json()["error"] == "exchange_refused"
    assert "max_depth" in refused.json()["detail"]


def test_exchange_needs_the_parent_tokens_key(world):
    """Presenting someone else's token fails at the proof, which is why this
    route needs no deployment credential."""
    thief = SigningKey.generate("thief")
    response = world.client.post("/v1/token/exchange", json={
        "task_token": world.root["task_token"],
        "dpop_proof": make_proof(thief, method="POST",
                                 url="http://testserver/v1/token/exchange",
                                 access_token=world.root["task_token"]),
        "actor": "helper", "grant": {}, "jkt": key_thumbprint(thief),
    })
    assert response.status_code == 401


def test_every_token_in_a_task_carries_the_same_tree_id(world):
    import jwt as _jwt

    child = world.client.post("/v1/token/exchange", json={
        "task_token": world.root["task_token"],
        "dpop_proof": make_proof(world.agent_key, method="POST",
                                 url="http://testserver/v1/token/exchange",
                                 access_token=world.root["task_token"]),
        "actor": "helper", "grant": {}, "jkt": key_thumbprint(world.agent_key),
    }).json()

    for raw in (world.root["task_token"], child["task_token"]):
        claims = _jwt.decode(raw, options={"verify_signature": False})
        assert claims["tree_id"] == "t1"
        assert claims["mission"] == "m1"
        assert claims["cnf"]["jkt"] == key_thumbprint(world.agent_key)


def test_a_revoked_tree_issues_no_further_tokens(world):
    world.client.post("/v1/trees/t1/revoke", headers=AUTH, json={"reason": "stop"})
    response = world.client.post("/v1/token/exchange", json={
        "task_token": world.root["task_token"],
        "dpop_proof": make_proof(world.agent_key, method="POST",
                                 url="http://testserver/v1/token/exchange",
                                 access_token=world.root["task_token"]),
        "actor": "helper", "grant": {}, "jkt": key_thumbprint(world.agent_key),
    })
    assert response.status_code == 409
    assert "revoked" in response.json()["detail"]


# --- the weak path is closed ------------------------------------------------


def test_the_self_asserted_node_path_is_refused_when_tokens_are_required(world):
    args = {"x": 1}
    signed = world.binder.attest(action="svc.read", args=args)
    response = world.client.post("/v1/decisions", headers=AUTH, json={
        "signed_attestation": {
            "attestation": signed.attestation.to_payload(),
            "key_id": signed.key_id, "signature": signed.signature,
            "algorithm": signed.algorithm,
        },
        "observed_args": args, "now": NOW,
        "node_id": world.tree["root_id"], "token_subject": "operator@example.test",
    })
    assert response.status_code == 400
    assert "not accepted" in response.json()["detail"]


def test_a_token_sent_to_a_deployment_that_does_not_require_them_is_refused(tmp_path):
    """Accepting a token nobody enforces would be worse than refusing it."""
    client = TestClient(_app(tmp_path, required=False))
    response = client.post("/v1/decisions", headers=AUTH, json={
        "signed_attestation": {"attestation": {
            "tree_id": "t", "node_id": "n", "action": "svc.read", "args_hash": "h"},
            "key_id": "k", "signature": "s"},
        "observed_args": {}, "now": NOW, "task_token": "whatever",
    })
    assert response.status_code == 400
    assert "would not be enforced" in response.json()["detail"]


# --- the proof format itself ------------------------------------------------


def test_a_proof_must_declare_its_type(world):
    """`typ: dpop+jwt` is what stops a token being replayed as a proof."""
    verifier = ProofVerifier()
    raw = make_proof(world.agent_key, method="POST", url="https://x.test")
    import base64

    head, rest = raw.split(".", 1)
    pad = head + "=" * (-len(head) % 4)
    header = json.loads(base64.urlsafe_b64decode(pad))
    assert header["typ"] == "dpop+jwt"

    header["typ"] = "JWT"
    swapped = base64.urlsafe_b64encode(
        json.dumps(header).encode()).decode().rstrip("=") + "." + rest
    with pytest.raises(DpopError, match="typ"):
        verifier.verify(swapped, method="POST", url="https://x.test")


def test_a_proof_embedding_private_key_material_is_refused():
    """Either a catastrophic client bug or an attempt to confuse the verifier."""
    key = SigningKey.generate("k")
    verifier = ProofVerifier()
    raw = make_proof(key, method="POST", url="https://x.test")
    import base64

    head, rest = raw.split(".", 1)
    pad = head + "=" * (-len(head) % 4)
    header = json.loads(base64.urlsafe_b64decode(pad))
    header["jwk"]["d"] = "private"
    tampered = base64.urlsafe_b64encode(
        json.dumps(header).encode()).decode().rstrip("=") + "." + rest
    with pytest.raises(DpopError, match="private key material"):
        verifier.verify(tampered, method="POST", url="https://x.test")


def test_a_stale_proof_is_refused():
    key = SigningKey.generate("k")
    verifier = ProofVerifier(acceptance_window_seconds=30)
    old = make_proof(key, method="POST", url="https://x.test",
                     now=int(time.time()) - 600)
    with pytest.raises(DpopError, match="acceptance window"):
        verifier.verify(old, method="POST", url="https://x.test")


def test_the_replay_cache_forgets_outside_the_window():
    """Bounded by time rather than by count: an LRU evicting early would let a
    proof be replayed exactly when the system was busiest."""
    from warrantservice.dpop import ReplayCache

    cache = ReplayCache(window_seconds=10)
    cache.check_and_record("a", now=1000)
    with pytest.raises(DpopError):
        cache.check_and_record("a", now=1005)
    cache.check_and_record("a", now=1100)   # window has passed
    assert len(cache) == 1


def test_a_token_without_a_key_binding_is_refused():
    """Refusing to treat an unbound token as a bearer token."""
    from warrantservice.tokens import TokenService
    import jwt as _jwt

    service = TokenService("iss", SigningKey.generate("tok"))
    forged = _jwt.encode(
        {"iss": "iss", "aud": "warrant-pds", "sub": "u", "exp": int(time.time()) + 600,
         "jti": "1", "tree_id": "t", "node_id": "n"},
        service._key.private_key, algorithm="EdDSA",
        headers={"typ": "warrant-task+jwt"},
    )
    with pytest.raises(TokenError, match="cnf.jkt"):
        service.verify(forged)


def test_a_subject_token_cannot_be_presented_as_a_task_token():
    """The `typ` header separates them."""
    from warrantservice.tokens import TokenService
    import jwt as _jwt

    service = TokenService("iss", SigningKey.generate("tok"))
    wrong_type = _jwt.encode(
        {"iss": "iss", "aud": "warrant-pds", "sub": "u", "exp": int(time.time()) + 600,
         "jti": "1", "tree_id": "t", "node_id": "n", "cnf": {"jkt": "x"}},
        service._key.private_key, algorithm="EdDSA", headers={"typ": "JWT"},
    )
    with pytest.raises(TokenError, match="typ"):
        service.verify(wrong_type)
