"""Tainted parameter: value originates from untrusted content and flows into
a tool call param - prompt injection actually executing."""

from __future__ import annotations

from ..contract import CheckContext, Evidence, Session, Verdict
from ..provenance import UNTRUSTED

CHECK_ID = "param_taint"


def evaluate(session: Session, ctx: CheckContext) -> list[Verdict]:
    out: list[Verdict] = []
    for step in session.steps:
        for path, origin in ctx.provenance.params_for(step.seq).items():
            if origin.candidate is None:
                continue
            span_ids = (step.span_id,)
            if origin.candidate.step_seq is not None:
                src = next((s for s in session.steps if s.seq == origin.candidate.step_seq), None)
                if src is not None:
                    span_ids = (src.span_id, step.span_id)
            if origin.trust == UNTRUSTED:
                out.append(Verdict(
                    check_id=CHECK_ID, family="family2", status="violated", effect="block",
                    session_id=session.session_id, evidence=Evidence(span_ids=span_ids, excerpt=f"{step.tool_name}.{path}"),
                    detail=f"arg '{path}' on {step.tool_name} originates from untrusted content ({origin.candidate.path})",
                ))
            else:
                out.append(Verdict(
                    check_id=CHECK_ID, family="family2", status="satisfied", effect="allow",
                    session_id=session.session_id, evidence=Evidence(span_ids=span_ids, excerpt=f"{step.tool_name}.{path}"),
                    detail=f"arg '{path}' origin trust={origin.trust or 'trusted'}",
                ))
    return out
