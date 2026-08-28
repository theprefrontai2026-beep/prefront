"""Over-retrieval: a call's row_count is both above an absolute floor and far
above this session's own pattern for that tool - fetched much more than its
peers, judged against the session itself rather than an external catalog
(Family 3's volume_scope does the catalog-relative version, Phase B).

Applicable only to steps that report a row_count (Hard Rule 16) - a tool
whose binding profile doesn't capture row_count is never judged here.
"""

from __future__ import annotations

from collections import defaultdict
from statistics import median

from ..contract import CheckContext, Evidence, Session, Verdict

CHECK_ID = "minimization"


def evaluate(session: Session, ctx: CheckContext) -> list[Verdict]:
    by_tool: dict[str, list] = defaultdict(list)
    for step in session.steps:
        if step.row_count is not None:
            by_tool[step.tool_name].append(step)

    floor = ctx.config.get("minimization_row_floor", 50)
    multiple = ctx.config.get("minimization_multiple", 5.0)

    out: list[Verdict] = []
    for tool_name, steps in by_tool.items():
        for step in steps:
            peers = [s.row_count for s in steps if s.span_id != step.span_id]
            baseline = median(peers) if peers else step.row_count
            over = step.row_count > floor and (baseline <= 0 or step.row_count > multiple * baseline)
            if over:
                out.append(Verdict(
                    check_id=CHECK_ID, family="family2", status="violated", effect="flag",
                    session_id=session.session_id,
                    evidence=Evidence(span_ids=(step.span_id,), excerpt=f"{tool_name} rows={step.row_count}"),
                    detail=(f"{tool_name} (step {step.seq}) returned {step.row_count} rows, "
                            f">{multiple:g}x this session's baseline ({baseline:g}) for the same tool"),
                ))
            else:
                out.append(Verdict(
                    check_id=CHECK_ID, family="family2", status="satisfied", effect="allow",
                    session_id=session.session_id,
                    evidence=Evidence(span_ids=(step.span_id,), excerpt=f"{tool_name} rows={step.row_count}"),
                    detail=f"{tool_name} (step {step.seq}) row_count in line with session baseline",
                ))
    return out
