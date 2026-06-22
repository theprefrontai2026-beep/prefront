"""Clause classifier.

Assigns every clause a ``disposition`` (and may refine its ``clause_type``), so
the clause ledger can prove nothing was dropped. The deterministic mapping from
clause_type → disposition is the baseline; an LLM pass can refine it. Either way
every clause comes out with a disposition.
"""

from __future__ import annotations

from typing import Iterable

from .schema import Clause, ClauseType, Disposition

# clause_type -> default disposition.
_DISPOSITION_BY_TYPE: dict[str, Disposition] = {
    "definition": "definition_only",
    "explanatory": "non_enforceable_context",
    "audit_requirement": "atom_candidate_required",
    "approval_threshold": "rule_candidate_required",
    "restriction": "rule_candidate_required",
    "exception": "rule_candidate_required",
    "role_permission": "rule_candidate_required",
    "data_access_rule": "rule_candidate_required",
    "regional_rule": "rule_candidate_required",
    "eligibility_rule": "rule_candidate_required",
    "fallback_or_escalation": "rule_candidate_required",
}

SYSTEM_PROMPT = (
    "You are classifying ONE policy clause for a policy compiler. Return ONLY "
    "JSON: {\"clause_type\": <one of the allowed types>, \"disposition\": <one of "
    "the allowed dispositions>}. Allowed clause_type: definition, eligibility_rule, "
    "approval_threshold, restriction, exception, role_permission, data_access_rule, "
    "regional_rule, audit_requirement, fallback_or_escalation, explanatory. Allowed "
    "disposition: rule_candidate_required, atom_candidate_required, definition_only, "
    "related_policy_reference, unresolved, non_enforceable_context, duplicate, "
    "needs_human_review."
)

_ALLOWED_TYPES = set(ClauseType.__args__)  # type: ignore[attr-defined]
_ALLOWED_DISPOSITIONS = set(Disposition.__args__)  # type: ignore[attr-defined]


def heuristic_disposition(clause_type: str) -> Disposition:
    return _DISPOSITION_BY_TYPE.get(clause_type, "needs_human_review")


def classify_clauses(clauses: Iterable[Clause], *, client=None) -> list[Clause]:
    """Return clauses with a disposition set (and possibly refined type)."""
    out: list[Clause] = []
    for cl in clauses:
        ctype = cl.clause_type
        disp = heuristic_disposition(ctype)
        if client is not None:
            try:
                data = client.chat_json(
                    SYSTEM_PROMPT, f'CLAUSE:\n"""\n{cl.source_text}\n"""\n'
                )
                if data:
                    if data.get("clause_type") in _ALLOWED_TYPES:
                        ctype = data["clause_type"]
                    if data.get("disposition") in _ALLOWED_DISPOSITIONS:
                        disp = data["disposition"]
            except Exception:
                pass  # fall back to the heuristic; never drop the clause
        out.append(cl.model_copy(update={"clause_type": ctype, "disposition": disp}))
    return out
