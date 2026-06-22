"""Document extraction: raw bytes/path -> plain text.

Deterministic extraction only (design.md "Document Extractor"). Markdown and
text are read directly. DOCX and text-based PDF are supported *if* the optional
libraries are installed; otherwise we raise a clear, actionable error rather
than guessing. Scanned-PDF OCR is intentionally out of scope for the MVP.
"""

from __future__ import annotations

from pathlib import Path

SUPPORTED_SUFFIXES = (".md", ".markdown", ".txt", ".text", ".docx", ".pdf")


class ExtractionError(RuntimeError):
    """Raised when a document cannot be extracted to text."""


def extract_text(path: str | Path) -> str:
    """Return the textual content of ``path`` as a single string.

    Dispatches on file suffix. Raises :class:`ExtractionError` for unsupported
    types or missing optional dependencies.
    """
    p = Path(path)
    if not p.is_file():
        raise ExtractionError(f"not a file: {p}")

    suffix = p.suffix.lower()
    if suffix in (".md", ".markdown", ".txt", ".text"):
        return p.read_text(encoding="utf-8")
    if suffix == ".docx":
        return _extract_docx(p)
    if suffix == ".pdf":
        return _extract_pdf(p)

    raise ExtractionError(
        f"unsupported file type {suffix!r}; supported: {', '.join(SUPPORTED_SUFFIXES)}"
    )


def _extract_docx(p: Path) -> str:
    try:
        import docx  # python-docx
    except ImportError as e:  # pragma: no cover - depends on optional dep
        raise ExtractionError(
            "DOCX support needs python-docx. Install with: pip install python-docx"
        ) from e

    document = docx.Document(str(p))
    lines: list[str] = []
    for para in document.paragraphs:
        text = para.text.strip()
        if not text:
            lines.append("")
            continue
        # Preserve heading levels so the normalizer can rebuild structure.
        style = (para.style.name or "").lower() if para.style else ""
        if style.startswith("heading"):
            level = "".join(ch for ch in style if ch.isdigit()) or "1"
            lines.append("#" * min(int(level), 6) + " " + text)
        else:
            lines.append(text)
    return "\n".join(lines)


def _extract_pdf(p: Path) -> str:
    try:
        import pypdf
    except ImportError as e:  # pragma: no cover - depends on optional dep
        raise ExtractionError(
            "PDF support needs pypdf. Install with: pip install pypdf "
            "(scanned PDFs need OCR, which is out of scope for the MVP)"
        ) from e

    reader = pypdf.PdfReader(str(p))
    pages: list[str] = []
    for i, page in enumerate(reader.pages, start=1):
        text = (page.extract_text() or "").strip()
        if not text:
            continue
        # Emit a page marker the normalizer turns into [page:N] references.
        pages.append(f"<<<PAGE {i}>>>\n{text}")
    if not pages:
        raise ExtractionError(
            f"no extractable text in {p.name}; it may be a scanned PDF (OCR not supported)"
        )
    return "\n\n".join(pages)
