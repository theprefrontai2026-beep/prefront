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

CHECK_ID = "content"


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
    pattern = re.escape(field_name).replace(r"\_", r"[\s_-]?").replace(r"\-", r"[\s_-]?")
    return re.search(pattern, text, re.IGNORECASE) is not None


def _applies(rule: Rule, step) -> bool:
    return not rule.applies_to_intents or step.intent in rule.applies_to_intents


def evaluate(session: Session, rule_pack: RulePack, ctx: CheckContext) -> list[Verdict]:
    turns_by_seq = {t.seq: t for t in session.turns}
    out: list[Verdict] = []
    for rule in rule_pack.by_engine("content"):
        for step in session.steps:
            if not _applies(rule, step):
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
                    check_id=CHECK_ID, family="family1", status="violated", effect=rule.effect,
                    session_id=session.session_id, evidence=evidence, rule_id=rule.rule_id,
                    detail=f"rule {rule.rule_id}: restricted field(s) {sorted(set(hits))} surfaced on {step.tool_name}",
                    source=rule.source or None,
                ))
            else:
                out.append(Verdict(
                    check_id=CHECK_ID, family="family1", status="satisfied", effect="allow",
                    session_id=session.session_id, evidence=evidence, rule_id=rule.rule_id,
                    detail=f"rule {rule.rule_id}: no restricted field surfaced on {step.tool_name}",
                    source=rule.source or None,
                ))
    return out
