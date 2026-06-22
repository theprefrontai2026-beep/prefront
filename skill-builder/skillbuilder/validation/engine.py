"""Validation orchestrator.

Runs schema / grounding / semantic / executability / consistency / coverage /
testability over a candidate-rule set and assembles a :class:`ValidationReport`.
The trivial validators (schema, consistency, testability, coverage) reuse code
that already exists — `conflicts.detect_conflicts` and
`tests_gen.untestable_rules` — rather than reimplementing it.
"""

from __future__ import annotations

import logging
from typing import Iterable, Optional

from ..conflicts import detect_conflicts
from ..schema import (
    CandidateRule,
    Clause,
    RuleValidation,
    UnresolvedItem,
    UnresolvedSource,
    ValidationReport,
)
from ..tests_gen import untestable_rules
from . import executability, grounding, semantic

log = logging.getLogger(__name__)

# Structural clause types that usually carry no rule (headings, glossary, prose).
# Coverage still REPORTS these — at low severity — rather than dropping them, so
# nothing the document contains is ever silently hidden from the reviewer. We
# deliberately do not suppress on the classifier's label (clause_type OR
# disposition): those are the very judgments that have mislabeled real
# role/permission clauses before, and the whole point of coverage is to show
# every part of the policy that did not become a rule.
_LOW_SEVERITY_TYPES = {"definition", "explanatory"}


def _pack_namespaces(pack) -> tuple[set[str], set[str]]:
    """Request-param and metric names the pack itself declares."""
    params, metrics = set(), set()
    if pack is not None:
        for name, f in pack.model.fields.items():
            if f.binds_to == "request_param":
                params.add(name)
            elif f.binds_to == "metric":
                metrics.add(name)
    return params, metrics


def run_all(
    rules: Iterable[CandidateRule],
    clauses: Iterable[Clause],
    *,
    pack=None,
    declared_params: Optional[set[str]] = None,
    metrics: Optional[set[str]] = None,
) -> ValidationReport:
    rules = list(rules)
    clause_by_id = {c.clause_id: c for c in clauses}
    clauses = list(clause_by_id.values())

    pack_params, pack_metrics = _pack_namespaces(pack)
    declared_params = (declared_params or set()) | pack_params
    metrics = (metrics or set()) | pack_metrics

    known_roles = pack.known_roles() if pack else None
    known_fields = pack.known_fields() if pack else None
    conflicts = detect_conflicts(rules, known_roles=known_roles, known_fields=known_fields)
    in_high_conflict = {
        rk for c in conflicts if c.severity == "high" for rk in c.rules
    }
    untestable = set(untestable_rules(rules))

    rule_results: list[RuleValidation] = []
    unresolved: list[UnresolvedItem] = []
    seq = 0

    def add_unresolved(rule: CandidateRule, problems: list[dict]):
        nonlocal seq
        clause = clause_by_id.get(rule.source_clause_id or "")
        for p in problems:
            seq += 1
            unresolved.append(UnresolvedItem(
                unresolved_id=f"u_{seq:03d}",
                type=p["type"],
                severity=p.get("severity", "medium"),
                source=UnresolvedSource(
                    document_id=clause.document_id if clause else None,
                    clause_id=rule.source_clause_id,
                    section=clause.section_path if clause else "",
                    evidence=rule.source_evidence,
                ),
                issue=p.get("issue", ""),
                impact=p.get("impact", ""),
                recommended_action=p.get("recommended_action", ""),
                blocks_publication=p.get("severity") == "critical",
                rule_key=rule.rule_key,
            ))

    for rule in rules:
        grounded, gp = grounding.check(rule, clause_by_id)
        sem_ok, sp = semantic.check(rule, pack)
        exec_ok, ep = executability.check(rule, pack, declared_params, metrics)
        add_unresolved(rule, gp + sp + ep)

        consistency_ok = rule.rule_key not in in_high_conflict
        testable = rule.rule_key not in untestable

        blockers: list[str] = []
        if not grounded:
            blockers.append("NOT_SOURCE_GROUNDED")
        if not sem_ok:
            blockers.append("SEMANTIC_INVALID")
        if not exec_ok:
            blockers.append("NOT_EXECUTABLE")
        if not testable:
            blockers.append("NOT_TESTABLE")
        if not consistency_ok:
            blockers.append("CONSISTENCY_CONFLICT")
        if rule.review_status != "approved":
            blockers.append("REVIEW_NOT_APPROVED")

        rule_results.append(RuleValidation(
            rule_key=rule.rule_key,
            schema_valid=True,  # it is a validated CandidateRule
            source_grounded=grounded,
            semantic_valid=sem_ok,
            executable=exec_ok,
            testable=testable,
            consistency_valid=consistency_ok,
            publishable=not blockers,
            publish_blockers=blockers,
        ))

    # Coverage: surface EVERY non-boilerplate clause that produced no rule, so a
    # reviewer can see exactly which parts of the policy were not converted (and
    # why). Only clauses explicitly marked non-enforceable (a definition/prose
    # clause_type, or a classifier disposition saying "not a rule") are skipped.
    clauses_with_rule = {r.source_clause_id for r in rules if r.source_clause_id}
    clauses_with_unresolved = {u.source.clause_id for u in unresolved if u.source.clause_id}
    uncovered = 0
    for cl in clauses:
        if cl.clause_id in clauses_with_rule or cl.clause_id in clauses_with_unresolved:
            continue
        seq += 1
        uncovered += 1
        disp = cl.disposition or "unset"
        severity = "low" if cl.clause_type in _LOW_SEVERITY_TYPES else "medium"
        unresolved.append(UnresolvedItem(
            unresolved_id=f"u_{seq:03d}",
            type="unconverted_clause",
            severity=severity,
            source=UnresolvedSource(
                document_id=cl.document_id,
                clause_id=cl.clause_id,
                section=cl.section_path,
                # Full clause text (not truncated) so the reviewer sees exactly
                # which part of the document this is.
                evidence=cl.source_text,
            ),
            issue=(
                f"clause in section '{cl.section_path or '(none)'}' "
                f"(clause_type={cl.clause_type}, disposition={disp}) produced no "
                f"rule — this part of the policy is not enforced"
            ),
            impact="policy text that may carry an enforceable control is unenforced",
            recommended_action=(
                "review this clause: extract a rule from it, or confirm it is "
                "non-enforceable (a definition, heading, or commentary)"
            ),
            rule_key=None,
        ))
    log.info(
        "coverage: %d clause(s) produced no rule (surfaced as unconverted_clause)",
        uncovered,
    )

    summary = {
        "candidate_rules_total": len(rules),
        "source_grounded_rules": sum(r.source_grounded for r in rule_results),
        "semantic_valid_rules": sum(r.semantic_valid for r in rule_results),
        "executable_rules": sum(r.executable for r in rule_results),
        "testable_rules": sum(r.testable for r in rule_results),
        "publishable_rules": sum(r.publishable for r in rule_results),
        "unresolved_items_total": len(unresolved),
        "critical_unresolved_items": sum(u.severity == "critical" for u in unresolved),
        "clauses_total": len(clauses),
        "clauses_with_candidate_rules": len(clauses_with_rule),
        "unconverted_clauses": uncovered,
    }

    return ValidationReport(
        summary=summary,
        rule_results=rule_results,
        unresolved_items=unresolved,
        conflicts=conflicts,
    )
