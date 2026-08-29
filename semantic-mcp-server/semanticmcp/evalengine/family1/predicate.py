"""Predicate engine: AND-combined field/operator/value conditions evaluated
against a per-call fact bag. No string parsing / eval() - each condition is
already structured (field, operator, value) by the rule-pack compiler, and
every operator is a plain Python comparison, never an arbitrary expression.

A rule with `approver_roles` is approval-gate shaped: when its conditions
fire, the check looks for an approval-shaped tool call the same way
family2.approval_evidence does, and - like that check - never guesses
"violated" when none is found (emits indeterminate; the combinator resolves
missing_precondition vs visibility_gap, Hard Rule 7). A rule with no
approver_roles is prohibition shaped: firing conditions ARE the violation.
"""

from __future__ import annotations

from typing import Any, Optional

from ..contract import CheckContext, Evidence, Session, Verdict
from .compilepack import Rule, RulePack
from .facts import build_facts


def _num(v: Any) -> Optional[float]:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    return None


def _cmp(operator: str, a: Any, b: Any) -> Optional[bool]:
    if operator == "==":
        return a == b
    if operator == "!=":
        return a != b
    if operator in (">", "<", ">=", "<="):
        na, nb = _num(a), _num(b)
        if na is None or nb is None:
            return None
        return {">": na > nb, "<": na < nb, ">=": na >= nb, "<=": na <= nb}[operator]
    if operator == "in":
        return a in b if isinstance(b, (list, tuple, set)) else None
    if operator == "not_in":
        return a not in b if isinstance(b, (list, tuple, set)) else None
    return None


def _rhs(c: dict[str, Any], facts: dict[str, Any]) -> tuple[Any, bool]:
    """The condition's right-hand side: a literal `value`, or a fact-relative
    `value_field` (optionally scaled by `value_multiplier`) for metric-style
    conditions ("requested_amount > 5x verified_income"). Returns (value, ok) -
    ok is False when a needed value_field is itself missing (indeterminate,
    same fail-safe treatment as a missing left-hand field)."""
    if "value_field" not in c:
        return c.get("value"), True
    field_name = str(c["value_field"])
    if field_name not in facts:
        return None, False
    base = _num(facts[field_name])
    if base is None:
        return None, False
    return base * float(c.get("value_multiplier", 1)), True


def _evaluate_conditions(conditions: tuple[dict[str, Any], ...], facts: dict[str, Any]) -> Optional[bool]:
    """AND-combined. None (indeterminate) if any needed symbol is missing -
    fail-safe, never silently resolved to True or False.

    Two operators are special-cased ahead of the "missing = indeterminate"
    rule: `exists`/`absent` ask whether a field is PRESENT at all, so absence
    is itself a determinate answer (a "must never appear" prohibition is
    trivially satisfied when the field never shows up - it is not unknown).
    """
    result = True
    for c in conditions:
        field_name = str(c.get("field", ""))
        operator = str(c.get("operator", "=="))
        if operator == "exists":
            outcome = field_name in facts
        elif operator == "absent":
            outcome = field_name not in facts
        else:
            if field_name not in facts:
                return None
            value, ok = _rhs(c, facts)
            if not ok:
                return None
            outcome = _cmp(operator, facts[field_name], value)
            if outcome is None:
                return None
        result = result and outcome
    return result


def _applies(rule: Rule, step) -> bool:
    return not rule.applies_to_intents or step.intent in rule.applies_to_intents


def _approval_backed(session: Session, up_to_turn: Optional[int]) -> bool:
    for s in session.steps:
        if "approv" in s.tool_name.lower() and (up_to_turn is None or s.turn_seq is None or s.turn_seq <= up_to_turn):
            return True
    return False


def _captures_approvals(ctx: CheckContext) -> bool:
    """Whether this trace source declares approval events captured (visibility
    profile). When it does, an approval-gated action with no approval event
    is a VIOLATION, not an open question; when it doesn't (or there is no
    profile at all - the inline reuse path), the check stays indeterminate
    and the combinator labels it a visibility gap (Hard Rule 7)."""
    vp = ctx.visibility_profile
    captured = getattr(vp, "captured", None)
    return bool(captured("approval_events")) if callable(captured) else False


def evaluate(session: Session, rule_pack: RulePack, ctx: CheckContext) -> list[Verdict]:
    out: list[Verdict] = []
    for rule in rule_pack.by_engine("predicate"):
        for step in session.steps:
            if not _applies(rule, step):
                continue
            facts = build_facts(step, session)
            fired = _evaluate_conditions(rule.conditions, facts)
            evidence = Evidence(span_ids=(step.span_id,), excerpt=f"{step.tool_name}: {rule.rule_id}")
            if fired is None:
                out.append(Verdict(
                    check_id=rule.check_id(), family="family1", status="indeterminate", effect=rule.effect,
                    session_id=session.session_id, evidence=evidence, rule_id=rule.rule_id,
                    detail=f"rule {rule.rule_id} condition symbol unresolved on {step.tool_name}",
                    source=rule.source or None,
                ))
            elif not fired:
                out.append(Verdict(
                    check_id=rule.check_id(), family="family1", status="satisfied", effect="allow",
                    session_id=session.session_id, evidence=evidence, rule_id=rule.rule_id,
                    detail=f"rule {rule.rule_id} condition did not hold on {step.tool_name}",
                    source=rule.source or None,
                ))
            elif rule.approver_roles:
                if _approval_backed(session, step.turn_seq):
                    out.append(Verdict(
                        check_id=rule.check_id(), family="family1", status="satisfied", effect="allow",
                        session_id=session.session_id, evidence=evidence, rule_id=rule.rule_id,
                        detail=f"rule {rule.rule_id} fired on {step.tool_name} and is approval-backed",
                        source=rule.source or None,
                    ))
                elif _captures_approvals(ctx):
                    # The trace source declares it captures approval events, so
                    # "none in the trace" is conclusive: the gated action ran
                    # without the approval the rule requires.
                    out.append(Verdict(
                        check_id=rule.check_id(), family="family1", status="violated", effect=rule.effect,
                        session_id=session.session_id, evidence=evidence, rule_id=rule.rule_id,
                        detail=f"rule {rule.rule_id} fired on {step.tool_name} with no approval event before it",
                        source=rule.source or None,
                    ))
                else:
                    out.append(Verdict(
                        check_id=rule.check_id(), family="family1", status="indeterminate", effect=rule.effect,
                        session_id=session.session_id, evidence=evidence, rule_id=rule.rule_id,
                        detail=f"rule {rule.rule_id} fired on {step.tool_name}; no approval event in trace",
                        missing_capture="approval_events", source=rule.source or None,
                    ))
            else:
                out.append(Verdict(
                    check_id=rule.check_id(), family="family1", status="violated", effect=rule.effect,
                    session_id=session.session_id, evidence=evidence, rule_id=rule.rule_id,
                    detail=f"rule {rule.rule_id} condition held on {step.tool_name}",
                    source=rule.source or None,
                ))
    return out
