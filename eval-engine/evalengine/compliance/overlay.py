"""Layer B: the per-deployment compliance overlay. The ONLY place the
compliance report meets domain vocabulary - column names, the deployment's
policy document, the sector regime it cites. Read from
`EVAL_COMPLIANCE_OVERLAY_PATH`; an empty path is "not configured" (every
data class unbound, no domain regime), never an error (Hard Rule 9).

    schema_version: prefront.compliance_overlay.v1
    deployment: <id>
    policy_document: <file the policy_section values refer into>
    data_classes:
      personal_data: [<table>.<column>, ...]
    frameworks: [gdpr, soc2]
    domain_regime:
      - regime: <name>
        bindings:
          - {policy_section: "n.m", control_class: field_protection, data_class: personal_data}
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import yaml

from .classes import CONTROL_CLASSES

SCHEMA_VERSION = "prefront.compliance_overlay.v1"


@dataclass(frozen=True)
class RegimeBinding:
    policy_section: str
    control_class: str
    data_class: str = ""
    note: str = ""


@dataclass(frozen=True)
class Regime:
    regime: str
    bindings: tuple[RegimeBinding, ...]


@dataclass(frozen=True)
class Overlay:
    deployment: str = ""
    policy_document: str = ""
    # data class label -> physical columns as declared ("table.column")
    data_classes: dict[str, tuple[str, ...]] = field(default_factory=dict)
    frameworks: tuple[str, ...] = field(default_factory=tuple)
    domain_regime: tuple[Regime, ...] = field(default_factory=tuple)
    path: str = ""

    @property
    def configured(self) -> bool:
        return bool(self.deployment or self.data_classes or self.frameworks or self.domain_regime)

    def columns_for(self, data_class: str) -> tuple[str, ...]:
        return self.data_classes.get(data_class, ())

    def field_names_for(self, data_class: str) -> tuple[str, ...]:
        """Bare column names (`table.column` -> `column`), lower-cased, for
        matching against a verdict's detail text."""
        return tuple(sorted({c.rsplit(".", 1)[-1].lower() for c in self.columns_for(data_class) if c}))


EMPTY_OVERLAY = Overlay()


def parse(raw: dict[str, Any], path: str = "") -> Overlay:
    if not isinstance(raw, dict):
        raise ValueError("compliance overlay must be a mapping")
    if raw.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"compliance overlay schema_version must be {SCHEMA_VERSION!r}, got {raw.get('schema_version')!r}")
    classes: dict[str, tuple[str, ...]] = {}
    for label, cols in (raw.get("data_classes") or {}).items():
        if cols is None:
            cols = []
        if not isinstance(cols, list):
            raise ValueError(f"data_classes.{label} must be a list of table.column strings")
        classes[str(label)] = tuple(str(c).strip() for c in cols if str(c).strip())
    regimes: list[Regime] = []
    for i, r in enumerate(raw.get("domain_regime") or []):
        if not isinstance(r, dict):
            raise ValueError(f"domain_regime[{i}] must be a mapping")
        bindings: list[RegimeBinding] = []
        for j, b in enumerate(r.get("bindings") or []):
            if not isinstance(b, dict):
                raise ValueError(f"domain_regime[{i}].bindings[{j}] must be a mapping")
            cls = str(b.get("control_class") or "").strip()
            if cls not in CONTROL_CLASSES:
                raise ValueError(f"domain_regime[{i}].bindings[{j}] names unknown control_class {cls!r}")
            bindings.append(RegimeBinding(
                policy_section=str(b.get("policy_section") or "").strip(),
                control_class=cls,
                data_class=str(b.get("data_class") or "").strip(),
                note=str(b.get("note") or "").strip(),
            ))
        regimes.append(Regime(regime=str(r.get("regime") or f"regime-{i}"), bindings=tuple(bindings)))
    return Overlay(
        deployment=str(raw.get("deployment") or "").strip(),
        policy_document=str(raw.get("policy_document") or "").strip(),
        data_classes=classes,
        frameworks=tuple(str(f).strip().lower() for f in (raw.get("frameworks") or []) if str(f).strip()),
        domain_regime=tuple(regimes),
        path=path,
    )


def load_overlay(path: str = "") -> Overlay:
    if not path:
        return EMPTY_OVERLAY
    with open(path, "r", encoding="utf-8") as f:
        return parse(yaml.safe_load(f), path)
