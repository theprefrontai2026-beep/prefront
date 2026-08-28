"""Load trace_binding.yaml and resolve canonical check inputs from span rows.

This is the ONLY module that knows a span field might live in a top-level
ClickHouse column or inside the `attributes` map - every other module (
reconstruct.py, family2/*) asks for a canonical field name and gets a value
back, never a raw span dict.
"""

from __future__ import annotations

import importlib.resources
from dataclasses import dataclass
from typing import Any, Optional

import yaml

_DEFAULT_PROFILE_RESOURCE = ("evalengine.profiles", "trace_binding.default.yaml")


@dataclass(frozen=True)
class FieldRef:
    kind: str  # "column" | "attr"
    key: str

    def resolve(self, row: dict[str, Any]) -> Any:
        if self.kind == "column":
            return row.get(self.key)
        attrs = row.get("attributes") or {}
        return attrs.get(self.key)


@dataclass(frozen=True)
class SpanMatcher:
    name_prefix: str = ""
    kind: str = ""

    def matches(self, row: dict[str, Any]) -> bool:
        if self.kind and (row.get("kind") or "").upper() != self.kind.upper():
            return False
        if self.name_prefix and not (row.get("name") or "").startswith(self.name_prefix):
            return False
        return bool(self.kind or self.name_prefix)


@dataclass(frozen=True)
class BindingProfile:
    version: str
    tool_span: SpanMatcher
    turn_span: SpanMatcher
    session_root: SpanMatcher
    fields: dict[str, FieldRef]

    def field(self, name: str, row: dict[str, Any]) -> Any:
        ref = self.fields.get(name)
        return ref.resolve(row) if ref else None


def _matcher(d: dict[str, Any]) -> SpanMatcher:
    return SpanMatcher(name_prefix=str(d.get("name_prefix", "")), kind=str(d.get("kind", "")))


def _field_ref(d: dict[str, Any]) -> FieldRef:
    if "column" in d:
        return FieldRef(kind="column", key=str(d["column"]))
    if "attr" in d:
        return FieldRef(kind="attr", key=str(d["attr"]))
    raise ValueError(f"trace_binding field must declare 'column' or 'attr': {d!r}")


def parse(raw: dict[str, Any]) -> BindingProfile:
    body = raw["trace_binding"]
    fields = {name: _field_ref(ref) for name, ref in (body.get("fields") or {}).items()}
    return BindingProfile(
        version=str(body.get("version", "1")),
        tool_span=_matcher(body.get("tool_span") or {}),
        turn_span=_matcher(body.get("turn_span") or {}),
        session_root=_matcher(body.get("session_root") or {}),
        fields=fields,
    )


def load(path: str = "") -> BindingProfile:
    if path:
        with open(path, "r", encoding="utf-8") as f:
            return parse(yaml.safe_load(f))
    pkg, name = _DEFAULT_PROFILE_RESOURCE
    raw = importlib.resources.files(pkg).joinpath(name).read_text(encoding="utf-8")
    return parse(yaml.safe_load(raw))
