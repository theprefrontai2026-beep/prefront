"""Service configuration — where a deployment's vocabulary enters.

The engine names no application, so every noun this service knows about
arrives through a file a deployment writes. That file is the action-class
registry: the verb list, and what each verb's blast radius is.

Two behaviours matter and they are deliberately different, matching the
convention the rest of this repo already uses for artifact-gated services:

  **Unset** is a legitimate state. The service starts with an EMPTY registry,
  and because an unregistered action class fails closed, it then denies every
  side-effect call and says why. That is the honest posture for a service
  nobody has configured — running and refusing, rather than refusing to run.

  **Set but unreadable** is a hard startup failure. A path that was configured
  and cannot be loaded means someone intended a boundary that is not there, and
  starting anyway would silently swap their policy for the empty one.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

from warrant import ActionClass, ActionRegistry, SigningKey
from warrant.signing import b64u_decode

from . import auth as _auth
from . import oidc as _oidc


class ConfigError(RuntimeError):
    """The service cannot start with the configuration it was given."""


@dataclass
class Settings:
    issuer: str
    policy_version: str
    registry: ActionRegistry
    authority_key: SigningKey
    registry_path: str
    authority_key_supplied: bool
    # Who may call this service at all, and with which scopes. Never None:
    # an unauthenticated deployment is an explicit `Authenticator(open_access
    # =True)`, so nothing downstream has to decide what absence means.
    authenticator: "_auth.Authenticator" = field(
        default_factory=lambda: _auth.Authenticator(open_access=True)
    )
    # Where the END USER's identity comes from. Disabled leaves the legacy
    # unverified `token_subject` path open; enabled closes it.
    oidc: "_oidc.OidcSettings" = field(default_factory=lambda: _oidc.OidcSettings())
    # Task-tree tokens. When required, an agent must prove which node it is
    # rather than asserting it in a signed attestation; the self-asserted path
    # is then closed, the same way configuring an IdP closes the unverified
    # subject path.
    task_tokens_required: bool = False
    token_key: Optional[SigningKey] = None
    token_key_supplied: bool = False
    token_ttl_seconds: int = 900
    token_audience: str = "warrant-pds"
    dpop_window_seconds: int = 60


def load_registry(path: Optional[str]) -> tuple[ActionRegistry, str]:
    if not path:
        return ActionRegistry(version="unconfigured"), ""

    file = Path(path)
    try:
        raw = file.read_text()
    except OSError as exc:
        raise ConfigError(
            f"WARRANT_ACTION_REGISTRY_PATH is set to {path!r} but it cannot be "
            f"read ({exc}). Refusing to start: a configured boundary that "
            "silently becomes the empty one is worse than no service"
        ) from exc

    try:
        doc = yaml.safe_load(raw) if file.suffix in (".yaml", ".yml") else json.loads(raw)
    except Exception as exc:
        raise ConfigError(f"action registry at {path!r} is not valid YAML/JSON: {exc}") from exc

    if not isinstance(doc, dict) or not isinstance(doc.get("actions"), list):
        raise ConfigError(
            f"action registry at {path!r} must be a mapping with an 'actions' list"
        )

    classes = []
    for i, entry in enumerate(doc["actions"]):
        if not isinstance(entry, dict) or not entry.get("name"):
            raise ConfigError(f"action registry entry {i} has no name")
        unknown = set(entry) - {"name", "blast_radius", "side_effect", "description"}
        if unknown:
            raise ConfigError(
                f"action registry entry {entry['name']!r} has unknown key(s) "
                f"{sorted(unknown)} — refusing rather than ignoring them"
            )
        try:
            classes.append(
                ActionClass(
                    name=str(entry["name"]),
                    blast_radius=str(entry.get("blast_radius", "low")),
                    side_effect=bool(entry.get("side_effect", False)),
                    description=str(entry.get("description", "")),
                )
            )
        except Exception as exc:
            raise ConfigError(f"action registry entry {entry['name']!r}: {exc}") from exc

    version = str(doc.get("version") or file.stem)
    return ActionRegistry(classes, version=version), path


def load_token_key(raw: Optional[str], key_id: str = "warrant-token-1") -> tuple[SigningKey, bool]:
    """The task-token signing key — deliberately NOT the Authority's.

    The Authority's signature says a human approved a task, and resource
    servers we do not run verify it. This key says an agent holds a node, and
    only this service verifies it. Sharing one key would mean a compromise of
    the busy online path also forged consent.
    """
    if not raw:
        return SigningKey.generate(key_id), False
    try:
        return SigningKey.from_raw(key_id, b64u_decode(raw)), True
    except Exception as exc:
        raise ConfigError(
            f"WARRANT_TOKEN_KEY is set but is not a base64url-encoded 32-byte "
            f"Ed25519 private key: {exc}"
        ) from exc


def load_authority_key(raw: Optional[str]) -> tuple[SigningKey, bool]:
    """The Mission Authority's private key, from config or freshly generated.

    A generated key is correct for a demo and wrong for anything else: Missions
    signed under it stop verifying the moment the process restarts, so any
    resource server holding the old JWKS sees genuine Missions as forgeries.
    The caller announces that at startup rather than this function hiding it.
    """
    key_id = os.environ.get("WARRANT_AUTHORITY_KEY_ID", "warrant-authority-1")
    if not raw:
        return SigningKey.generate(key_id), False
    try:
        return SigningKey.from_raw(key_id, b64u_decode(raw)), True
    except Exception as exc:
        raise ConfigError(
            f"WARRANT_AUTHORITY_KEY is set but is not a base64url-encoded 32-byte "
            f"Ed25519 private key: {exc}"
        ) from exc


def from_env(env: Optional[dict] = None) -> Settings:
    env = dict(os.environ if env is None else env)
    registry, registry_path = load_registry(env.get("WARRANT_ACTION_REGISTRY_PATH"))
    key, supplied = load_authority_key(env.get("WARRANT_AUTHORITY_KEY"))
    try:
        authenticator = _auth.load(
            env.get("WARRANT_CREDENTIALS_PATH"),
            allow_unauthenticated=env.get("WARRANT_ALLOW_UNAUTHENTICATED", "") == "1",
        )
    except _auth.AuthConfigError as exc:
        # Re-raised as ConfigError so `__main__` has one failure type to report
        # and an operator sees one consistent message shape at startup.
        raise ConfigError(str(exc)) from exc
    tokens_required = env.get("WARRANT_TASK_TOKENS", "").lower() in ("required", "1", "true")
    token_key, token_key_supplied = load_token_key(env.get("WARRANT_TOKEN_KEY"))
    return Settings(
        issuer=env.get("WARRANT_ISSUER", "warrant.local"),
        policy_version=env.get("WARRANT_POLICY_VERSION", registry.version),
        registry=registry,
        authority_key=key,
        registry_path=registry_path,
        authority_key_supplied=supplied,
        authenticator=authenticator,
        oidc=_oidc.from_env(env),
        task_tokens_required=tokens_required,
        token_key=token_key,
        token_key_supplied=token_key_supplied,
        token_ttl_seconds=int(env.get("WARRANT_TOKEN_TTL", "900")),
        token_audience=env.get("WARRANT_TOKEN_AUDIENCE", "warrant-pds"),
        dpop_window_seconds=int(env.get("WARRANT_DPOP_WINDOW", "60")),
    )
