"""Content engine: detector sets over tool results / the final answer -
field_restriction / output-prohibition rules ("tax_id must never appear").

Applicable to every step whose intent is in the rule's applies_to_intents (or
every step if that list is empty) - the rule was exercised because the
governed intent fired, independent of whether the restricted field happened
to be present that time.
"""

from __future__ import annotations

import re

from ..contract import CheckContext, Evidence, Session, Verdict
from ..provenance import flatten
from .compilepack import Rule, RulePack


def _normalize(name: str) -> str:
    return re.sub(r"[\s_-]+", "", name.strip().lower())


def _field_in_result(result, field_name: str) -> bool:
    target = _normalize(field_name)
    for path, _ in flatten(result):
        leaf = path.rsplit(".", 1)[-1].split("[")[0]
        if _normalize(leaf) == target:
            return True
    return False


def _field_in_text(text: str, field_name: str) -> bool:
    if not text:
        return False
    # "credit_score" must also match "credit score" / "credit-score" in prose.
    # Built from the name's own word parts - NOT via re.escape().replace(r"\_",
    # ...): re.escape stopped escaping "_" in Python 3.7, so that rewrite
    # silently never matched and a final answer saying "credit score of 700"
    # sailed past a credit_score detector (caught live on a scripted scenario).
    parts = [re.escape(p) for p in re.split(r"[\s_-]+", field_name.strip()) if p]
    if not parts:
        return False
    pattern = r"\b" + r"[\s_-]?".join(parts) + r"\b"
    return re.search(pattern, text, re.IGNORECASE) is not None


def _applies(rule: Rule, step, session: Session) -> bool:
    if rule.applies_to_intents and step.intent not in rule.applies_to_intents:
        return False
    if rule.restricted_from_roles and session.caller_role not in rule.restricted_from_roles:
        return False
    return True


def _restricted_field_names(rule: Rule) -> list[str]:
    return [f for det in rule.detectors for f in (det.get("field_names") or [])]


def evaluate_substitution(session: Session, rule_pack: RulePack, ctx: CheckContext) -> list[Verdict]:
    """Substitution obligations: the POSITIVE half of a restriction rule
    (autonomous_build.md step 26).

    `field_restriction` answers "did the forbidden field appear?". It cannot
    answer "did the thing that should stand in its place appear?", so a policy
    shaped "not X, but Y instead" was only half enforceable: an agent that
    returned NEITHER the restricted value nor its substitute produced no
    finding at all, and a correct substitution registered only as the absence
    of a violation rather than as positive evidence.

    Applicability is deliberately narrow (Hard Rule 16) - all four must hold,
    or the check emits nothing:
      1. the rule declares `required_substitute` (opt-in per rule);
      2. the caller is in `restricted_from_roles`, so the restriction binds;
      3. the restricted field was actually present in a tool RESULT - the agent
         HELD the data and had to choose what to surface. Without this the
         check would fire on every session that never asked the question;
      4. there is an assistant answer for that step's turn to judge.

    Effect is `flag`, not the rule's own (usually `block`) effect: withholding
    a restricted value but supplying nothing in its place is an incomplete
    answer, not a disclosure breach. The breach, if any, is already reported by
    `field_restriction` on the same rule.
    """
    turns_by_seq = {t.seq: t for t in session.turns}
    out: list[Verdict] = []
    for rule in rule_pack.by_engine("content"):
        if not rule.required_substitute:
            continue
        if rule.restricted_from_roles and session.caller_role not in rule.restricted_from_roles:
            continue
        fields = _restricted_field_names(rule)
        for step in session.steps:
            if not _applies(rule, step, session):
                continue
            if not any(_field_in_result(step.result, f) for f in fields):
                continue  # the agent never held the restricted value here
            turn = turns_by_seq.get(step.turn_seq) if step.turn_seq is not None else None
            answer = (turn.assistant_message if turn else "") or session.final_answer
            if not answer:
                continue  # nothing to judge yet
            found = [s for s in rule.required_substitute if _field_in_text(answer, s)]
            evidence = Evidence(span_ids=(step.span_id,), excerpt=f"{step.tool_name}: {rule.rule_id}")
            if found:
                out.append(Verdict(
                    check_id="substitution", family="family1", status="satisfied", effect="allow",
                    session_id=session.session_id, evidence=evidence, rule_id=rule.rule_id,
                    detail=(f"rule {rule.rule_id}: answer supplies the required substitute "
                            f"{sorted(found)!r} in place of {sorted(set(fields))!r}"),
                    source=rule.source or None,
                ))
            else:
                out.append(Verdict(
                    check_id="substitution", family="family1", status="violated", effect="flag",
                    session_id=session.session_id, evidence=evidence, rule_id=rule.rule_id,
                    detail=(f"rule {rule.rule_id}: {step.tool_name} returned restricted field(s) "
                            f"{sorted(set(fields))!r} to a {session.caller_role}, but the answer "
                            f"supplies none of the required substitute(s) "
                            f"{sorted(rule.required_substitute)!r}"),
                    source=rule.source or None,
                ))
    return out


def evaluate(session: Session, rule_pack: RulePack, ctx: CheckContext) -> list[Verdict]:
    turns_by_seq = {t.seq: t for t in session.turns}
    out: list[Verdict] = []
    for rule in rule_pack.by_engine("content"):
        for step in session.steps:
            if not _applies(rule, step, session):
                continue
            hits: list[str] = []
            for det in rule.detectors:
                field_names = det.get("field_names") or []
                scopes = det.get("scopes") or ["result"]
                for fname in field_names:
                    if "result" in scopes and _field_in_result(step.result, fname):
                        hits.append(f"{fname} (result)")
                    turn = turns_by_seq.get(step.turn_seq) if step.turn_seq is not None else None
                    if "final_answer" in scopes and turn and _field_in_text(turn.assistant_message, fname):
                        hits.append(f"{fname} (final answer)")
            evidence = Evidence(span_ids=(step.span_id,), excerpt=f"{step.tool_name}: {rule.rule_id}")
            if hits:
                out.append(Verdict(
                    check_id=rule.check_id(), family="family1", status="violated", effect=rule.effect,
                    session_id=session.session_id, evidence=evidence, rule_id=rule.rule_id,
                    detail=f"rule {rule.rule_id}: restricted field(s) {sorted(set(hits))} surfaced on {step.tool_name}",
                    source=rule.source or None,
                ))
            else:
                out.append(Verdict(
                    check_id=rule.check_id(), family="family1", status="satisfied", effect="allow",
                    session_id=session.session_id, evidence=evidence, rule_id=rule.rule_id,
                    detail=f"rule {rule.rule_id}: no restricted field surfaced on {step.tool_name}",
                    source=rule.source or None,
                ))
    return out
