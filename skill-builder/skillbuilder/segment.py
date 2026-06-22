"""Clause segmenter.

Splits normalized sections into atomic policy clauses and tags each with a
heuristic ``clause_type`` (design.md "Clause Segmenter"). The type is only a
hint for the reviewer and a routing signal for extraction — the LLM still
decides the real ``rule_type``, and explanatory clauses are kept (not dropped)
so nothing silently disappears.

Deterministic and dependency-free.
"""

from __future__ import annotations

import re

from .normalize import NormalizedDoc
from .schema import Clause, ClauseType

# Sentence-ish splitter: break on sentence terminators followed by space + capital,
# but keep bullets/numbered list items as their own clauses.
_SENTENCE_SPLIT = re.compile(r"(?<=[.;])\s+(?=[A-Z0-9])")
_BULLET = re.compile(r"^\s*(?:[-*•]|\(?[a-z0-9]{1,3}[.)])\s+")

# (clause_type, keyword-regex) — first match wins, order matters.
_TYPE_PATTERNS: list[tuple[ClauseType, re.Pattern[str]]] = [
    ("approval_threshold", re.compile(r"\b(approv|sign[- ]?off|authoriz)\w*", re.I)),
    ("restriction", re.compile(r"\b(must not|may not|prohibit|forbidden|denied|restrict|block|withheld|masked?)\w*", re.I)),
    ("exception", re.compile(r"\b(except|unless|other than|excluding|waiver)\w*", re.I)),
    ("regional_rule", re.compile(r"\b(region|territory|out[- ]of[- ]territory|geograph)\w*", re.I)),
    ("data_access_rule", re.compile(r"\b(field|column|confidential|restricted data|need[- ]to[- ]know|disclos)\w*", re.I)),
    ("role_permission", re.compile(r"\b(role|manager|finance|rep|entitlement|permission)\w*", re.I)),
    ("audit_requirement", re.compile(r"\b(audit|trace|log|record|retain)\w*", re.I)),
    ("eligibility_rule", re.compile(r"\b(eligib|qualif|entitled to)\w*", re.I)),
    ("fallback_or_escalation", re.compile(r"\b(escalat|fallback|default to|otherwise)\w*", re.I)),
    ("definition", re.compile(r"\b(means|is defined as|refers to|definition)\b", re.I)),
]

# Lines that are pure structure rather than policy text.
_NOISE = re.compile(r"^\s*(\[page:\d+\]|source:|version:|#)", re.I)

# Section headings that carry no enforceable control — skipped for extraction.
# Matched against the heading text with any leading "N.N " number stripped.
_BOILERPLATE_HEADING = re.compile(
    r"^(?:purpose|scope|definitions?|revision history|related documents?|"
    r"document control|table of contents|references?|approval history|"
    r"compliance\s*&?\s*audit|audit(?:\s*&?\s*compliance)?|glossary|overview|"
    r"introduction|background)\b",
    re.I,
)
_LEADING_NUM = re.compile(r"^\s*\d+(?:\.\d+)*\s*[.)]?\s*")


# Signature of a document-control header block (ID/version/owner metadata).
_CONTROL_HEADER = re.compile(r"document id", re.I)


def is_boilerplate_section(section_path: str, body: str = "") -> bool:
    """True for sections that should not produce enforceable rules."""
    if section_path.strip().lower() == "preamble":
        return True  # document-control header table
    heading = _LEADING_NUM.sub("", section_path).strip()
    if _BOILERPLATE_HEADING.match(heading):
        return True
    # The doc title section often carries the control-header table (Document
    # ID / Classification / Approved by). Skip it without dropping real tables.
    if _CONTROL_HEADER.search(body) and re.search(
        r"classification|approved by|effective date|next review", body, re.I
    ):
        return True
    return False


def _classify(text: str) -> ClauseType:
    for clause_type, pat in _TYPE_PATTERNS:
        if pat.search(text):
            return clause_type
    return "explanatory"


def _clause_units(markdown: str) -> list[str]:
    """Break a section body into clause-sized text units."""
    units: list[str] = []
    for raw_line in markdown.split("\n"):
        line = raw_line.strip()
        if not line or _NOISE.match(line):
            continue
        if _BULLET.match(line):
            # Bullets/numbered items are atomic clauses on their own.
            units.append(_BULLET.sub("", line).strip())
            continue
        # Otherwise split the paragraph into sentences.
        for sent in _SENTENCE_SPLIT.split(line):
            sent = sent.strip()
            if len(sent) >= 12:  # ignore stray fragments
                units.append(sent)
    return units


def segment(doc: NormalizedDoc) -> list[Clause]:
    """Produce policy clauses from a normalized document."""
    clauses: list[Clause] = []
    seq = 0
    # Map paragraph text -> (page, ref) for best-effort provenance lookup.
    para_index = {p.text: (p.page, p.paragraph_ref) for p in doc.paragraphs}

    for sec in doc.sections:
        for unit in _clause_units(sec.markdown):
            seq += 1
            page, ref = _lookup_provenance(unit, para_index, sec)
            clauses.append(
                Clause(
                    clause_id=f"clause_{seq:04d}",
                    document_id=doc.document_id,
                    section_id=sec.section_id,
                    section_path=sec.section_path,
                    page_number=page,
                    paragraph_ref=ref,
                    clause_type=_classify(unit),
                    source_text=unit,
                )
            )
    return clauses


def segment_sections(doc: NormalizedDoc) -> list[Clause]:
    """One clause per meaningful *section* (default granularity).

    Feeds the LLM whole sections — tables and lists kept intact as context —
    instead of shredding prose into sentences. Boilerplate sections (purpose,
    definitions, revision history, audit, ...) are skipped so they cannot
    produce hallucinated rules.
    """
    clauses: list[Clause] = []
    seq = 0
    for sec in doc.sections:
        body = "\n".join(
            ln for ln in sec.markdown.split("\n") if not _NOISE.match(ln.strip())
        ).strip()
        if len(body) < 12:
            continue
        if is_boilerplate_section(sec.section_path, body):
            continue
        seq += 1
        clauses.append(
            Clause(
                clause_id=f"clause_{seq:04d}",
                document_id=doc.document_id,
                section_id=sec.section_id,
                section_path=sec.section_path,
                page_number=sec.page_start,
                paragraph_ref=sec.section_id,
                clause_type=_classify(sec.section_path + "\n" + body),
                source_text=body,
            )
        )
    return clauses


def _lookup_provenance(unit, para_index, sec):
    """Best-effort page/paragraph for a clause unit."""
    # Exact paragraph match first.
    if unit in para_index:
        return para_index[unit]
    # Fall back to any paragraph that contains the unit (sentence within a para).
    for text, (page, ref) in para_index.items():
        if unit in text:
            return page, ref
    return sec.page_start, None
