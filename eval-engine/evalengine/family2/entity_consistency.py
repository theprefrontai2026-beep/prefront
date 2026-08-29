"""Entity confusion: a call's subject differs from the subject the session
already established for that identifier slot.

Generic, not domain-specific: any arg key ending in "_id" (other than the
trusted-layer session_id/user_id) is an identifier slot. The first value seen
for a given slot in the session establishes that slot's subject; a later step
using a DIFFERENT value for the same slot is the finding (Hard Rule 16: a
slot used only once has nothing to be inconsistent with, so it is skipped).

Subjects are read from ARGS and from SINGLE-ROW RESULTS. The result side is
what catches the classic confusion - the agent looked up applicant A, then
decided a loan that belongs to applicant B: the loan_id arg is a different
slot from applicant_id, so args alone never compare them, but the decision
tool's own result names the applicant the loan resolved to. Multi-row results
(a listing) are ignored - many subjects is the point of a list, not a
contradiction.
"""

from __future__ import annotations

from ..contract import CheckContext, Evidence, Session, Verdict
from ..provenance import flatten

CHECK_ID = "entity_consistency"

_IGNORED_SUFFIXES = ("session_id", "user_id")


def _id_slot(path: str) -> str:
    leaf = path.rsplit(".", 1)[-1].split("[")[0]
    return leaf


def _single_row_result(result) -> object:
    """The one row a single-row result carries, else None. Understands both a
    bare dict and the {columns, rows:[...]} shape a tabular tool returns."""
    if isinstance(result, dict):
        rows = result.get("rows")
        if isinstance(rows, list):
            return rows[0] if len(rows) == 1 and isinstance(rows[0], dict) else None
        return result
    return None


def evaluate(session: Session, ctx: CheckContext) -> list[Verdict]:
    established: dict[str, tuple[object, object]] = {}  # slot -> (value, step)
    out: list[Verdict] = []
    for step in session.steps:
        sources = [("arg", flatten(step.args))]
        row = _single_row_result(step.result)
        if row is not None:
            sources.append(("result", flatten(row)))
        # Compare each slot once per step even if args and result both name it.
        seen_slots: set[str] = set()
        for where, pairs in sources:
            for path, value in pairs:
                slot = _id_slot(path)
                if not slot.endswith("_id") or slot in _IGNORED_SUFFIXES or slot in seen_slots:
                    continue
                if value in (session.session_id, session.user_id):
                    continue
                seen_slots.add(slot)
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
                        detail=(f"{step.tool_name} (step {step.seq}) {'resolves to' if where == 'result' else 'uses'} "
                                f"'{slot}'={value!r}, session established {prior_value!r} at step {prior_step.seq}"),
                    ))
    return out
