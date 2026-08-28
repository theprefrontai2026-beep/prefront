"""Family 1 - Learnt Rules: temporal | predicate | content engines over a
published rule_pack.yaml (autonomous_build.md Phase B step 10).

Consuming a rule pack that doesn't exist is a configuration gap, not an
error (Hard Rule 9): `evaluate_all` with `rule_pack.EMPTY` returns no
verdicts at all, never raises.
"""

from __future__ import annotations

from ..contract import CheckContext, Session, Verdict
from . import compilepack, content, predicate, temporal
from .compilepack import RulePack

RulePack = RulePack  # re-export


def evaluate_all(session: Session, rule_pack: RulePack, ctx: CheckContext) -> list[Verdict]:
    if not rule_pack.rules:
        return []
    out: list[Verdict] = []
    out.extend(temporal.evaluate(session, rule_pack, ctx))
    out.extend(predicate.evaluate(session, rule_pack, ctx))
    out.extend(content.evaluate(session, rule_pack, ctx))
    return out
