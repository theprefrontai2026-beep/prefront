"""Document extraction: raw file -> plain text.

Guards the ingestion seam that lets a customer hand us the same policy as a
PDF instead of markdown. The `.pdf` path was always dispatched (extract.py),
but `pypdf` was undeclared in requirements — so a PDF upload failed at runtime
with an ExtractionError. These tests pin the contract and would have caught it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from skillbuilder.extract import (
    SUPPORTED_SUFFIXES,
    ExtractionError,
    extract_text,
)
from skillbuilder.normalize import normalize
from skillbuilder.segment import segment_sections

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SECUREBANK_PDF = FIXTURES / "securebank_rules.pdf"

# Hard rules that must survive extraction regardless of source format
# (the SecureBank BRD's non-negotiable controls — see §5/§7 of the doc).
SECUREBANK_RULE_MARKERS = [
    "daily transfer limit",
    "permission matrix",
    "maker-checker",
    "FR-TXN-2",
    "Account Holder",
    "Bank Teller",
    "Bank Manager",
]


def test_pdf_is_a_supported_suffix():
    assert ".pdf" in SUPPORTED_SUFFIXES


def test_markdown_and_text_are_read_verbatim(tmp_path):
    p = tmp_path / "policy.md"
    p.write_text("# Title\n\nDeny by default.\n", encoding="utf-8")
    assert extract_text(p) == "# Title\n\nDeny by default.\n"


def test_unsupported_suffix_raises(tmp_path):
    p = tmp_path / "policy.rtf"
    p.write_text("x", encoding="utf-8")
    with pytest.raises(ExtractionError):
        extract_text(p)


def test_missing_file_raises(tmp_path):
    with pytest.raises(ExtractionError):
        extract_text(tmp_path / "nope.pdf")


def test_pdf_extracts_text_with_page_markers():
    """A text-based PDF yields extractable text tagged with <<<PAGE N>>>
    markers the normalizer turns into page references."""
    pytest.importorskip("pypdf")
    raw = extract_text(SECUREBANK_PDF)
    assert raw.strip()
    assert "<<<PAGE 1>>>" in raw
    # SecureBank's non-negotiable rules come through the PDF text layer.
    for marker in SECUREBANK_RULE_MARKERS:
        assert marker.lower() in raw.lower(), f"missing rule text: {marker}"


def test_pdf_flows_through_normalize_and_segment_into_clauses():
    """The PDF rejoins the same deterministic chain as markdown: extracted
    text -> normalized sections -> segmented clauses ready for rule extraction."""
    pytest.importorskip("pypdf")
    raw = extract_text(SECUREBANK_PDF)
    doc = normalize(
        raw, document_id="securebank-brd", version="2.0", file_name="securebank_rules.pdf"
    )
    clauses = segment_sections(doc)
    assert doc.sections, "PDF produced no sections"
    assert clauses, "PDF produced no clauses to extract rules from"
    # The functional-requirements sections (§5.x) carry the enforceable rules.
    paths = " ".join(s.section_path for s in doc.sections)
    assert "5.5" in paths or "Transactions" in paths
