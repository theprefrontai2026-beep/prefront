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
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml

from warrant import ActionClass, ActionRegistry, SigningKey
from warrant.signing import b64u_decode


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
    return Settings(
        issuer=env.get("WARRANT_ISSUER", "warrant.local"),
        policy_version=env.get("WARRANT_POLICY_VERSION", registry.version),
        registry=registry,
        authority_key=key,
        registry_path=registry_path,
        authority_key_supplied=supplied,
    )
