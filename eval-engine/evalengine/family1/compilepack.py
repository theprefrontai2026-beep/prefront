"""Load and validate a published rule_pack.yaml.

Family 1 degrades to "not configured" when no rule pack is on disk (Hard
Rule 9) - callers get an empty RulePack, never an exception. A rule pack
that DOES exist but is malformed is a real error (a broken artifact, not an
absent one) and raises.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

import yaml

VALID_ENGINES = ("temporal", "predicate", "content")


@dataclass(frozen=True)
class Rule:
    rule_id: str
    engine: str
    effect: str
    source: dict[str, Any] = field(default_factory=dict)
    conditions: tuple[dict[str, Any], ...] = ()
    approver_roles: tuple[str, ...] = ()
    detectors: tuple[dict[str, Any], ...] = ()
    applies_to_intents: tuple[str, ...] = ()
    automaton: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RulePack:
    version: str = ""
    source_skill: str = ""
    source_skill_version: str = ""
    rules: tuple[Rule, ...] = ()

    def by_engine(self, engine: str) -> tuple[Rule, ...]:
        return tuple(r for r in self.rules if r.engine == engine)


EMPTY = RulePack()


def _rule(raw: dict[str, Any]) -> Rule:
    engine = str(raw.get("engine", ""))
    if engine not in VALID_ENGINES:
        raise ValueError(f"rule_pack: rule {raw.get('rule_id')!r} has unknown engine {engine!r}")
    return Rule(
        rule_id=str(raw.get("rule_id", "")),
        engine=engine,
        effect=str(raw.get("effect", "flag")),
        source=raw.get("source") or {},
        conditions=tuple(raw.get("conditions") or ()),
        approver_roles=tuple(raw.get("approver_roles") or ()),
        detectors=tuple(raw.get("detectors") or ()),
        applies_to_intents=tuple(raw.get("applies_to_intents") or ()),
        automaton=raw.get("automaton") or {},
    )


def parse(raw: dict[str, Any]) -> RulePack:
    body = raw["rule_pack"]
    return RulePack(
        version=str(body.get("version", "1")),
        source_skill=str(body.get("source_skill", "")),
        source_skill_version=str(body.get("source_skill_version", "")),
        rules=tuple(_rule(r) for r in (body.get("rules") or [])),
    )


def load(path: str = "") -> RulePack:
    if not path:
        return EMPTY
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    if not raw:
        return EMPTY
    return parse(raw)
