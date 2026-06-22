"""Source-grounding validator.

A rule is grounded when its ``source_evidence`` actually appears in the clause it
cites — proof the LLM did not invent the control. Comparison is
whitespace-normalized and case-insensitive.
"""

from __future__ import annotations

import re

from ..schema import CandidateRule, Clause


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip().lower()


def check(rule: CandidateRule, clause_by_id: dict[str, Clause]) -> tuple[bool, list[dict]]:
    clause = clause_by_id.get(rule.source_clause_id or "")
    evidence = _norm(rule.source_evidence)
    if not evidence:
        return False, [{
            "type": "non_executable_language",
            "severity": "medium",
            "issue": "rule has no source_evidence",
            "recommended_action": "cite the exact clause phrase the rule comes from",
        }]
    if clause is None:
        return False, [{
            "type": "non_executable_language",
            "severity": "medium",
            "issue": f"source_clause_id {rule.source_clause_id!r} not found",
            "recommended_action": "re-segment the document or fix the clause link",
        }]
    if evidence in _norm(clause.source_text):
        return True, []
    return False, [{
        "type": "non_executable_language",
        "severity": "medium",
        "issue": "source_evidence does not appear in the cited clause",
        "recommended_action": "quote the clause verbatim or correct the citation",
    }]
