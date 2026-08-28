"""Family 2 - Integrity Invariants: built-in, no artifacts required.

Every module here exports CHECK_ID and evaluate(session, ctx) -> list[Verdict].
CHECKS is the registry the worker/combinator iterate - the only place that
enumerates "every Family 2 check" (Hard Rule 9: these run with zero artifacts
configured, day one).
"""

from __future__ import annotations

from ..contract import CheckContext, Session, Verdict
from . import (
    approval_evidence,
    entity_consistency,
    error_blindness,
    minimization,
    param_discard,
    param_mutation,
    param_provenance,
    param_staleness,
    param_taint,
    result_fidelity,
)

CHECKS = (
    param_provenance,
    param_mutation,
    param_discard,
    param_taint,
    param_staleness,
    entity_consistency,
    result_fidelity,
    error_blindness,
    approval_evidence,
    minimization,
)


def evaluate_all(session: Session, ctx: CheckContext) -> list[Verdict]:
    out: list[Verdict] = []
    for mod in CHECKS:
        out.extend(mod.evaluate(session, ctx))
    return out
