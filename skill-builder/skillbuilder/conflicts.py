"""Conflict detector.

Surfaces problems *before* human review (design.md "Conflict Detector"):
unknown roles, unknown fields, contradictory allow/block effects on the same
field, and overlapping numeric thresholds. Deterministic — no LLM.

This is intentionally conservative: it flags for a human, it does not auto-fix.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable, Optional

from .schema import CandidateRule, Conflict

_NUMERIC_OPS = {">", ">=", "<", "<=", "=="}


def detect_conflicts(
    rules: Iterable[CandidateRule],
    *,
    known_roles: Optional[Iterable[str]] = None,
    known_fields: Optional[Iterable[str]] = None,
) -> list[Conflict]:
    rules = list(rules)
    roles = {r.lower() for r in (known_roles or [])}
    fields = {f.lower() for f in (known_fields or [])}
    conflicts: list[Conflict] = []
    seq = 0

    def new_id() -> str:
        nonlocal seq
        seq += 1
        return f"conflict_{seq:03d}"

    # 1. Approver role not in the known-roles list.
    if roles:
        for r in rules:
            role = (r.effect.approver_role or "").lower()
            if role and role not in roles:
                conflicts.append(
                    Conflict(
                        conflict_id=new_id(),
                        severity="high",
                        type="unknown_role",
                        rules=[r.rule_key],
                        message=f"Rule references approver role '{r.effect.approver_role}' not in the known-roles list.",
                        recommended_action="Map the role to a known role or add it to the role catalog.",
                    )
                )

    # Flatten to (rule, condition) entries — rules may now AND several conditions.
    entries = [(r, cond) for r in rules for cond in r.conditions]

    # 2. Condition field not in the semantic map (one conflict per rule).
    if fields:
        for r in rules:
            unknown = sorted(
                {c.field for c in r.conditions if c.field.lower() not in fields}
            )
            if unknown:
                conflicts.append(
                    Conflict(
                        conflict_id=new_id(),
                        severity="medium",
                        type="unknown_field",
                        rules=[r.rule_key],
                        message=f"Rule conditions on field(s) {unknown} not in the semantic map.",
                        recommended_action="Add the field to the semantic map or correct the field name.",
                    )
                )

    # 3. Same field+operator+value but different decisions: a direct
    #    contradiction (disjoint thresholds are NOT flagged here — see #4).
    by_test: dict[tuple, set[str]] = defaultdict(set)
    decisions_by_test: dict[tuple, set[str]] = defaultdict(set)
    for r, cond in entries:
        key = (cond.field, cond.operator, _hashable(cond.value))
        by_test[key].add(r.rule_key)
        decisions_by_test[key].add(r.effect.decision)
    for key, decisions in decisions_by_test.items():
        if len(decisions) > 1 and len(by_test[key]) > 1:
            conflicts.append(
                Conflict(
                    conflict_id=new_id(),
                    severity="high",
                    type="contradictory_effect",
                    rules=sorted(by_test[key]),
                    message=f"Identical condition on '{key[0]}' yields different decisions ({', '.join(sorted(decisions))}).",
                    recommended_action="Clarify precedence or merge the rules.",
                )
            )

    # 3b. Same rule_key extracted with a different body (likely a merge needed).
    by_key: dict[str, list[CandidateRule]] = defaultdict(list)
    for r in rules:
        by_key[r.rule_key].append(r)
    for key, group in by_key.items():
        bodies = {_rule_body(g) for g in group}
        if len(bodies) > 1:
            conflicts.append(
                Conflict(
                    conflict_id=new_id(),
                    severity="medium",
                    type="duplicate_rule_key",
                    rules=[key],
                    message=f"rule_key '{key}' was extracted with conflicting bodies.",
                    recommended_action="Rename one rule or merge them.",
                )
            )

    # 4. Overlapping numeric thresholds with different effects on the same field.
    numeric_by_field: dict[str, list[tuple[CandidateRule, Any]]] = defaultdict(list)
    for r, cond in entries:
        if cond.operator in _NUMERIC_OPS and _is_number(cond.value):
            numeric_by_field[cond.field].append((r, cond))
    for fld, group in numeric_by_field.items():
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                (ra, ca), (rb, cb) = group[i], group[j]
                if ra.rule_key == rb.rule_key or ra.effect.decision == rb.effect.decision:
                    continue
                if _thresholds_overlap(ca, cb):
                    conflicts.append(
                        Conflict(
                            conflict_id=new_id(),
                            severity="high",
                            type="threshold_overlap",
                            rules=sorted({ra.rule_key, rb.rule_key}),
                            message=(
                                f"Overlapping thresholds on '{fld}' produce different "
                                f"effects ({ra.effect.decision} vs {rb.effect.decision})."
                            ),
                            recommended_action="Clarify precedence or make the ranges disjoint.",
                        )
                    )

    # 5. Exception rule with no plausible base rule on any of its fields.
    base_fields = {c.field for r in rules if r.rule_type != "exception" for c in r.conditions}
    for r in rules:
        if r.rule_type == "exception" and not any(
            c.field in base_fields for c in r.conditions
        ):
            conflicts.append(
                Conflict(
                    conflict_id=new_id(),
                    severity="medium",
                    type="orphan_exception",
                    rules=[r.rule_key],
                    message=f"Exception '{r.rule_key}' has no base rule on its field(s).",
                    recommended_action="Add the base rule the exception modifies, or reclassify it.",
                )
            )

    return conflicts


def _rule_body(r: CandidateRule) -> tuple:
    """A hashable signature of a rule's logic, for duplicate detection."""
    conds = tuple(
        sorted((c.field, c.operator, _hashable(c.value)) for c in r.conditions)
    )
    return (conds, r.effect.decision)


def _hashable(v):
    """Make condition values hashable for grouping (lists -> tuples)."""
    if isinstance(v, list):
        return tuple(_hashable(x) for x in v)
    if isinstance(v, dict):
        return tuple(sorted((k, _hashable(val)) for k, val in v.items()))
    return v


def _is_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _thresholds_overlap(a, b) -> bool:
    """Do two single-bound numeric conditions admit a common value?"""
    ar = _range(a.operator, a.value)
    br = _range(b.operator, b.value)
    if ar is None or br is None:
        return False
    lo = max(ar[0], br[0])
    hi = min(ar[1], br[1])
    return lo <= hi


def _range(op: str, v: float):
    """Map an operator/value to an inclusive numeric interval."""
    inf = float("inf")
    if op == ">":
        return (v + 1e-9, inf)
    if op == ">=":
        return (v, inf)
    if op == "<":
        return (-inf, v - 1e-9)
    if op == "<=":
        return (-inf, v)
    if op == "==":
        return (v, v)
    return None
