"""The end user's identity, verified against a real issuer.

Before this, the subject was a string in the request body and
`pds._check_subject` compared it to the Mission's — a control that looked like
identity and was not. These tests run a real OIDC issuer (an RSA key, a JWKS
endpoint on a real port) and check both directions: that a genuine token
resolves to its subject, and that the ways a token can be wrong are each
refused rather than any of them slipping through.

A local issuer rather than a mocked verifier, because the failures worth
catching — algorithm confusion, a key from the wrong issuer, an audience for a
different application — all live in the verification path a mock replaces.
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from conftest import NOW, REGISTRY_DOC
from warrantservice.app import create_app
from warrantservice.auth import hash_secret, load as load_creds
from warrantservice.config import Settings, load_registry
from warrantservice.oidc import (
    OidcConfigError,
    OidcSettings,
    SubjectError,
    SubjectVerifier,
)

from warrant import IntentBinder, SigningKey

AUDIENCE = "warrant-pds"
SUBJECT = "t.okafor@arcadia.example"


class Issuer:
    """A minimal OIDC issuer: one RSA key, a JWKS endpoint, a token minter."""

    def __init__(self) -> None:
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.kid = "issuer-key-1"
        jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self.key.public_key()))
        jwk.update({"kid": self.kid, "use": "sig", "alg": "RS256"})
        self.jwks = {"keys": [jwk]}

        issuer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                body = json.dumps(issuer.jwks).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self._server.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}"
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def token(self, *, sub=SUBJECT, aud=AUDIENCE, iss=None, expires_in=3600,
              alg="RS256", key=None, kid=None) -> str:
        now = int(time.time())
        claims = {"sub": sub, "iss": iss if iss is not None else self.url,
                  "iat": now, "exp": now + expires_in}
        if aud is not None:
            claims["aud"] = aud
        return jwt.encode(claims, key or self.key, algorithm=alg,
                          headers={"kid": kid or self.kid})

    def stop(self):
        self._server.shutdown()


@pytest.fixture(scope="module")
def issuer():
    i = Issuer()
    yield i
    i.stop()


@pytest.fixture
def verifier(issuer):
    return SubjectVerifier(OidcSettings(
        issuer=issuer.url, audience=AUDIENCE, jwks_url=f"{issuer.url}/.well-known/jwks.json"
    ))


# --- verification -----------------------------------------------------------


def test_a_genuine_token_resolves_to_its_subject(verifier, issuer):
    assert verifier.subject_of(issuer.token()) == SUBJECT


def test_an_expired_token_is_refused(verifier, issuer):
    with pytest.raises(SubjectError, match="expired"):
        verifier.subject_of(issuer.token(expires_in=-120))


def test_a_token_for_another_application_is_refused(verifier, issuer):
    """A token minted for a different audience at the SAME issuer is a valid
    token; without this check it would also be a valid subject here."""
    with pytest.raises(SubjectError, match="different audience"):
        verifier.subject_of(issuer.token(aud="some-other-app"))


def test_a_token_from_another_issuer_is_refused(verifier, issuer):
    with pytest.raises(SubjectError, match="not issued by"):
        verifier.subject_of(issuer.token(iss="https://evil.test"))


def test_a_token_signed_by_the_wrong_key_is_refused(verifier, issuer):
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(SubjectError):
        verifier.subject_of(issuer.token(key=other))


def test_an_unsigned_token_is_refused(verifier, issuer):
    """`alg: none` — the oldest JWT attack there is."""
    forged = jwt.encode({"sub": "attacker", "iss": issuer.url, "aud": AUDIENCE,
                         "exp": int(time.time()) + 600}, key="", algorithm="none")
    with pytest.raises(SubjectError):
        verifier.subject_of(forged)


def test_a_token_naming_an_unknown_key_is_refused(verifier, issuer):
    with pytest.raises(SubjectError, match="could not find a key"):
        verifier.subject_of(issuer.token(kid="no-such-key"))


def test_a_token_with_no_subject_is_refused(verifier, issuer):
    with pytest.raises(SubjectError):
        verifier.subject_of(issuer.token(sub=None))


def test_garbage_is_refused(verifier):
    for value in ("", "not-a-token", "a.b.c"):
        with pytest.raises(SubjectError):
            verifier.subject_of(value)


def test_symmetric_algorithms_are_refused_at_configuration_time():
    """With HMAC the verification key IS the signing key, so anything able to
    verify could also mint. Caught at startup rather than per token."""
    with pytest.raises(OidcConfigError, match="symmetric"):
        SubjectVerifier(OidcSettings(issuer="https://x.test", algorithms=("HS256",)))


def test_a_rotated_key_is_picked_up_without_a_restart(issuer):
    """A token naming an unseen kid forces a re-fetch, so an IdP rotating keys
    does not take the estate down.

    Built with a zero cooldown because the refresh is rate limited in real
    deployments — see the test below, which pins that the limit is genuinely
    enforced rather than decorative."""
    verifier = SubjectVerifier(OidcSettings(
        issuer=issuer.url, audience=AUDIENCE,
        jwks_url=f"{issuer.url}/.well-known/jwks.json",
        refresh_cooldown_seconds=0,
    ))
    assert verifier.subject_of(issuer.token()) == SUBJECT

    new_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(new_key.public_key()))
    jwk.update({"kid": "issuer-key-2", "use": "sig", "alg": "RS256"})
    issuer.jwks = {"keys": issuer.jwks["keys"] + [jwk]}

    assert verifier.subject_of(
        issuer.token(key=new_key, kid="issuer-key-2")
    ) == SUBJECT


def test_the_refresh_of_unknown_keys_is_rate_limited(verifier, issuer):
    """Without a cooldown, a flood of tokens naming random kids becomes a flood
    of requests to the IdP. The cost is that a rotation faster than the window
    is refused until it elapses — a trade worth making, and worth pinning so a
    future change to the default is a deliberate one."""
    verifier.subject_of(issuer.token())  # warms the cache, starts the cooldown

    new_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(new_key.public_key()))
    jwk.update({"kid": "issuer-key-3", "use": "sig", "alg": "RS256"})
    issuer.jwks = {"keys": issuer.jwks["keys"] + [jwk]}

    with pytest.raises(SubjectError, match="could not find a key"):
        verifier.subject_of(issuer.token(key=new_key, kid="issuer-key-3"))


# --- through the service ----------------------------------------------------


@pytest.fixture
def secured(tmp_path, issuer):
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(json.dumps(REGISTRY_DOC))
    registry, source = load_registry(str(registry_path))

    creds = tmp_path / "credentials.json"
    creds.write_text(json.dumps({"clients": [{
        "client_id": "c", "secret_sha256": hash_secret("s"),
        "scopes": ["decide", "read", "mission:issue", "tree:manage",
                   "tree:revoke", "agent-key:register"],
    }]}))

    return TestClient(create_app(Settings(
        issuer="test.authority", policy_version="p1", registry=registry,
        authority_key=SigningKey.generate("ma-1"), registry_path=source,
        authority_key_supplied=True, authenticator=load_creds(str(creds)),
        oidc=OidcSettings(issuer=issuer.url, audience=AUDIENCE,
                          jwks_url=f"{issuer.url}/.well-known/jwks.json"),
    )))


AUTH = {"Authorization": "Bearer c.s"}


def test_consent_is_minted_for_the_verified_user_not_a_typed_name(secured, issuer):
    body = secured.post("/v1/missions", headers=AUTH, json={
        "mission_id": "m1", "subject_token": issuer.token(),
        "instruction": "Settle the March batch.",
    }).json()
    assert body["mission"]["subject"] == SUBJECT


def test_a_typed_subject_is_refused_when_an_idp_is_configured(secured):
    """The two paths are mutually exclusive. Leaving the unverified one open
    beside the verified one means the weakest link is still there."""
    response = secured.post("/v1/missions", headers=AUTH, json={
        "mission_id": "m1", "subject": "anyone-i-like@example.test",
        "instruction": "do it",
    })
    assert response.status_code == 400
    assert "identity provider" in response.json()["detail"]


def test_a_decision_takes_its_subject_from_the_verified_token(secured, issuer):
    key = SigningKey.generate("agent-1")
    secured.post("/v1/agent-keys", headers=AUTH, json=key.verify_key().to_jwk())
    secured.post("/v1/missions", headers=AUTH, json={
        "mission_id": "m1", "subject_token": issuer.token(),
        "instruction": "Settle the March batch.",
        "action_classes": ["svc.read"], "not_before": NOW - 10, "not_after": NOW + 3600,
    })
    tree = secured.post("/v1/trees", headers=AUTH, json={
        "tree_id": "t1", "mission_id": "m1", "root_actor": "agent"}).json()

    binder = IntentBinder(key, "t1", tree["root_id"])
    args = {"x": 1}
    signed = binder.attest(action="svc.read", args=args)

    def decide(**extra):
        return secured.post("/v1/decisions", headers=AUTH, json={
            "signed_attestation": {
                "attestation": signed.attestation.to_payload(), "key_id": signed.key_id,
                "signature": signed.signature, "algorithm": signed.algorithm,
            },
            "observed_args": args, "node_id": tree["root_id"], "now": NOW, **extra,
        })

    assert decide(subject_token=issuer.token()).json()["effect"] == "allow"

    # Another user's genuine token: correctly denied on subject binding.
    other = decide(subject_token=issuer.token(sub="someone.else@arcadia.example")).json()
    assert other["effect"] == "deny"
    assert "pds.subject_binding" in other["reasons"]

    # The old unverified path is closed.
    assert decide(token_subject=SUBJECT).status_code == 400


def test_a_bad_subject_token_is_a_401_not_a_400(secured):
    """The request was well formed; the identity was not established. A
    gateway retrying a 400 would retry forever."""
    response = secured.post("/v1/decisions", headers=AUTH, json={
        "signed_attestation": {"attestation": {
            "tree_id": "t", "node_id": "n", "action": "svc.read", "args_hash": "h"},
            "key_id": "k", "signature": "s"},
        "observed_args": {}, "node_id": "n", "now": NOW,
        "subject_token": "not-a-real-token",
    })
    assert response.status_code == 401
    assert response.json()["error"] == "subject_not_verified"


def test_an_unverifiable_token_is_refused_when_no_idp_is_configured(client):
    """Accepting a token nobody checked would be worse than refusing it."""
    response = client.post("/v1/missions", json={
        "mission_id": "m1", "subject_token": "whatever", "instruction": "do it",
    })
    assert response.status_code == 400
    assert "cannot be verified" in response.json()["detail"]
