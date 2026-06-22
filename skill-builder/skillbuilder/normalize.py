"""Markdown normalizer.

Turns raw extracted text into *canonical markdown* and a structured list of
:class:`~skillbuilder.schema.Section` objects. Goals (design.md "Markdown
Normalizer"): preserve headings, numbered sections, bullets, and page/section
references, and assign stable paragraph IDs so every downstream rule can cite
``[page:N paragraph:pNNN]``.

Deterministic and dependency-free — no LLM involved at this stage.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .schema import Section

# A markdown ATX heading: "## 3.1 Discount Thresholds"
_ATX_HEADING = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
# A bare numbered heading: "3.1 Discount Thresholds" / "4.1.3 Holds"
# Short trailing title, no terminal punctuation — distinguishes from prose that
# merely starts with a number.
_NUMBERED_HEADING = re.compile(r"^(\d+(?:\.\d+){0,3})\s+([A-Z][^.\n]{1,80})$")
# Page marker emitted by the PDF extractor.
_PAGE_MARKER = re.compile(r"^<<<PAGE\s+(\d+)>>>\s*$")


@dataclass
class Paragraph:
    paragraph_ref: str  # e.g. "p004"
    page: int | None
    section_path: str
    text: str


@dataclass
class NormalizedDoc:
    document_id: str
    version: str
    file_name: str
    canonical_markdown: str
    sections: list[Section] = field(default_factory=list)
    paragraphs: list[Paragraph] = field(default_factory=list)


def normalize(
    raw_text: str,
    *,
    document_id: str,
    version: str,
    file_name: str,
    title: str | None = None,
) -> NormalizedDoc:
    """Normalize ``raw_text`` into canonical markdown + structured sections."""
    lines = raw_text.replace("\r\n", "\n").replace("\r", "\n").split("\n")

    page = None
    para_counter = 0
    # Accumulators for the current section.
    cur_path = "Preamble"
    cur_heading = "Preamble"
    cur_level = 1
    cur_page_start: int | None = None
    cur_page_end: int | None = None
    cur_md_lines: list[str] = []

    sections: list[Section] = []
    paragraphs: list[Paragraph] = []
    section_seq = 0

    def flush_section() -> None:
        nonlocal section_seq
        body = "\n".join(cur_md_lines).strip("\n")
        if not body and cur_path == "Preamble":
            return  # skip an empty preamble
        section_seq += 1
        sections.append(
            Section(
                section_id=f"sec_{section_seq:03d}",
                section_path=cur_path,
                heading=cur_heading,
                page_start=cur_page_start,
                page_end=cur_page_end if cur_page_end is not None else cur_page_start,
                markdown=body,
            )
        )

    for line in lines:
        pm = _PAGE_MARKER.match(line)
        if pm:
            page = int(pm.group(1))
            cur_page_end = page
            if cur_page_start is None:
                cur_page_start = page
            continue

        heading_text: str | None = None
        heading_level = 2
        atx = _ATX_HEADING.match(line)
        if atx:
            heading_level = len(atx.group(1))
            heading_text = atx.group(2).strip()
        else:
            num = _NUMBERED_HEADING.match(line.strip())
            if num:
                heading_text = num.group(0).strip()
                heading_level = 2 + num.group(1).count(".")

        if heading_text is not None:
            flush_section()
            cur_path = heading_text
            cur_heading = heading_text
            cur_level = heading_level
            cur_page_start = page
            cur_page_end = page
            cur_md_lines = []
            continue

        if line.strip() == "":
            cur_md_lines.append("")
            continue

        # A content paragraph/bullet line. Assign a stable ref.
        para_counter += 1
        ref = f"p{para_counter:03d}"
        paragraphs.append(
            Paragraph(paragraph_ref=ref, page=page, section_path=cur_path, text=line.strip())
        )
        cur_md_lines.append(line.rstrip())

    flush_section()

    canonical_markdown = _render_canonical(
        title or file_name, document_id, version, sections
    )
    return NormalizedDoc(
        document_id=document_id,
        version=version,
        file_name=file_name,
        canonical_markdown=canonical_markdown,
        sections=sections,
        paragraphs=paragraphs,
    )


def _render_canonical(
    title: str, document_id: str, version: str, sections: list[Section]
) -> str:
    """Render sections back into a clean, citation-friendly markdown document."""
    out: list[str] = [f"# {title}", ""]
    out.append(f"Source: {document_id}")
    out.append(f"Version: {version}")
    out.append("")
    for sec in sections:
        if sec.section_path == "Preamble":
            if sec.markdown.strip():
                out.append(sec.markdown.strip())
                out.append("")
            continue
        out.append(f"## {sec.section_path}")
        out.append("")
        if sec.page_start is not None:
            out.append(f"[page:{sec.page_start}]")
            out.append("")
        if sec.markdown.strip():
            out.append(sec.markdown.strip())
            out.append("")
    return "\n".join(out).rstrip() + "\n"
