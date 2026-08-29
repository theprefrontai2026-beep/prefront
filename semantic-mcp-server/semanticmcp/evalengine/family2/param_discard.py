"""Dropped constraint: a constraint supplied upstream never reached the call -
over-broad retrieval. Two shapes, both generic:

1. Repeated call to the same tool drops a top-level arg key an earlier call
   supplied. Needs at least two calls to the same tool_name (Hard Rule 16).

2. The user's message names a value that the tool's own result shows is a
   filterable column value - the rows carry a column K whose value appears
   verbatim in the user's words - yet the call passed no K, AND the rows
   include other values of K (so the constraint demonstrably wasn't applied).
   "Show my pending applications" -> a call with no `status` arg whose rows
   are a mix of pending/approved/rejected. Purely structural: the column and
   value vocabulary come from the tool's result, never from this file.
"""

from __future__ import annotations

import re
from collections import defaultdict

from ..contract import CheckContext, Evidence, Session, Verdict

CHECK_ID = "param_discard"

_MIN_TOKEN = 3


def _rows(result) -> list[dict]:
    if isinstance(result, dict) and isinstance(result.get("rows"), list):
        return [r for r in result["rows"] if isinstance(r, dict)]
    if isinstance(result, list):
        return [r for r in result if isinstance(r, dict)]
    return []


def _user_message_for(session: Session, step) -> str:
    if step.turn_seq is None:
        return ""
    for t in session.turns:
        if t.seq == step.turn_seq:
            return t.user_message or ""
    return ""


def _dropped_user_constraints(session: Session, step) -> list[tuple[str, str]]:
    """(column, value) pairs the user named that the call didn't filter on."""
    text = _user_message_for(session, step).lower()
    rows = _rows(step.result)
    if not text or len(rows) < 2:
        return []
    # A call that passed NO arguments at all can't have "dropped" a filter: it
    # is either a parameterless, identity-scoped retrieval (get_my_applications
    # returns the caller's own rows; narrowing is a presentation concern) or a
    # deliberate bulk fetch. Only a call that carried at least one arg proves
    # the tool is parameterized and the agent filtered selectively - which is
    # what makes an ABSENT user constraint a genuine dropped constraint. Caught
    # live: a clean baseline ("which of my applications are pending?" ->
    # get_my_applications() -> a mix of statuses) was flagged without this gate.
    passed_args = {k: v for k, v in (step.args or {}).items() if v not in (None, "", [], {})}
    if not passed_args:
        return []
    args = {k.lower() for k in passed_args}
    found: list[tuple[str, str]] = []
    columns = {k for r in rows for k in r}
    for col in sorted(columns):
        if col.lower() in args or col.lower().endswith("_id"):
            continue
        values = {str(r.get(col)).strip().lower() for r in rows if isinstance(r.get(col), str) and r.get(col)}
        if len(values) < 2:
            continue  # every row already agrees - no constraint to have dropped
        for v in sorted(values):
            if len(v) >= _MIN_TOKEN and re.search(r"\b" + re.escape(v) + r"\b", text):
                found.append((col, v))
                break
    return found


def evaluate(session: Session, ctx: CheckContext) -> list[Verdict]:
    out: list[Verdict] = []

    # Shape 2: user-named constraint absent from the call, rows unfiltered.
    for step in session.steps:
        dropped = _dropped_user_constraints(session, step)
        if not dropped:
            continue
        out.append(Verdict(
            check_id=CHECK_ID, family="family2", status="violated", effect="approval_required",
            session_id=session.session_id,
            evidence=Evidence(span_ids=(step.span_id,), excerpt=f"{step.tool_name}: user constraint(s) {dropped}"),
            detail=(f"{step.tool_name} (step {step.seq}) ignored the user's constraint(s) "
                    + ", ".join(f"{c}={v!r}" for c, v in dropped)
                    + " - no such arg was passed and the result mixes other values"),
        ))

    # Shape 1: a repeat of the same tool drops an earlier call's key.
    by_tool: dict[str, list] = defaultdict(list)
    for step in session.steps:
        by_tool[step.tool_name].append(step)
    for tool_name, steps in by_tool.items():
        if len(steps) < 2:
            continue
        steps = sorted(steps, key=lambda s: s.seq)
        for earlier, later in zip(steps, steps[1:]):
            earlier_keys = {k for k, v in earlier.args.items() if v not in (None, "", [])}
            later_keys = {k for k, v in later.args.items() if v not in (None, "", [])}
            dropped = sorted(earlier_keys - later_keys)
            if dropped:
                out.append(Verdict(
                    check_id=CHECK_ID, family="family2", status="violated", effect="approval_required",
                    session_id=session.session_id,
                    evidence=Evidence(span_ids=(earlier.span_id, later.span_id),
                                      excerpt=f"{tool_name}: dropped {dropped}"),
                    detail=f"{tool_name} call at step {later.seq} omits constraint(s) {dropped} "
                          f"supplied at step {earlier.seq}",
                ))
            else:
                out.append(Verdict(
                    check_id=CHECK_ID, family="family2", status="satisfied", effect="allow",
                    session_id=session.session_id,
                    evidence=Evidence(span_ids=(earlier.span_id, later.span_id), excerpt=tool_name),
                    detail=f"{tool_name} call at step {later.seq} carries forward step {earlier.seq}'s constraints",
                ))
    return out
