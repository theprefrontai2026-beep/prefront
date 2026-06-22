"""Command-line interface.

    python -m skillbuilder build <doc> --doc-id CR-FIN-001 --version 3.2 \
        --domain credit_approval --out ./skills

Runs the full pipeline and writes the five skill artifacts. Use --dry-run to
skip the LLM (segment-only, to inspect clauses), and --roles/--fields/--intents
to ground the extractor.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .llm import ExtractionContext, RuleExtractor
from .logconfig import setup_logging
from .pipeline import run_pipeline, write_artifacts
from .segment import segment, segment_sections
from .normalize import normalize
from . import extract as extract_mod


def _csv(value: str | None) -> list[str]:
    return [v.strip() for v in value.split(",")] if value else []


def _cmd_build(args: argparse.Namespace) -> int:
    ctx = ExtractionContext(
        domain=args.domain,
        known_roles=_csv(args.roles),
        known_fields=_csv(args.fields),
        known_intents=_csv(args.intents),
    )

    if args.dry_run:
        return _dry_run(args)

    extractor = RuleExtractor(provider=args.provider, model=args.model)
    print(
        f"Extracting with provider={extractor.provider} model={extractor.model}",
        file=sys.stderr,
    )

    result = run_pipeline(
        args.document,
        document_id=args.doc_id,
        version=args.version,
        domain=args.domain,
        title=args.name,
        context=ctx,
        extractor=extractor,
        granularity=args.granularity,
    )

    print(
        f"clauses={len(result.clauses)} "
        f"candidate_rules={len(result.candidates)} "
        f"skipped_clauses={result.skipped_clauses} "
        f"conflicts={len(result.conflicts)} "
        f"errors={len(result.errors)}",
        file=sys.stderr,
    )
    for err in result.errors:
        print(f"  ! {err}", file=sys.stderr)
    for c in result.conflicts:
        print(f"  ⚠ [{c.severity}] {c.type}: {c.message}", file=sys.stderr)

    written = write_artifacts(
        result,
        out_root=args.out,
        skill_id=args.skill_id or args.doc_id.lower().replace("-", "_"),
        name=args.name or args.doc_id,
        domain=args.domain,
        file_name=Path(args.document).name,
        owner=args.owner,
        effective_from=args.effective_from,
        known_roles=ctx.known_roles,
        known_fields=ctx.known_fields,
    )
    print("\nWrote:")
    for name, path in written.items():
        print(f"  {path}")
    return 0


def _dry_run(args: argparse.Namespace) -> int:
    raw = extract_mod.extract_text(args.document)
    doc = normalize(
        raw,
        document_id=args.doc_id,
        version=args.version,
        file_name=Path(args.document).name,
        title=args.name,
    )
    clauses = segment_sections(doc) if args.granularity == "section" else segment(doc)
    print(f"sections={len(doc.sections)} units={len(clauses)} ({args.granularity})\n")
    for c in clauses:
        text = c.source_text.replace("\n", " ")
        print(f"[{c.clause_type:>20}] {c.section_path}: {text[:90]}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="skillbuilder", description=__doc__)
    parser.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="Debug output to stderr: -v stage trace, -vv per-clause LLM detail",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    b = sub.add_parser("build", help="Run the full pipeline and write artifacts")
    b.add_argument("document", help="Path to policy document (.md/.txt/.docx/.pdf)")
    b.add_argument("--doc-id", required=True, help="Stable document id, e.g. CR-FIN-001")
    b.add_argument("--version", required=True, help="Document version, e.g. 3.2")
    b.add_argument("--domain", default="general")
    b.add_argument("--skill-id", default=None, help="Defaults to doc-id lowercased")
    b.add_argument("--name", default=None, help="Human-readable skill name")
    b.add_argument("--owner", default=None)
    b.add_argument("--effective-from", default=None)
    b.add_argument("--out", default="./skills", help="Output registry root")
    b.add_argument("--roles", default=None, help="Comma-separated known roles")
    b.add_argument("--fields", default=None, help="Comma-separated known fields")
    b.add_argument("--intents", default=None, help="Comma-separated known intents")
    b.add_argument(
        "--provider",
        choices=["nvidia", "deepseek", "grok", "xai", "groq", "openai"],
        default=None,
        help="LLM provider preset (default: SKILLBUILDER_PROVIDER env or nvidia)",
    )
    b.add_argument("--model", default=None, help="Override extraction model")
    b.add_argument(
        "--granularity",
        choices=["section", "clause"],
        default="section",
        help="Extraction unit: whole sections (default) or per-sentence clauses",
    )
    b.add_argument("--dry-run", action="store_true", help="Segment only, skip the LLM")
    b.set_defaults(func=_cmd_build)

    args = parser.parse_args(argv)
    setup_logging(args.verbose)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
