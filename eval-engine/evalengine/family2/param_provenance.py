"""Fabricated parameter: an arg value with no legitimate origin at all.

Applicability split for a "no origin" value (Hard Rule 16 doesn't cover this
case directly - the check clearly applies to the param, the question is what
status to emit): prefront-check-families.md's own examples for this check
("agent invented an account ID, amount, rate") are all numeric-shaped
quantities/identifiers - values that, if genuine, MUST have come from
somewhere (a tool result, a user-stated figure), so an untraceable one is a
confident fabrication (violated/block). A non-numeric value with no origin
(an approval decision, a notice kind, free-text reasoning) is structurally
different: it may equally be the agent's own synthesized judgment, which by
design never traces to a prior value - the check cannot tell the two apart
from the value alone, so it reports indeterminate (Hard Rule 6/7: an honest
"can't tell" fail-safes to approval_required inline / missing_precondition
in OOB, never a silent pass and never a confident violation it can't back up).
"""

from __future__ import annotations

from ..contract import CheckContext, Evidence, Session, Verdict
from ..provenance import flatten, is_numeric_like

CHECK_ID = "param_provenance"


def evaluate(session: Session, ctx: CheckContext) -> list[Verdict]:
    out: list[Verdict] = []
    for step in session.steps:
        arg_values = dict(flatten(step.args))
        for path, origin in ctx.provenance.params_for(step.seq).items():
            if origin.match == "none":
                if is_numeric_like(arg_values.get(path)):
                    out.append(Verdict(
                        check_id=CHECK_ID, family="family2", status="violated", effect="block",
                        session_id=session.session_id,
                        evidence=Evidence(span_ids=(step.span_id,), excerpt=f"{step.tool_name}.{path}"),
                        detail=f"arg '{path}' on {step.tool_name} has no origin in prior tool "
                              "results or user messages",
                    ))
                else:
                    out.append(Verdict(
                        check_id=CHECK_ID, family="family2", status="indeterminate", effect="approval_required",
                        session_id=session.session_id,
                        evidence=Evidence(span_ids=(step.span_id,), excerpt=f"{step.tool_name}.{path}"),
                        detail=f"arg '{path}' on {step.tool_name} is a non-numeric value with no "
                              "traceable origin - could be a fabricated categorical claim or the "
                              "agent's own synthesized judgment; not distinguishable from the value alone",
                    ))
            else:
                out.append(Verdict(
                    check_id=CHECK_ID, family="family2", status="satisfied", effect="allow",
                    session_id=session.session_id,
                    evidence=Evidence(span_ids=(step.span_id,), excerpt=f"{step.tool_name}.{path}"),
                    detail=f"arg '{path}' traces to {origin.match} origin",
                ))
    return out
