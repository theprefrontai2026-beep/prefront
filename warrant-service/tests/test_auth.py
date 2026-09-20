"""Who may call the service at all.

The hole this closes was demonstrable in four unauthenticated calls: register
your own signing key, mint a Mission naming any subject with any budget, open a
tree, collect an `allow`. Every individual control worked; the Mission
Authority, the one component that can WIDEN permissions, simply had an open
door in front of it.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from conftest import NOW, REGISTRY_DOC
from warrantservice.app import create_app
from warrantservice.auth import (
    Authenticator,
    AuthConfigError,
    hash_secret,
    load,
    mint_secret,
)
from warrantservice.config import ConfigError, Settings, from_env, load_registry

from warrant import SigningKey

GATEWAY_SECRET = "gateway-secret-value"
CONTROL_SECRET = "control-secret-value"

CREDENTIALS = {
    "clients": [
        {"client_id": "gateway", "secret_sha256": hash_secret(GATEWAY_SECRET),
         "scopes": ["decide", "read"]},
        {"client_id": "control", "secret_sha256": hash_secret(CONTROL_SECRET),
         "scopes": ["mission:issue", "tree:manage", "tree:revoke",
                    "agent-key:register", "read", "decide"]},
    ]
}


@pytest.fixture
def creds_file(tmp_path):
    path = tmp_path / "credentials.json"
    path.write_text(json.dumps(CREDENTIALS))
    return path


@pytest.fixture
def secured(tmp_path, creds_file):
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(json.dumps(REGISTRY_DOC))
    registry, source = load_registry(str(registry_path))
    return TestClient(create_app(Settings(
        issuer="test.authority", policy_version="p1", registry=registry,
        authority_key=SigningKey.generate("ma-1"), registry_path=source,
        authority_key_supplied=True, authenticator=load(str(creds_file)),
    )))


def auth(secret_holder: str) -> dict:
    return {"Authorization": f"Bearer {secret_holder}"}


GATEWAY = auth(f"gateway.{GATEWAY_SECRET}")
CONTROL = auth(f"control.{CONTROL_SECRET}")


# --- the door is shut -------------------------------------------------------


def test_the_demonstrated_self_authorization_chain_is_closed(secured):
    """The exact four calls that worked before, in order."""
    key = SigningKey.generate("attacker-key")
    assert secured.post("/v1/agent-keys", json=key.verify_key().to_jwk()).status_code == 401
    assert secured.post("/v1/missions", json={
        "mission_id": "x", "subject": "anyone", "instruction": "whatever",
    }).status_code == 401
    assert secured.post("/v1/trees", json={
        "tree_id": "x", "mission_id": "x", "root_actor": "me",
    }).status_code == 401
    assert secured.post("/v1/decisions", json={}).status_code == 401


def test_a_401_says_what_to_present(secured):
    response = secured.post("/v1/missions", json={})
    assert response.status_code == 401
    assert response.headers.get("WWW-Authenticate") == "Bearer"
    assert response.json()["error"] == "unauthenticated"


@pytest.mark.parametrize("header", [
    {"Authorization": "gateway.x"},                    # no scheme
    {"Authorization": "Basic gateway.x"},              # wrong scheme
    {"Authorization": "Bearer "},                      # empty
    {"Authorization": "Bearer gateway"},               # no secret
    {"Authorization": "Bearer gateway.wrong-secret"},  # wrong secret
    {"Authorization": "Bearer unknown.whatever"},      # unknown client
])
def test_a_malformed_or_wrong_credential_is_refused(secured, header):
    assert secured.get("/v1/registry", headers=header).status_code == 401


# --- scopes -----------------------------------------------------------------


def test_the_gateway_can_decide_but_cannot_mint_consent(secured):
    """The separation that matters. A gateway sits on every tool call; if its
    credential could also issue Missions, compromising it would be the same
    hole with a password on it."""
    assert secured.get("/v1/registry", headers=GATEWAY).status_code == 200

    response = secured.post("/v1/missions", headers=GATEWAY, json={
        "mission_id": "m1", "subject": "s", "instruction": "do it",
    })
    assert response.status_code == 403
    assert response.json()["error"] == "forbidden"
    assert "mission:issue" in response.json()["detail"]


def test_the_control_plane_can_mint_consent(secured):
    assert secured.post("/v1/missions", headers=CONTROL, json={
        "mission_id": "m1", "subject": "s", "instruction": "do it",
    }).status_code == 200


def test_pressing_stop_needs_its_own_scope(secured):
    """Revocation is safety-critical, so it is separated in BOTH directions: a
    gateway should not be able to halt every task in the estate, and a
    credential that can halt tasks need not also be able to mint them."""
    assert secured.post("/v1/trees/t/revoke", headers=GATEWAY,
                        json={"reason": "x"}).status_code == 403
    assert secured.post("/v1/trees/t/revoke", headers=CONTROL,
                        json={"reason": "x"}).status_code == 200


# --- what stays public ------------------------------------------------------


def test_the_jwks_stays_public(secured):
    """It must. The whole promise is that a resource server we do not run — in
    another organisation, possibly air-gapped — can verify a Mission without
    asking us anything."""
    assert secured.get("/.well-known/jwks.json").status_code == 200


def test_health_stays_public_and_reports_the_posture(secured):
    body = secured.get("/healthz").json()
    assert body["caller_auth"] == "required"
    assert body["subject_identity"].startswith("UNVERIFIED")


def test_an_open_deployment_says_so_in_its_health(tmp_path):
    app = create_app(from_env({"WARRANT_ALLOW_UNAUTHENTICATED": "1"}))
    assert TestClient(app).get("/healthz").json()["caller_auth"] == "DISABLED"


# --- configuration ----------------------------------------------------------


def test_the_service_refuses_to_start_unauthenticated_by_accident():
    """Not a degraded mode — an open door in front of the component that mints
    consent. Reaching it has to be a deliberate act."""
    with pytest.raises(ConfigError, match="will not start unauthenticated"):
        from_env({})


def test_the_opt_out_must_be_explicit():
    settings = from_env({"WARRANT_ALLOW_UNAUTHENTICATED": "1"})
    assert settings.authenticator.open_access is True


def test_a_configured_but_unreadable_credentials_file_refuses_to_start():
    with pytest.raises(AuthConfigError, match="cannot be read"):
        load("/nonexistent/credentials.yaml")


def test_plaintext_secrets_are_refused(tmp_path):
    """A credentials file that leaks should be a list of hashes, not of keys."""
    path = tmp_path / "c.json"
    path.write_text(json.dumps({"clients": [
        {"client_id": "a", "secret_sha256": "hunter2", "scopes": ["read"]}
    ]}))
    with pytest.raises(AuthConfigError, match="64-character hex"):
        load(str(path))


def test_an_unknown_scope_is_refused(tmp_path):
    """A typo that silently granted nothing would look like a working
    credential until the route it was meant to open refused it."""
    path = tmp_path / "c.json"
    path.write_text(json.dumps({"clients": [
        {"client_id": "a", "secret_sha256": hash_secret("x"), "scopes": ["decid"]}
    ]}))
    with pytest.raises(AuthConfigError, match="unknown scope"):
        load(str(path))


def test_an_empty_client_list_is_refused(tmp_path):
    path = tmp_path / "c.json"
    path.write_text(json.dumps({"clients": []}))
    with pytest.raises(AuthConfigError, match="no clients"):
        load(str(path))


def test_duplicate_client_ids_are_refused(tmp_path):
    path = tmp_path / "c.json"
    path.write_text(json.dumps({"clients": [
        {"client_id": "a", "secret_sha256": hash_secret("x"), "scopes": ["read"]},
        {"client_id": "a", "secret_sha256": hash_secret("y"), "scopes": ["read"]},
    ]}))
    with pytest.raises(AuthConfigError, match="duplicate"):
        load(str(path))


def test_minted_secrets_are_long_and_unique():
    secrets_seen = {mint_secret() for _ in range(50)}
    assert len(secrets_seen) == 50
    assert all(len(s) >= 40 for s in secrets_seen)


def test_an_unknown_client_is_rejected_without_an_early_return():
    """Returning early on an unknown id leaks which ids exist through response
    timing. The comparison runs either way; this pins the behaviour rather than
    the timing, which is not measurable reliably in a test."""
    a = Authenticator(clients=load_clients())
    with pytest.raises(Exception):
        a.authenticate("Bearer nobody.secret")


def load_clients():
    from warrantservice.auth import Client

    return {"gateway": Client("gateway", hash_secret(GATEWAY_SECRET), frozenset({"read"}))}
