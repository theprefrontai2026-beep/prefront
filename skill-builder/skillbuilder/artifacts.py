"""Artifact renderers.

Renders the registry layout from design.md:

    skills/<skill_id>/v<version>/
      source_policy.md       # clean, citable markdown
      policy_skill.yaml       # high-level skill metadata
      extracted_rules.yaml    # candidate rules (status: draft) + provenance
      test_cases.yaml         # generated policy tests
      review_report.yaml      # confidence / ambiguities / conflicts (review aid)

Nothing here promotes a rule to runtime. Rules are emitted as drafts; the
published runtime rule (with approved_by/approved_at) is only produced when a
human approves it via the API/CLI.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

import yaml

from .conflicts import detect_conflicts
from .schema import (
    CandidateRule,
    Clause,
    ClauseLedgerEntry,
    Conflict,
    DocumentProfile,
    PolicyAtom,
    UnresolvedItem,
    ValidationReport,
)
from .tests_gen import generate_test_cases, untestable_rules

log = logging.getLogger(__name__)


def _safe_segment(value: str, *, label: str = "segment") -> str:
    """Reduce an id/version to ONE safe path component.

    Defends the registry against a full path or stray separators leaking into a
    segment (e.g. a DDL path mistakenly passed as the version), which would
    otherwise explode ``skills/<id>/v<version>/`` into a deep directory tree.
    """
    raw = str(value).strip()
    base = os.path.basename(raw.rstrip("/\\"))  # drop any directory portion
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", base).strip("._-")
    safe = safe or "unknown"
    if safe != raw:
        log.warning("sanitized %s %r -> %r for registry path", label, raw, safe)
    return safe


def _log_written(kind: str, out_dir: Path, written: dict[str, str]) -> None:
    """Report where artifacts landed (container path; bind-mounted to the host)."""
    log.info(
        "wrote %d %s artifact(s) to %s", len(written), kind, out_dir.resolve()
    )
    for name, path in written.items():
        log.debug("  %s -> %s", name, path)


class _BlockDumper(yaml.SafeDumper):
    """YAML dumper that keeps lists block-style and strings readable."""


def _dump(data: Any) -> str:
    return yaml.dump(
        data,
        Dumper=_BlockDumper,
        sort_keys=False,
        default_flow_style=False,
        allow_unicode=True,
        width=100,
    )


@dataclass
class SkillMeta:
    skill_id: str
    name: str
    domain: str
    version: str
    source_document: str
    file_name: str
    file_hash: str
    owner: Optional[str] = None
    effective_from: Optional[str] = None


def _rule_block(rule: CandidateRule, clause_by_id: dict[str, Clause]) -> dict[str, Any]:
    """One rule rendered for extracted_rules.yaml, with a full source block."""
    clause = clause_by_id.get(rule.source_clause_id or "")
    source: dict[str, Any] = {
        "document": clause.document_id if clause else rule.source_clause_id,
        "clause_id": rule.source_clause_id,
        "section": clause.section_path if clause else "",
        "page": clause.page_number if clause else None,
        "paragraph_ref": clause.paragraph_ref if clause else None,
        "evidence": rule.source_evidence,
        "text": clause.source_text if clause else "",
    }
    effect: dict[str, Any] = {"decision": rule.effect.decision}
    if rule.effect.approval_required is not None:
        effect["approval_required"] = rule.effect.approval_required
    if rule.effect.approver_role:
        effect["approver_role"] = rule.effect.approver_role
    if rule.effect.restricted_fields:
        effect["restricted_fields"] = rule.effect.restricted_fields
    if rule.effect.message:
        effect["message"] = rule.effect.message

    return {
        "rule_key": rule.rule_key,
        "rule_type": rule.rule_type,
        "conditions": [
            {"field": c.field, "operator": c.operator, "value": c.value}
            for c in rule.conditions
        ],
        "effect": effect,
        "applies_to_intents": rule.applies_to_intents,
        "requires_trace": rule.requires_trace,
        "source": source,
        # Runtime status tracks review: approved -> active, otherwise draft.
        "status": "active" if rule.review_status == "approved" else "draft",
        "review_status": rule.review_status,
    }


def render_extracted_rules(
    meta: SkillMeta,
    rules: list[CandidateRule],
    clauses: Iterable[Clause],
    generated_by: str,
) -> str:
    clause_by_id = {c.clause_id: c for c in clauses}
    doc = {
        "skill_id": meta.skill_id,
        "source_document": meta.source_document,
        "document_version": meta.version,
        "domain": meta.domain,
        "generated_by": generated_by,
        "rules": [_rule_block(r, clause_by_id) for r in rules],
    }
    header = (
        f"# Extracted rules for {meta.source_document} (v{meta.version})\n"
        f"# MACHINE-GENERATED DRAFT by {generated_by} — REVIEW REQUIRED before approval.\n"
        "# LLM output is candidate output: promote review_status pending -> approved after human check.\n"
        "# Every rule cites its source clause; do not enable enforcement until approved.\n\n"
    )
    return header + _dump(doc)


def render_policy_skill(
    meta: SkillMeta, rules: list[CandidateRule], requires_review: bool = True
) -> str:
    intents = sorted({i for r in rules for i in r.applies_to_intents})
    doc = {
        "skill_id": meta.skill_id,
        "name": meta.name,
        "version": meta.version,
        "status": "draft",
        "domain": meta.domain,
        "source_documents": [
            {
                "document_id": meta.source_document,
                "file_name": meta.file_name,
                "file_hash": meta.file_hash,
            }
        ],
        "owner": meta.owner,
        "effective_from": meta.effective_from,
        "requires_human_review": requires_review,
        "rule_count": len(rules),
        "applies_to": intents,
    }
    return _dump(doc)


def render_test_cases(meta: SkillMeta, rules: list[CandidateRule]) -> str:
    cases = generate_test_cases(rules)
    doc: dict[str, Any] = {
        "skill_id": meta.skill_id,
        "version": meta.version,
        "test_cases": cases,
    }
    missing = untestable_rules(rules)
    if missing:
        doc["untestable_rules"] = missing
    return _dump(doc)


def render_source_policy(meta: SkillMeta, canonical_markdown: str) -> str:
    return canonical_markdown


def render_review_report(
    meta: SkillMeta,
    rules: list[CandidateRule],
    conflicts: list[Conflict],
) -> str:
    doc = {
        "skill_id": meta.skill_id,
        "version": meta.version,
        "rule_reviews": [
            {
                "rule_key": r.rule_key,
                "confidence": r.confidence,
                "ambiguities": r.ambiguities,
                "review_status": r.review_status,
            }
            for r in rules
        ],
        "conflicts": [c.model_dump() for c in conflicts],
        "untestable_rules": untestable_rules(rules),
    }
    return _dump(doc)


def render_validation_report(meta: SkillMeta, report: ValidationReport) -> str:
    doc = {
        "schema_version": "prefront.validation_report.v1",
        "skill_id": meta.skill_id,
        "document_id": meta.source_document,
        **report.model_dump(),
    }
    return _dump(doc)


def render_unresolved_items(meta: SkillMeta, items: list[UnresolvedItem]) -> str:
    doc = {
        "schema_version": "prefront.unresolved_items.v1",
        "document_id": meta.source_document,
        "unresolved_items": [i.model_dump() for i in items],
    }
    return _dump(doc)


def render_clauses(meta: SkillMeta, clauses: list[Clause]) -> str:
    doc = {
        "schema_version": "prefront.clauses.v1",
        "document_id": meta.source_document,
        "clauses": [
            {
                "clause_id": c.clause_id,
                "section": c.section_path,
                "clause_type": c.clause_type,
                "disposition": c.disposition,
                "page": c.page_number,
                "paragraph_ref": c.paragraph_ref,
                "source_text": c.source_text,
            }
            for c in clauses
        ],
    }
    return _dump(doc)


def render_clause_ledger(meta: SkillMeta, ledger: list[ClauseLedgerEntry]) -> str:
    doc = {
        "schema_version": "prefront.clause_ledger.v1",
        "document_id": meta.source_document,
        "clauses": [e.model_dump() for e in ledger],
    }
    return _dump(doc)


def render_policy_atoms(meta: SkillMeta, atoms: list[PolicyAtom]) -> str:
    doc = {
        "schema_version": "prefront.policy_atoms.v1",
        "document_id": meta.source_document,
        "atoms": [a.model_dump() for a in atoms],
    }
    return _dump(doc)


def render_document_profile(meta: SkillMeta, profile: DocumentProfile) -> str:
    doc = {"document_id": meta.source_document, **profile.model_dump()}
    return _dump(doc)


def write_run_artifacts(
    out_root: str | Path,
    meta: SkillMeta,
    run_id: str,
    *,
    profile: Optional[DocumentProfile] = None,
    clauses: Optional[list[Clause]] = None,
    ledger: Optional[list[ClauseLedgerEntry]] = None,
    atoms: Optional[list[PolicyAtom]] = None,
    unresolved_items: Optional[list[UnresolvedItem]] = None,
    validation_report: Optional[ValidationReport] = None,
) -> dict[str, str]:
    """Write per-run intermediates under skills/<skill_id>/v<version>/runs/<run_id>/."""
    out_dir = (
        Path(out_root)
        / _safe_segment(meta.skill_id, label="skill_id")
        / f"v{_safe_segment(meta.version, label='version')}"
        / "runs"
        / _safe_segment(run_id, label="run_id")
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    files: dict[str, str] = {}
    if profile is not None:
        files["document_profile.yaml"] = render_document_profile(meta, profile)
    if clauses is not None:
        files["clauses.yaml"] = render_clauses(meta, clauses)
    if ledger is not None:
        files["clause_ledger.yaml"] = render_clause_ledger(meta, ledger)
    if atoms is not None:
        files["policy_atoms.yaml"] = render_policy_atoms(meta, atoms)
    if unresolved_items is not None:
        files["unresolved_items.yaml"] = render_unresolved_items(meta, unresolved_items)
    if validation_report is not None:
        files["validation_report.yaml"] = render_validation_report(meta, validation_report)
    written: dict[str, str] = {}
    for name, content in files.items():
        path = out_dir / name
        path.write_text(content, encoding="utf-8")
        written[name] = str(path)
    _log_written("per-run", out_dir, written)
    return written


def write_skill_artifacts(
    out_root: str | Path,
    meta: SkillMeta,
    rules: list[CandidateRule],
    clauses: list[Clause],
    canonical_markdown: str,
    generated_by: str,
    *,
    known_roles: Optional[list[str]] = None,
    known_fields: Optional[list[str]] = None,
    validation_report: Optional[ValidationReport] = None,
    unresolved_items: Optional[list[UnresolvedItem]] = None,
) -> dict[str, str]:
    """Write the skill artifacts under skills/<skill_id>/v<version>/.

    Always writes the core five; adds validation_report.yaml /
    unresolved_items.yaml when those are supplied.
    """
    conflicts = detect_conflicts(
        rules, known_roles=known_roles, known_fields=known_fields
    )
    out_dir = (
        Path(out_root)
        / _safe_segment(meta.skill_id, label="skill_id")
        / f"v{_safe_segment(meta.version, label='version')}"
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    files = {
        "source_policy.md": render_source_policy(meta, canonical_markdown),
        "policy_skill.yaml": render_policy_skill(meta, rules),
        "extracted_rules.yaml": render_extracted_rules(
            meta, rules, clauses, generated_by
        ),
        "test_cases.yaml": render_test_cases(meta, rules),
        "review_report.yaml": render_review_report(meta, rules, conflicts),
    }
    if validation_report is not None:
        files["validation_report.yaml"] = render_validation_report(
            meta, validation_report
        )
    if unresolved_items is not None:
        files["unresolved_items.yaml"] = render_unresolved_items(
            meta, unresolved_items
        )
    written: dict[str, str] = {}
    for name, content in files.items():
        path = out_dir / name
        path.write_text(content, encoding="utf-8")
        written[name] = str(path)
    _log_written("skill", out_dir, written)
    return written
