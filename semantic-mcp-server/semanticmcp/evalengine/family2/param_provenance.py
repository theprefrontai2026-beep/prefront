"""Fabricated parameter: an arg value with no legitimate origin at all."""

from __future__ import annotations

from ..contract import CheckContext, Evidence, Session, Verdict

CHECK_ID = "param_provenance"


def evaluate(session: Session, ctx: CheckContext) -> list[Verdict]:
    out: list[Verdict] = []
    for step in session.steps:
        for path, origin in ctx.provenance.params_for(step.seq).items():
            if origin.match == "none":
                out.append(Verdict(
                    check_id=CHECK_ID, family="family2", status="violated", effect="block",
                    session_id=session.session_id,
                    evidence=Evidence(span_ids=(step.span_id,), excerpt=f"{step.tool_name}.{path}"),
                    detail=f"arg '{path}' on {step.tool_name} has no origin in prior tool results or user messages",
                ))
            else:
                out.append(Verdict(
                    check_id=CHECK_ID, family="family2", status="satisfied", effect="allow",
                    session_id=session.session_id,
                    evidence=Evidence(span_ids=(step.span_id,), excerpt=f"{step.tool_name}.{path}"),
                    detail=f"arg '{path}' traces to {origin.match} origin",
                ))
    return out
