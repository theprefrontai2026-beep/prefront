"""Domain-pack loader + resolver.

A pack is a YAML vocabulary map that mirrors the four runtime binding namespaces
(column / request_param / metric / caller). The :class:`Pack` wrapper precomputes
alias indexes and answers the questions the validators ask: does this field/role/
action map to a known symbol, and to which namespace?

Packs are *configuration*: built-ins ship under this directory; uploads land in
``SKILLBUILDER_DOMAIN_PACKS`` if set. Engine code never hardcodes a domain's
vocabulary — it only loads packs.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import yaml

from ..schema import BindsTo, DomainPack

_BUILTIN_DIR = Path(__file__).resolve().parent


def _upload_dir() -> Optional[Path]:
    p = os.environ.get("SKILLBUILDER_DOMAIN_PACKS")
    return Path(p) if p else None


def _search_dirs() -> list[Path]:
    dirs = [_BUILTIN_DIR]
    up = _upload_dir()
    if up and up.exists():
        dirs.insert(0, up)  # uploads override built-ins
    return dirs


class Pack:
    """Resolution helpers over a loaded :class:`DomainPack`."""

    def __init__(self, model: DomainPack) -> None:
        self.model = model
        # alias (lowercased) -> canonical field name, and field -> binds_to
        self._field_alias: dict[str, str] = {}
        for name, f in model.fields.items():
            self._field_alias[name.lower()] = name
            for a in f.aliases:
                self._field_alias[a.lower()] = name
        # alias -> canonical role
        self._role_alias: dict[str, str] = {}
        for name, r in model.roles.items():
            self._role_alias[name.lower()] = name
            for a in r.aliases:
                self._role_alias[a.lower()] = name
        # alias -> canonical action
        self._action_alias: dict[str, str] = {}
        for name, a in model.actions.items():
            self._action_alias[name.lower()] = name
            for al in a.aliases:
                self._action_alias[al.lower()] = name

    # -- vocabulary for the extractor -----------------------------------------

    def known_fields(self) -> list[str]:
        return list(self.model.fields)

    def known_roles(self) -> list[str]:
        return list(self.model.roles)

    def known_intents(self) -> list[str]:
        return sorted({a.intent for a in self.model.actions.values() if a.intent})

    # -- resolution (mirrors the four binding namespaces) ---------------------

    def resolve_field(self, name: str) -> Optional[BindsTo]:
        """Namespace a condition field would bind to, or None if unmappable."""
        if not name:
            return None
        if name.startswith("caller."):
            return "caller"
        canon = self._field_alias.get(name.split(".")[-1].lower())
        if canon is None:
            return None
        return self.model.fields[canon].binds_to or "column"

    def field_canonical(self, name: str) -> Optional[str]:
        return self._field_alias.get(name.split(".")[-1].lower())

    def allowed_values(self, name: str) -> Optional[list]:
        canon = self.field_canonical(name)
        return self.model.fields[canon].allowed_values if canon else None

    def resolve_role(self, name: str) -> Optional[str]:
        return self._role_alias.get((name or "").strip().lower())

    def resolve_action(self, name: str) -> Optional[str]:
        return self._action_alias.get((name or "").strip().lower())

    def intent_for_action(self, name: str) -> Optional[str]:
        canon = self.resolve_action(name)
        return self.model.actions[canon].intent if canon else None

    def render_reason(self, code: str) -> Optional[str]:
        rc = self.model.reason_codes.get(code)
        return rc.message if rc else None


# --- loading ------------------------------------------------------------------


def _parse(path: Path) -> Pack:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return Pack(DomainPack.model_validate(data))


def load_pack_file(path: str | Path) -> Pack:
    return _parse(Path(path))


def load_pack(domain: str) -> Optional[Pack]:
    """Load the pack whose file or declared domain matches ``domain``."""
    if not domain:
        return None
    for d in _search_dirs():
        cand = d / f"{domain}.yaml"
        if cand.exists():
            return _parse(cand)
    # Fall back to matching the declared `domain:` inside any pack file.
    for d in _search_dirs():
        for f in sorted(d.glob("*.yaml")):
            try:
                pack = _parse(f)
            except Exception:
                continue
            if pack.model.domain == domain:
                return pack
    return None


def list_pack_names() -> list[str]:
    names: list[str] = []
    for d in _search_dirs():
        for f in sorted(d.glob("*.yaml")):
            if f.stem not in names:
                names.append(f.stem)
    return names
