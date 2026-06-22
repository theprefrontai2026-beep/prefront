"""Pipeline orchestrator.

Wires the stages into one flow that both the CLI and the FastAPI service call:

    extract -> normalize -> segment -> LLM candidate rules -> validate -> conflicts

The pipeline is store-agnostic: it returns a :class:`PipelineResult` of plain
objects. Callers persist and/or render artifacts as they see fit.
"""

from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from . import extract as extract_mod
from .artifacts import SkillMeta, write_skill_artifacts
from .atoms import extract_atoms
from .classifier import classify_clauses
from .conflicts import detect_conflicts
from .llm import ExtractionContext, RuleExtractor
from .normalize import NormalizedDoc, normalize
from .profiler import profile_document
from .schema import CandidateRule, Clause, Conflict, DocumentProfile, PolicyAtom
from .segment import segment, segment_sections

log = logging.getLogger(__name__)


@dataclass
class PipelineResult:
    document_id: str
    version: str
    normalized: NormalizedDoc
    clauses: list[Clause]
    candidates: list[CandidateRule]
    conflicts: list[Conflict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    skipped_clauses: int = 0
    generated_by: str = ""
    profile: Optional[DocumentProfile] = None
    atoms: list[PolicyAtom] = field(default_factory=list)


def run_pipeline(
    source_path: str | Path,
    *,
    document_id: str,
    version: str,
    domain: str = "general",
    title: Optional[str] = None,
    context: Optional[ExtractionContext] = None,
    extractor: Optional[RuleExtractor] = None,
    granularity: str = "section",
) -> PipelineResult:
    """Run the full design-time pipeline over a single document file."""
    path = Path(source_path)
    raw_text = extract_mod.extract_text(path)
    return run_pipeline_text(
        raw_text,
        document_id=document_id,
        version=version,
        domain=domain,
        file_name=path.name,
        title=title,
        context=context,
        extractor=extractor,
        granularity=granularity,
    )


def run_pipeline_text(
    raw_text: str,
    *,
    document_id: str,
    version: str,
    file_name: str,
    domain: str = "general",
    title: Optional[str] = None,
    context: Optional[ExtractionContext] = None,
    extractor: Optional[RuleExtractor] = None,
    granularity: str = "section",
    do_profile: bool = False,
    do_classify: bool = False,
    do_atoms: bool = False,
) -> PipelineResult:
    """Same as :func:`run_pipeline` but starting from already-extracted text.

    ``granularity='section'`` (default) feeds whole sections to the LLM and
    skips boilerplate; ``'clause'`` shreds prose into per-sentence clauses.

    The ``do_profile`` / ``do_classify`` / ``do_atoms`` flags add the deeper
    design stages (off by default, so the direct extract path is unchanged).
    """
    ctx = context or ExtractionContext(domain=domain)
    extractor = extractor or RuleExtractor()
    run_t0 = time.perf_counter()
    log.info(
        "pipeline start doc=%s v=%s domain=%s granularity=%s chars=%d "
        "(profile=%s classify=%s atoms=%s)",
        document_id, version, domain, granularity, len(raw_text or ""),
        do_profile, do_classify, do_atoms,
    )

    with _stage("normalize"):
        normalized = normalize(
            raw_text,
            document_id=document_id,
            version=version,
            file_name=file_name,
            title=title,
        )
    log.debug("normalized: sections=%d", len(normalized.sections))

    with _stage("segment"):
        clauses = (
            segment_sections(normalized)
            if granularity == "section"
            else segment(normalized)
        )
    log.info("segmented %d clause(s) at granularity=%s", len(clauses), granularity)
    if log.isEnabledFor(logging.DEBUG):
        for c in clauses:
            log.debug(
                "  clause %s [%s] %s", c.clause_id, c.clause_type, c.section_path
            )

    profile = None
    if do_profile:
        with _stage("profile"):
            profile = profile_document(
                normalized.canonical_markdown, domain=domain, client=extractor
            )
        if profile:
            log.info(
                "profiled: domain=%s confidence=%.2f",
                profile.detected_domain, profile.domain_confidence,
            )
    if do_classify:
        with _stage("classify"):
            clauses = classify_clauses(clauses, client=extractor)
    atoms = []
    if do_atoms:
        with _stage("atoms"):
            atoms = extract_atoms(clauses, client=extractor)
        log.info("extracted %d policy atom(s)", len(atoms))

    candidates: list[CandidateRule] = []
    errors: list[str] = []
    skipped = 0
    with _stage("extract-rules"):
        for result in extractor.extract_clauses(clauses, ctx):
            if result.skipped:
                skipped += 1
            candidates.extend(result.candidates)
            errors.extend(
                f"{result.clause.clause_id}: {e}" for e in result.errors
            )
    log.info(
        "extracted %d candidate rule(s); skipped=%d errors=%d",
        len(candidates), skipped, len(errors),
    )
    for e in errors:
        log.warning("extract error %s", e)

    # Collapse exact-duplicate rule_keys (same key + identical body) that
    # multiple clauses can produce; keep the highest-confidence occurrence.
    # Same key with a *different* body is left in place — the conflict detector
    # flags it as duplicate_rule_key for the reviewer to merge.
    before = len(candidates)
    candidates = _dedupe(candidates)
    if before != len(candidates):
        log.info("deduped %d -> %d candidate rule(s)", before, len(candidates))

    with _stage("conflicts"):
        conflicts = detect_conflicts(
            candidates,
            known_roles=ctx.known_roles,
            known_fields=ctx.known_fields,
        )
    for c in conflicts:
        log.warning("conflict [%s] %s: %s", c.severity, c.type, c.message)
    log.info(
        "pipeline done doc=%s rules=%d conflicts=%d in %.2fs",
        document_id, len(candidates), len(conflicts), time.perf_counter() - run_t0,
    )

    return PipelineResult(
        document_id=document_id,
        version=version,
        normalized=normalized,
        clauses=clauses,
        candidates=candidates,
        conflicts=conflicts,
        errors=errors,
        skipped_clauses=skipped,
        generated_by=extractor.model,
        profile=profile,
        atoms=atoms,
    )


@contextmanager
def _stage(name: str):
    """Time a pipeline stage and log its duration at DEBUG."""
    t0 = time.perf_counter()
    log.debug("stage %s ...", name)
    try:
        yield
    finally:
        log.debug("stage %s done in %.2fs", name, time.perf_counter() - t0)


def _dedupe(candidates: list[CandidateRule]) -> list[CandidateRule]:
    """Drop exact-duplicate (rule_key + identical condition/effect) rules."""

    def body(r: CandidateRule):
        conds = tuple(
            sorted((c.field, c.operator, repr(c.value)) for c in r.conditions)
        )
        return (
            r.rule_key,
            conds,
            r.effect.decision,
            r.effect.approver_role,
            tuple(r.effect.restricted_fields or ()),
        )

    best: dict[tuple, CandidateRule] = {}
    order: list[tuple] = []
    for c in candidates:
        k = body(c)
        if k not in best:
            best[k] = c
            order.append(k)
        elif c.confidence > best[k].confidence:
            best[k] = c
    return [best[k] for k in order]


def write_artifacts(
    result: PipelineResult,
    *,
    out_root: str | Path,
    skill_id: str,
    name: str,
    domain: str,
    file_name: str,
    owner: Optional[str] = None,
    effective_from: Optional[str] = None,
    known_roles: Optional[list[str]] = None,
    known_fields: Optional[list[str]] = None,
) -> dict[str, str]:
    """Render the five skill artifacts for a completed pipeline run."""
    import hashlib

    file_hash = "sha256:" + hashlib.sha256(
        result.normalized.canonical_markdown.encode("utf-8")
    ).hexdigest()
    meta = SkillMeta(
        skill_id=skill_id,
        name=name,
        domain=domain,
        version=result.version,
        source_document=result.document_id,
        file_name=file_name,
        file_hash=file_hash,
        owner=owner,
        effective_from=effective_from,
    )
    return write_skill_artifacts(
        out_root,
        meta,
        result.candidates,
        result.clauses,
        result.normalized.canonical_markdown,
        result.generated_by,
        known_roles=known_roles,
        known_fields=known_fields,
    )
