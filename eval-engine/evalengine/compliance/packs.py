"""Layer A: framework packs. Shipped with the engine as YAML data under
`evalengine/frameworks/`, or read from `EVAL_FRAMEWORK_PACKS_DIR` when a
deployment wants to add or replace one. A pack names control classes and
data classes only - never a column, table, role, channel or policy section.

    schema_version: prefront.framework_pack.v1
    framework: gdpr
    title: GDPR
    version: 1
    out_of_scope: [ ... prose ... ]
    controls:
      - id: art5_1_c
        title: Data minimisation
        control_class: minimization
        data_class: personal_data     # optional
        note: ...                     # optional, surfaced on the report row
"""

from __future__ import annotations

import importlib.resources
import pathlib
from dataclasses import dataclass, field
from typing import Any

import yaml

from .classes import CONTROL_CLASSES

SCHEMA_VERSION = "prefront.framework_pack.v1"
_BUNDLED_PACKAGE = "evalengine.frameworks"


@dataclass(frozen=True)
class Control:
    id: str
    title: str
    control_class: str
    data_class: str = ""
    note: str = ""


@dataclass(frozen=True)
class FrameworkPack:
    framework: str
    title: str
    version: str
    controls: tuple[Control, ...]
    out_of_scope: tuple[str, ...] = field(default_factory=tuple)


def parse(raw: dict[str, Any], label: str = "<pack>") -> FrameworkPack:
    if not isinstance(raw, dict):
        raise ValueError(f"{label}: framework pack must be a mapping")
    if raw.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"{label}: schema_version must be {SCHEMA_VERSION!r}, got {raw.get('schema_version')!r}")
    framework = str(raw.get("framework") or "").strip()
    if not framework:
        raise ValueError(f"{label}: framework id is required")
    controls: list[Control] = []
    seen: set[str] = set()
    for i, c in enumerate(raw.get("controls") or []):
        if not isinstance(c, dict):
            raise ValueError(f"{label}: controls[{i}] must be a mapping")
        cid = str(c.get("id") or "").strip()
        cls = str(c.get("control_class") or "").strip()
        if not cid:
            raise ValueError(f"{label}: controls[{i}] has no id")
        if cid in seen:
            raise ValueError(f"{label}: duplicate control id {cid!r}")
        if cls not in CONTROL_CLASSES:
            raise ValueError(f"{label}: control {cid!r} names unknown control_class {cls!r}")
        seen.add(cid)
        controls.append(Control(
            id=cid, title=str(c.get("title") or cid), control_class=cls,
            data_class=str(c.get("data_class") or "").strip(), note=str(c.get("note") or "").strip(),
        ))
    return FrameworkPack(
        framework=framework,
        title=str(raw.get("title") or framework),
        version=str(raw.get("version") or "1"),
        controls=tuple(controls),
        out_of_scope=tuple(str(s) for s in (raw.get("out_of_scope") or [])),
    )


def _bundled() -> list[FrameworkPack]:
    out: list[FrameworkPack] = []
    root = importlib.resources.files(_BUNDLED_PACKAGE)
    for entry in sorted(root.iterdir(), key=lambda e: e.name):
        if entry.name.endswith((".yaml", ".yml")):
            out.append(parse(yaml.safe_load(entry.read_text(encoding="utf-8")), entry.name))
    return out


def _from_dir(path: str) -> list[FrameworkPack]:
    out: list[FrameworkPack] = []
    for p in sorted(pathlib.Path(path).glob("*.y*ml")):
        with p.open("r", encoding="utf-8") as f:
            out.append(parse(yaml.safe_load(f), str(p)))
    return out


def load_packs(extra_dir: str = "") -> dict[str, FrameworkPack]:
    """Bundled packs, then any in `extra_dir` (same framework id replaces the
    bundled one - a deployment can tighten a pack without forking the image)."""
    packs = {p.framework: p for p in _bundled()}
    if extra_dir:
        for p in _from_dir(extra_dir):
            packs[p.framework] = p
    return packs
