"""Temporal engine: per-rule automata over the session's step stream -
precondition ("before tool T fires, fact F must already be established").

No rule_type in skill-builder's current schema lowers to this engine (see
skill-builder/skillbuilder/rulepack.py's docstring) - this is real, generic
machinery waiting for a rule source that expresses ordering, not dead code.
Only `automaton: {before: {intent | intents}, requires_fact}` is implemented;
`intent` names one target ("*" = every step), `intents` a list of targets -
there is no `after` variant because nothing in prefront-check-families.md's
examples needs one and speculative syntax nobody can trigger isn't worth
carrying.

A fact is "established" by an earlier step in the SAME session: either that
step's own intent equals requires_fact (the fact IS having called that
intent), or one of its result leaves is a truthy value at a key matching
requires_fact.
"""

from __future__ import annotations

from ..contract import CheckContext, Evidence, Session, Verdict
from ..provenance import flatten
from .compilepack import Rule, RulePack


def _fact_established_by(step, requires_fact: str) -> bool:
    if step.intent == requires_fact or step.tool_name == requires_fact:
        return True
    for path, value in flatten(step.result):
        leaf = path.rsplit(".", 1)[-1].split("[")[0]
        if leaf == requires_fact and value:
            return True
    return False


def _targets(before: dict) -> tuple[str, tuple[str, ...]]:
    if "intents" in before:
        return "", tuple(before["intents"])
    return before.get("intent", "*"), ()


def _matches(step, target_intent: str, target_intents: tuple[str, ...]) -> bool:
    if target_intents:
        return step.intent in target_intents
    return target_intent == "*" or step.intent == target_intent


def evaluate(session: Session, rule_pack: RulePack, ctx: CheckContext) -> list[Verdict]:
    out: list[Verdict] = []
    for rule in rule_pack.by_engine("temporal"):
        before = (rule.automaton or {}).get("before") or {}
        target_intent, target_intents = _targets(before)
        requires_fact = (rule.automaton or {}).get("requires_fact", "")
        if not requires_fact:
            continue
        for step in session.steps:
            if not _matches(step, target_intent, target_intents):
                continue
            established = any(
                _fact_established_by(prior, requires_fact)
                for prior in session.steps if prior.seq < step.seq
            )
            evidence = Evidence(span_ids=(step.span_id,), excerpt=f"{step.tool_name}: {rule.rule_id}")
            if established:
                out.append(Verdict(
                    check_id=rule.check_id(), family="family1", status="satisfied", effect="allow",
                    session_id=session.session_id, evidence=evidence, rule_id=rule.rule_id,
                    detail=f"rule {rule.rule_id}: '{requires_fact}' established before {step.tool_name}",
                    source=rule.source or None,
                ))
            else:
                out.append(Verdict(
                    check_id=rule.check_id(), family="family1", status="violated", effect=rule.effect,
                    session_id=session.session_id, evidence=evidence, rule_id=rule.rule_id,
                    detail=f"rule {rule.rule_id}: {step.tool_name} fired without '{requires_fact}' established first",
                    source=rule.source or None,
                ))
    return out
