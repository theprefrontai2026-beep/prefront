"""Deterministic test-case generation.

design.md: "If you cannot test the extracted rule, it is not ready for runtime."
For each candidate rule we synthesize at least one *trigger* case (inputs that
satisfy the condition -> expected effect) and, where the operator allows, one
*negative* case (inputs that do not trigger -> expected allow). No LLM.

Test cases mirror design.md's ``test_cases.yaml`` shape.
"""

from __future__ import annotations

from typing import Any, Iterable, Optional

from .schema import CandidateRule


def _expected(rule: CandidateRule) -> dict[str, Any]:
    eff = rule.effect
    exp: dict[str, Any] = {"decision": eff.decision}
    if eff.decision == "approval_required":
        exp["approval_required"] = True
        if eff.approver_role:
            exp["approver_role"] = eff.approver_role
    elif eff.decision == "allow":
        exp["approval_required"] = False
    if eff.restricted_fields:
        exp["restricted_fields"] = eff.restricted_fields
    return exp


def _trigger_value(op: str, value: Any) -> Optional[Any]:
    """A value for the condition field that makes ``field op value`` true."""
    if op == "==":
        return value
    if op == "!=":
        return _other_than(value)
    if op == ">":
        return _bump(value, +1)
    if op == ">=":
        return value
    if op == "<":
        return _bump(value, -1)
    if op == "<=":
        return value
    if op == "in":
        return value[0] if isinstance(value, list) and value else None
    if op == "not_in":
        return _other_than(value[0] if isinstance(value, list) and value else value)
    return None


def _negative_value(op: str, value: Any) -> Optional[Any]:
    """A value that makes the condition false, if one is cleanly derivable."""
    if op == "==":
        return _other_than(value)
    if op == ">":
        return value  # value is not > value
    if op == ">=":
        return _bump(value, -1)
    if op == "<":
        return value
    if op == "<=":
        return _bump(value, +1)
    if op == "in":
        return _other_than(value[0] if isinstance(value, list) and value else value)
    if op == "not_in":
        return value[0] if isinstance(value, list) and value else None
    return None  # != has no single clean negative


def _bump(value: Any, direction: int) -> Optional[Any]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value + direction


def _other_than(value: Any) -> Any:
    if isinstance(value, bool):
        return not value
    if isinstance(value, (int, float)):
        return value + 1
    return f"not_{value}"


def _trigger_input(rule: CandidateRule) -> Optional[dict[str, Any]]:
    """Inputs satisfying ALL conditions, or None if any can't be triggered."""
    inputs: dict[str, Any] = {}
    for c in rule.conditions:
        tv = _trigger_value(c.operator, c.value)
        if tv is None:
            return None
        inputs[c.field] = tv
    return inputs


def generate_test_cases(rules: Iterable[CandidateRule]) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for rule in rules:
        trig = _trigger_input(rule)
        if trig is not None:
            cases.append(
                {
                    "test_id": f"{rule.rule_key}__triggers",
                    "rule_key": rule.rule_key,
                    "input": trig,
                    "expected": _expected(rule),
                }
            )
            # Negative: flip the first condition that has a clean negative,
            # keeping the rest at their trigger values -> the AND is false.
            for c in rule.conditions:
                neg = _negative_value(c.operator, c.value)
                if neg is not None and neg != trig.get(c.field):
                    cases.append(
                        {
                            "test_id": f"{rule.rule_key}__does_not_trigger",
                            "rule_key": rule.rule_key,
                            "input": {**trig, c.field: neg},
                            "expected": {"decision": "allow", "approval_required": False},
                        }
                    )
                    break
    return cases


def untestable_rules(rules: Iterable[CandidateRule]) -> list[str]:
    """Rule keys for which no trigger case could be generated (need attention)."""
    return [r.rule_key for r in rules if _trigger_input(r) is None]
