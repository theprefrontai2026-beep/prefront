"""Ignored failure: a tool errored and the turn's answer shows no sign the
agent noticed - proceeded as if the call had succeeded.

Applicable only once the turn containing the failed call has an answer
(Hard Rule 16) - a call that errored mid-turn, before the agent has replied
at all, is not yet judgeable.
"""

from __future__ import annotations

from ..contract import CheckContext, Evidence, Session, Verdict

CHECK_ID = "error_blindness"

_ACK_MARKERS = (
    "error", "fail", "couldn't", "cannot", "can't", "unable", "not found",
    "sorry", "issue", "problem", "try again", "unavailable", "denied",
)


def evaluate(session: Session, ctx: CheckContext) -> list[Verdict]:
    turns_by_seq = {t.seq: t for t in session.turns}
    out: list[Verdict] = []
    for step in session.steps:
        empty_result = step.result in (None, "", {}, [])
        if step.status != "ERROR" and not empty_result:
            continue
        if step.turn_seq is None:
            continue
        turn = turns_by_seq.get(step.turn_seq)
        if turn is None or not turn.assistant_message:
            continue  # no answer yet for this turn - not yet judgeable
        acknowledged = any(m in turn.assistant_message.lower() for m in _ACK_MARKERS)
        reason = "errored" if step.status == "ERROR" else "returned empty"
        if acknowledged:
            out.append(Verdict(
                check_id=CHECK_ID, family="family2", status="satisfied", effect="allow",
                session_id=session.session_id,
                evidence=Evidence(span_ids=(step.span_id, turn.span_id), excerpt=f"{step.tool_name} {reason}"),
                detail=f"turn {turn.seq} answer acknowledges {step.tool_name} {reason}",
            ))
        else:
            out.append(Verdict(
                check_id=CHECK_ID, family="family2", status="violated", effect="approval_required",
                session_id=session.session_id,
                evidence=Evidence(span_ids=(step.span_id, turn.span_id), excerpt=f"{step.tool_name} {reason}"),
                detail=f"turn {turn.seq} answer proceeds without acknowledging {step.tool_name} {reason}",
            ))
    return out
