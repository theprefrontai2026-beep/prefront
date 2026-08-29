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


_DEFAULT_CHECK = {"temporal": "precondition", "predicate": "prohibition", "content": "field_restriction"}


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
    # content engine only: restrict the detector to these caller roles (empty =
    # every role) - a field can be restricted from SOME roles but approved for
    # others (a raw credit score hidden from a Loan Officer, visible to an
    # Underwriter), which applies_to_intents alone can't express.
    restricted_from_roles: tuple[str, ...] = ()
    # content engine only: the POSITIVE half of a restriction - "not X, but Y in
    # its place" (autonomous_build.md step 26). Tokens that an acceptable
    # substitute answer may contain; ANY one of them satisfies. They are
    # DECLARED, never derived: a rule may list the substitute field's name
    # ("tier") and/or the literal values it can take ("near-prime", ...),
    # because an answer often names the value without ever naming the field
    # ("She's Near-prime"). Deriving the value instead - score 712 -> Near-prime
    # - would mean the engine reading the policy's band table, a far bigger
    # change than checking that a declared token is present. Empty = this rule
    # asserts no substitute, and the check emits nothing (Hard Rule 16).
    required_substitute: tuple[str, ...] = ()
    # The check-families vocabulary this rule reports as (prefront-check-families.md:
    # precondition | sequencing | prohibition | field_restriction | approval_gate).
    # Defaults to the engine's most common shape (see _DEFAULT_CHECK) - a rule pack
    # author overrides with an explicit `check:` key when a rule needs the OTHER
    # id its engine can produce (a temporal rule reporting "sequencing" instead of
    # "precondition"; a predicate rule reporting "approval_gate" - though that's
    # already implied by approver_roles being set, see family1/predicate.py).
    check: str = ""

    def check_id(self) -> str:
        if self.check:
            return self.check
        if self.engine == "predicate" and self.approver_roles:
            return "approval_gate"
        return _DEFAULT_CHECK.get(self.engine, self.engine)


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
        check=str(raw.get("check", "")),
        restricted_from_roles=tuple(raw.get("restricted_from_roles") or ()),
        required_substitute=tuple(raw.get("required_substitute") or ()),
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
