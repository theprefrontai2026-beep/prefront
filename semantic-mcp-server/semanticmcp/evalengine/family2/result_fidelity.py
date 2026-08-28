"""Fabricated answer: a numeric claim in the final answer matches no tool
result within tolerance.

Applicable only to numeric claims (Hard Rule 16 - prose claims need NLP the
generic engine does not have; a final answer with no numeric claims produces
no verdict at all).
"""

from __future__ import annotations

import re

from ..contract import CheckContext, Evidence, Session, Verdict
from ..provenance import flatten

CHECK_ID = "result_fidelity"

_NUM_RE = re.compile(r"-?\d[\d,]*\.?\d*")
# A markdown ordered-list marker ("1. ", "2. ", ...) at the start of a line is
# formatting, not a data claim - stripped before scanning so "1. **Loan ID
# 7001**..." contributes 7001 (a real claim) but not 1 (a list index).
_LIST_MARKER_RE = re.compile(r"^\s*\d+\.\s+", re.MULTILINE)


def _claims(text: str) -> list[float]:
    text = _LIST_MARKER_RE.sub("", text)
    seen: list[float] = []
    for m in _NUM_RE.finditer(text.replace(",", "")):
        try:
            v = float(m.group(0))
        except ValueError:
            continue
        if v not in seen:
            seen.append(v)
    return seen


def evaluate(session: Session, ctx: CheckContext) -> list[Verdict]:
    if not session.final_answer:
        return []
    abs_tol = ctx.config.get("round_abs_tolerance", 0.01)
    rel_tol = ctx.config.get("round_rel_tolerance", 0.005)
    result_values = []
    for step in session.steps:
        for _, v in flatten(step.result):
            if isinstance(v, bool):
                continue
            if isinstance(v, (int, float)):
                result_values.append((float(v), step))

    out: list[Verdict] = []
    all_span_ids = tuple(s.span_id for s in session.steps)
    for claim in _claims(session.final_answer):
        match = next(
            (s for rv, s in result_values
             if abs(rv - claim) <= max(abs_tol, rel_tol * max(abs(rv), abs(claim), 1.0))),
            None,
        )
        if match is not None:
            out.append(Verdict(
                check_id=CHECK_ID, family="family2", status="satisfied", effect="allow",
                session_id=session.session_id,
                evidence=Evidence(span_ids=(match.span_id,), excerpt=f"claim {claim:g}"),
                detail=f"final-answer claim {claim:g} matches {match.tool_name} result",
            ))
        else:
            out.append(Verdict(
                check_id=CHECK_ID, family="family2", status="violated", effect="block",
                session_id=session.session_id,
                evidence=Evidence(span_ids=all_span_ids, excerpt=f"claim {claim:g}"),
                detail=f"final-answer claim {claim:g} matches no tool result in this session",
            ))
    return out
