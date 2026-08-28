"""Dropped constraint: a repeated call to the same tool drops a top-level arg
key an earlier call in this session supplied - over-broad retrieval.

Applicability requires at least two calls to the same tool_name in the
session (Hard Rule 16): a tool called exactly once has nothing to compare
its argument shape against, so it is never checked.
"""

from __future__ import annotations

from collections import defaultdict

from ..contract import CheckContext, Evidence, Session, Verdict

CHECK_ID = "param_discard"


def evaluate(session: Session, ctx: CheckContext) -> list[Verdict]:
    by_tool: dict[str, list] = defaultdict(list)
    for step in session.steps:
        by_tool[step.tool_name].append(step)

    out: list[Verdict] = []
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
