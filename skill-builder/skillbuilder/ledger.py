"""Clause ledger.

Proves every clause was processed: links clause → disposition → atoms → rules →
unresolved items. Deterministic. The coverage validator and the per-run
``clause_ledger.yaml`` artifact both consume this. No clause may be left without
a disposition — if none was assigned, it is recorded as ``needs_human_review``.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from .schema import (
    CandidateRule,
    Clause,
    ClauseLedgerEntry,
    PolicyAtom,
    UnresolvedItem,
)


def build_ledger(
    clauses: Iterable[Clause],
    rules: Iterable[CandidateRule],
    atoms: Iterable[PolicyAtom] | None = None,
    unresolved: Iterable[UnresolvedItem] | None = None,
) -> list[ClauseLedgerEntry]:
    atoms = list(atoms or [])
    unresolved = list(unresolved or [])

    atoms_by_clause: dict[str, list[str]] = defaultdict(list)
    for a in atoms:
        if a.clause_id:
            atoms_by_clause[a.clause_id].append(a.atom_id)

    rules_by_clause: dict[str, list[str]] = defaultdict(list)
    for r in rules:
        if r.source_clause_id:
            rules_by_clause[r.source_clause_id].append(r.rule_key)

    unresolved_by_clause: dict[str, list[str]] = defaultdict(list)
    for u in unresolved:
        if u.source.clause_id:
            unresolved_by_clause[u.source.clause_id].append(u.unresolved_id)

    entries: list[ClauseLedgerEntry] = []
    for cl in clauses:
        cid = cl.clause_id
        rule_keys = rules_by_clause.get(cid, [])
        atom_ids = atoms_by_clause.get(cid, [])
        unresolved_ids = unresolved_by_clause.get(cid, [])
        disposition = cl.disposition or _infer(rule_keys, atom_ids, unresolved_ids)
        entries.append(ClauseLedgerEntry(
            clause_id=cid,
            section=cl.section_path,
            disposition=disposition,
            generated_atoms=atom_ids,
            generated_rules=rule_keys,
            unresolved_items=unresolved_ids,
        ))
    return entries


def _infer(rule_keys, atom_ids, unresolved_ids):
    """Fallback disposition when the classifier did not set one."""
    if rule_keys:
        return "rule_extracted"
    if unresolved_ids:
        return "unresolved"
    if atom_ids:
        return "atom_extracted"
    return "needs_human_review"
