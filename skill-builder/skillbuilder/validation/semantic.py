"""Semantic validator — vocabulary checks against the domain pack.

Confirms that the symbols a rule uses exist in the pack's vocabulary: condition
fields are known, the approver role resolves to a known role, and enum-valued
conditions use allowed values. Gaps become unresolved items (unknown_role,
unmappable_symbol, vague_condition). No pack -> nothing to check (returns valid).
"""

from __future__ import annotations

from ..schema import CandidateRule


def check(rule: CandidateRule, pack) -> tuple[bool, list[dict]]:
    if pack is None:
        return True, []
    problems: list[dict] = []

    role = rule.effect.approver_role
    if role:
        for part in _approver_parts(str(role), pack):
            if pack.resolve_role(part) is None:
                problems.append({
                    "type": "unknown_role",
                    "severity": "high",
                    "issue": f"approver role {part!r} is not in the domain pack",
                    "recommended_action": "map the role or add it to the pack",
                })

    for c in rule.conditions:
        field = str(c.field).strip()
        if field.startswith("caller."):
            continue
        canon = pack.field_canonical(field)
        if canon is None:
            # executability already reports unmappable symbols; flag the vague
            # vocabulary mismatch here at lower severity for the reviewer.
            problems.append({
                "type": "vague_condition",
                "severity": "medium",
                "issue": f"field {field!r} is not in the domain pack vocabulary",
                "recommended_action": "add an alias/field to the pack or rename it",
            })
            continue
        allowed = pack.allowed_values(field)
        if allowed is not None and not _value_in(c.value, allowed):
            problems.append({
                "type": "vague_condition",
                "severity": "medium",
                "issue": f"value {c.value!r} for {canon!r} is not in {allowed}",
                "recommended_action": "use one of the allowed enum values",
            })

    return (len(problems) == 0, problems)


def _approver_parts(role: str, pack) -> list[str]:
    """Split a comma-joined approver list — but a role name can itself contain a
    comma ("Director, Credit & Collections"). Resolve the whole string first;
    only split if that fails."""
    role = role.strip()
    if not role:
        return []
    if pack.resolve_role(role) is not None:
        return [role]
    return [p.strip() for p in role.split(",") if p.strip()]


def _value_in(value, allowed: list) -> bool:
    al = {str(a).lower() for a in allowed}
    items = value if isinstance(value, list) else [value]
    return all(str(v).lower() in al for v in items)
