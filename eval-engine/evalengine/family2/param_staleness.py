"""Stale parameter: value sourced from a step whose data was superseded by a
later re-invocation of the same tool, before this call used the old value.

Applicable only when the originating tool was actually re-invoked between the
source step and this use (Hard Rule 16) - otherwise there is nothing to be
stale relative to.
"""

from __future__ import annotations

from ..contract import CheckContext, Evidence, Session, Verdict
from ..provenance import flatten

CHECK_ID = "param_staleness"


def evaluate(session: Session, ctx: CheckContext) -> list[Verdict]:
    by_seq = {s.seq: s for s in session.steps}
    out: list[Verdict] = []
    for step in session.steps:
        for path, origin in ctx.provenance.params_for(step.seq).items():
            c = origin.candidate
            if c is None or c.origin != "tool_result" or c.step_seq is None:
                continue
            src = by_seq.get(c.step_seq)
            if src is None:
                continue
            refreshes = [
                s for s in session.steps
                if s.tool_name == src.tool_name and src.seq < s.seq < step.seq
            ]
            if not refreshes:
                continue  # never re-invoked between source and use: not applicable
            stale = False
            newest = None
            for r in refreshes:
                for rpath, rvalue in flatten(r.result, "result"):
                    if rpath == c.path and rvalue != c.value:
                        stale = True
                        newest = r
                        break
                if stale:
                    break
            span_ids = (src.span_id, step.span_id) if newest is None else (src.span_id, newest.span_id, step.span_id)
            if stale:
                out.append(Verdict(
                    check_id=CHECK_ID, family="family2", status="violated", effect="approval_required",
                    session_id=session.session_id,
                    evidence=Evidence(span_ids=span_ids, excerpt=f"{step.tool_name}.{path}"),
                    detail=(f"arg '{path}' on {step.tool_name} (step {step.seq}) reuses step {src.seq}'s "
                            f"{c.path}, superseded by step {newest.seq if newest else '?'}"),
                ))
            else:
                out.append(Verdict(
                    check_id=CHECK_ID, family="family2", status="satisfied", effect="allow",
                    session_id=session.session_id,
                    evidence=Evidence(span_ids=span_ids, excerpt=f"{step.tool_name}.{path}"),
                    detail=f"arg '{path}' confirmed unchanged across re-invocation of {src.tool_name}",
                ))
    return out
