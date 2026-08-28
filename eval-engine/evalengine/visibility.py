"""Load visibility_profile.yaml - what the trace source can/can't show.

Used by the combinator (never by checks - Hard Rule 3 keeps checks pure) to
split an indeterminate verdict into missing_precondition (the agent never
established the fact - a real gap) vs visibility_gap (the trace source never
captures that fact at all - an artifact-coverage limitation, not a finding
about the agent). See Hard Rule 7.
"""

from __future__ import annotations

import importlib.resources
from dataclasses import dataclass
from typing import Any

import yaml

_DEFAULT_PROFILE_RESOURCE = ("evalengine.profiles", "visibility_profile.default.yaml")


@dataclass(frozen=True)
class VisibilityProfile:
    version: str
    captures: dict[str, bool]

    def captured(self, key: str) -> bool:
        # Unknown capture key: assume captured (fail toward missing_precondition,
        # i.e. treat it as a real agent gap) rather than silently manufacturing
        # visibility_gap excuses for keys nobody declared.
        return bool(self.captures.get(key, True))


def parse(raw: dict[str, Any]) -> VisibilityProfile:
    body = raw["visibility_profile"]
    return VisibilityProfile(
        version=str(body.get("version", "1")),
        captures={str(k): bool(v) for k, v in (body.get("captures") or {}).items()},
    )


def load(path: str = "") -> VisibilityProfile:
    if path:
        with open(path, "r", encoding="utf-8") as f:
            return parse(yaml.safe_load(f))
    pkg, name = _DEFAULT_PROFILE_RESOURCE
    raw = importlib.resources.files(pkg).joinpath(name).read_text(encoding="utf-8")
    return parse(yaml.safe_load(raw))
