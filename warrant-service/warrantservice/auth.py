"""Who may call this service, and which routes they may call.

The enforcement plane decides whether an AGENT may act. This file answers the
prior question the service had simply not asked: whether the caller is entitled
to ask at all. Without it, anyone who could reach the port could register their
own signing key, mint a Mission naming any subject with any budget, and collect
an `allow` — four unauthenticated calls to full self-authorization, because the
Mission Authority is the one component that can WIDEN permissions.

Three decisions shape this.

**Scopes, not a single key.** The routes are not equally dangerous. A gateway
on the hot path needs `decide` and nothing else; minting consent is the highest
privilege in the system and belongs to a control plane that never touches a
tool call. One credential for everything would mean every gateway could issue
Missions, which is the same hole with a password on it.

**Secrets are stored hashed.** The configuration file holds SHA-256 of each
secret, so a leaked config does not hand over working credentials, and
comparison is constant-time. `python -m warrantservice.credentials` mints them.

**Unauthenticated operation is possible but must be chosen.** Following the
convention the registry already uses: a missing credentials file is a HARD
startup failure unless `WARRANT_ALLOW_UNAUTHENTICATED=1` is set explicitly.
Someone who wants the open door has to say so, in writing, in their deployment
config — rather than getting it by forgetting to configure anything. The state
is then reported by `/healthz` for as long as it lasts.

What this is NOT: an identity store for people. These are service credentials
for the components on the call path. Human identity comes only from the
customer's IdP — see `oidc.py`.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

import yaml

# Every scope the service recognises. A credential naming one that is not here
# is a configuration error, not a no-op: a typo'd scope that silently granted
# nothing would look like a working credential until the route it was meant to
# open refused it in production.
SCOPES = {
    "decide": "POST /v1/decisions — the hot path",
    "read": "read trees, registry, denylist, verify a Mission",
    "mission:issue": "mint and revoke Missions — the highest privilege here",
    "agent-key:register": "publish an agent process's public key",
    "tree:manage": "open trees, spawn nodes, reserve and settle budget",
    "tree:revoke": "press stop on a task",
    "denylist:merge": "accept revocations replicated from a peer",
    "token:issue": "mint a task tree's ROOT token — the start of a delegation chain",
    "approval:decide": "answer a step-up on an operator's behalf — see the README "
                       "on why this is a fallback for deployments with no IdP",
}


class AuthConfigError(RuntimeError):
    """The service cannot start with the credentials it was given."""


class Unauthenticated(Exception):
    """No usable credential was presented."""


class Forbidden(Exception):
    """A valid credential without the scope this route needs."""


def hash_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def mint_secret() -> str:
    """A secret with 256 bits of entropy, URL-safe so it survives env vars."""
    return secrets.token_urlsafe(32)


@dataclass(frozen=True)
class Client:
    client_id: str
    secret_sha256: str
    scopes: frozenset[str]
    description: str = ""

    def permits(self, scope: str) -> bool:
        return scope in self.scopes


@dataclass
class Authenticator:
    """The configured clients, or an explicit decision to have none."""

    clients: dict[str, Client] = field(default_factory=dict)
    # True only when the operator explicitly opted out. Never a default.
    open_access: bool = False
    source: str = ""

    @property
    def enabled(self) -> bool:
        return not self.open_access

    def authenticate(self, header: Optional[str]) -> Optional[Client]:
        """Resolve `Authorization: Bearer <client_id>.<secret>` to a client.

        Returns None in open-access mode so callers can skip the scope check
        without a second flag to forget.
        """
        if self.open_access:
            return None
        if not header:
            raise Unauthenticated(
                "no Authorization header. This service requires a service "
                "credential: Authorization: Bearer <client_id>.<secret>"
            )
        scheme, _, token = header.partition(" ")
        if scheme.lower() != "bearer" or not token:
            raise Unauthenticated("Authorization header must be 'Bearer <client_id>.<secret>'")

        client_id, _, secret = token.partition(".")
        client = self.clients.get(client_id)

        # Hash and compare even when the client is unknown, against a dummy
        # digest. Returning early on an unknown id leaks which ids exist
        # through response timing, and the work is a single SHA-256.
        expected = client.secret_sha256 if client else hash_secret("\x00unknown")
        presented = hash_secret(secret)
        ok = hmac.compare_digest(expected, presented)

        if not client or not ok:
            raise Unauthenticated("credential is not recognised")
        return client

    def require(self, client: Optional[Client], scope: str) -> None:
        if self.open_access:
            return
        if client is None:  # defensive: authenticate() raises rather than returning None
            raise Unauthenticated("credential is not recognised")
        if not client.permits(scope):
            raise Forbidden(
                f"credential {client.client_id!r} does not have the {scope!r} scope "
                f"(it has: {', '.join(sorted(client.scopes)) or 'none'})"
            )


def load(path: Optional[str], allow_unauthenticated: bool = False) -> Authenticator:
    """Read the credentials file, or refuse to start.

    The refusal is the point. An unauthenticated PDS is not a degraded mode, it
    is an open door in front of the component that mints consent — so it cannot
    be reached by forgetting to set a variable.
    """
    if not path:
        if allow_unauthenticated:
            return Authenticator(open_access=True)
        raise AuthConfigError(
            "WARRANT_CREDENTIALS_PATH is not set. This service will not start "
            "unauthenticated by accident: anyone able to reach it could mint a "
            "Mission naming any subject and collect an allow. Configure "
            "credentials (python -m warrantservice.credentials new <client-id> "
            "--scopes decide,read), or set WARRANT_ALLOW_UNAUTHENTICATED=1 to "
            "say deliberately that this deployment is not reachable by anyone "
            "who should not already have this access"
        )

    file = Path(path)
    try:
        raw = file.read_text()
    except OSError as exc:
        raise AuthConfigError(
            f"WARRANT_CREDENTIALS_PATH is set to {path!r} but cannot be read "
            f"({exc}). Refusing to start rather than falling back to open access"
        ) from exc

    try:
        doc = yaml.safe_load(raw) if file.suffix in (".yaml", ".yml") else json.loads(raw)
    except Exception as exc:
        raise AuthConfigError(f"credentials file {path!r} is not valid YAML/JSON: {exc}") from exc

    if not isinstance(doc, dict) or not isinstance(doc.get("clients"), list):
        raise AuthConfigError(f"credentials file {path!r} must be a mapping with a 'clients' list")

    clients: dict[str, Client] = {}
    for i, entry in enumerate(doc["clients"]):
        if not isinstance(entry, dict):
            raise AuthConfigError(f"credentials entry {i} is not a mapping")
        unknown = set(entry) - {"client_id", "secret_sha256", "scopes", "description"}
        if unknown:
            raise AuthConfigError(
                f"credentials entry {i} has unknown key(s) {sorted(unknown)} — "
                "refusing rather than ignoring them"
            )
        client_id = str(entry.get("client_id") or "")
        digest = str(entry.get("secret_sha256") or "")
        if not client_id:
            raise AuthConfigError(f"credentials entry {i} has no client_id")
        if client_id in clients:
            raise AuthConfigError(f"duplicate client_id {client_id!r}")
        if len(digest) != 64 or not all(c in "0123456789abcdef" for c in digest.lower()):
            raise AuthConfigError(
                f"client {client_id!r}: secret_sha256 must be a 64-character hex "
                "SHA-256 digest. Plaintext secrets are not accepted — a config "
                "file that leaks should not hand over working credentials"
            )
        scopes = entry.get("scopes") or []
        if not isinstance(scopes, list) or any(not isinstance(s, str) for s in scopes):
            raise AuthConfigError(f"client {client_id!r}: scopes must be a list of strings")
        bad = sorted(set(scopes) - set(SCOPES))
        if bad:
            raise AuthConfigError(
                f"client {client_id!r} names unknown scope(s) {bad}. Known scopes: "
                f"{', '.join(sorted(SCOPES))}. A typo that silently granted nothing "
                "would look like a working credential until the route refused it"
            )
        clients[client_id] = Client(
            client_id=client_id,
            secret_sha256=digest.lower(),
            scopes=frozenset(scopes),
            description=str(entry.get("description") or ""),
        )

    if not clients:
        raise AuthConfigError(
            f"credentials file {path!r} defines no clients, so nothing could call "
            "this service. Set WARRANT_ALLOW_UNAUTHENTICATED=1 if that was the intent"
        )
    return Authenticator(clients=clients, source=path)
