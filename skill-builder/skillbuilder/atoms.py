"""Policy-atom extractor (intermediate IR).

Extracts domain-neutral policy atoms from clauses for auditability and the clause
ledger. This is *additive*: the proven direct clause→candidate-rule extractor in
``llm.py`` remains the default rule source. Atoms make extraction explainable
(clause → atom → rule in the ledger) and never carry a construct the flat rule IR
forbids. Requires an LLM client; without one it returns no atoms.
"""

from __future__ import annotations

from typing import Iterable

from pydantic import ValidationError

from .schema import AtomType, Clause, PolicyAtom

_ALLOWED = set(AtomType.__args__)  # type: ignore[attr-defined]

SYSTEM_PROMPT = (
    "You are extracting domain-neutral POLICY ATOMS from one policy clause for a "
    "policy compiler. Return ONLY JSON: {\"atoms\": [{atom_type, actor, action, "
    "object, condition, effect, source_evidence, confidence}]}. atom_type is one "
    "of: prohibition, permission, obligation, approval_requirement, "
    "authority_assignment, threshold, exception, waiver, segregation_of_duties, "
    "audit_requirement, retention_requirement, data_access_permission, "
    "data_access_restriction, routing_requirement, definition, "
    "related_policy_reference. action is a list of strings. condition/effect are "
    "objects or null. Extract ONLY what the clause supports; do not invent. If the "
    "clause states no atom, return an empty list."
)


def extract_atoms(clauses: Iterable[Clause], *, client=None) -> list[PolicyAtom]:
    if client is None:
        return []
    atoms: list[PolicyAtom] = []
    seq = 0
    for cl in clauses:
        if cl.clause_type in ("definition", "explanatory"):
            continue
        try:
            data = client.chat_json(
                SYSTEM_PROMPT, f'CLAUSE:\n"""\n{cl.source_text}\n"""\n'
            )
        except Exception:
            continue
        for item in (data or {}).get("atoms", []) or []:
            if item.get("atom_type") not in _ALLOWED:
                continue
            seq += 1
            item.setdefault("source_evidence", cl.source_text[:200])
            item["atom_id"] = f"a_{seq:04d}"
            item["clause_id"] = cl.clause_id
            # normalize a stringy action into a list
            if isinstance(item.get("action"), str):
                item["action"] = [item["action"]]
            try:
                atoms.append(PolicyAtom.model_validate(item))
            except ValidationError:
                continue
    return atoms
