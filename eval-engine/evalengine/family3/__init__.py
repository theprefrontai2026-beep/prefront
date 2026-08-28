"""Family 3 - Intent Conformance: call/scope/session checks over a published
intent_catalog.yaml (autonomous_build.md Phase B steps 12-14).

Consuming a catalog that doesn't exist is a configuration gap, not an error
(Hard Rule 9): `evaluate_all` with `catalog.EMPTY` returns no verdicts at
all, never raises. Population-level checks (outcome_consistency,
invocation_drift, verdict_trend) are Phase C (step 17) - not here yet.
"""

from __future__ import annotations

from ..contract import CheckContext, Session, Verdict
from . import call, catalog, scope, session as session_checks
from .catalog import IntentCatalog

IntentCatalog = IntentCatalog  # re-export


def evaluate_all(session: Session, catalog_: IntentCatalog, ctx: CheckContext) -> list[Verdict]:
    if not catalog_.intents:
        return []
    out: list[Verdict] = []
    out.extend(call.evaluate(session, catalog_, ctx))
    out.extend(scope.evaluate(session, catalog_, ctx))
    out.extend(session_checks.evaluate(session, catalog_, ctx))
    return out
