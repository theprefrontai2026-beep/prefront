"""Distorted parameter: value has an origin but was altered en route beyond
the whitelisted-transform tolerance (rounding, unit/currency, sign, ...)."""

from __future__ import annotations

from ..contract import CheckContext, Evidence, Session, Verdict

CHECK_ID = "param_mutation"


def evaluate(session: Session, ctx: CheckContext) -> list[Verdict]:
    out: list[Verdict] = []
    for step in session.steps:
        for path, origin in ctx.provenance.params_for(step.seq).items():
            if origin.candidate is None:
                continue  # nothing to compare drift against (identity/bool/no-origin)
            if origin.match == "mutated":
                out.append(Verdict(
                    check_id=CHECK_ID, family="family2", status="violated", effect="block",
                    session_id=session.session_id,
                    evidence=Evidence(span_ids=(step.span_id,), excerpt=f"{step.tool_name}.{path}"),
                    detail=(f"arg '{path}' on {step.tool_name} is a near-miss "
                            f"({origin.transform}, delta={origin.delta:.4g}) of its nearest candidate origin "
                            "- beyond whitelisted-transform tolerance"),
                ))
            else:
                out.append(Verdict(
                    check_id=CHECK_ID, family="family2", status="satisfied", effect="allow",
                    session_id=session.session_id,
                    evidence=Evidence(span_ids=(step.span_id,), excerpt=f"{step.tool_name}.{path}"),
                    detail=f"arg '{path}' unaltered from origin ({origin.match})",
                ))
    return out
