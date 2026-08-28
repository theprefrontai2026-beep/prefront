"""Entity confusion: a call's subject differs from the subject the session
already established for that identifier slot.

Generic, not domain-specific: any arg key ending in "_id" (other than the
trusted-layer session_id/user_id) is an identifier slot. The first value seen
for a given slot in the session establishes that slot's subject; a later step
using a DIFFERENT value for the same slot is the finding (Hard Rule 16: a
slot used only once has nothing to be inconsistent with, so it is skipped).
"""

from __future__ import annotations

from ..contract import CheckContext, Evidence, Session, Verdict
from ..provenance import flatten

CHECK_ID = "entity_consistency"

_IGNORED_SUFFIXES = ("session_id", "user_id")


def _id_slot(path: str) -> str:
    leaf = path.rsplit(".", 1)[-1].split("[")[0]
    return leaf


def evaluate(session: Session, ctx: CheckContext) -> list[Verdict]:
    established: dict[str, tuple[object, object]] = {}  # slot -> (value, step)
    out: list[Verdict] = []
    for step in session.steps:
        for path, value in flatten(step.args):
            slot = _id_slot(path)
            if not slot.endswith("_id") or slot in _IGNORED_SUFFIXES:
                continue
            if value in (session.session_id, session.user_id):
                continue
            prior = established.get(slot)
            if prior is None:
                established[slot] = (value, step)
                continue
            prior_value, prior_step = prior
            if value == prior_value:
                out.append(Verdict(
                    check_id=CHECK_ID, family="family2", status="satisfied", effect="allow",
                    session_id=session.session_id,
                    evidence=Evidence(span_ids=(prior_step.span_id, step.span_id), excerpt=f"{slot}={value!r}"),
                    detail=f"'{slot}' consistent with step {prior_step.seq}",
                ))
            else:
                out.append(Verdict(
                    check_id=CHECK_ID, family="family2", status="violated", effect="block",
                    session_id=session.session_id,
                    evidence=Evidence(span_ids=(prior_step.span_id, step.span_id),
                                      excerpt=f"{slot}: {prior_value!r} -> {value!r}"),
                    detail=(f"{step.tool_name} (step {step.seq}) uses '{slot}'={value!r}, "
                            f"session established {prior_value!r} at step {prior_step.seq}"),
                ))
    return out
