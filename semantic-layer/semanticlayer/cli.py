"""Command-line interface.

    # Build the Core 6 semantic-layer artifacts from a schema + approved policy:
    python -m semanticlayer build \\
        --schema /path/to/schema.sql \\
        --rules  /path/to/skills/cr_fin_001/v3.2 \\
        --model-id example_semantic_model --domain example \\
        --out ./out/example --provider groq

    # Re-run the publish-time validator over a generated set:
    python -m semanticlayer validate --in ./out/example

    # Serve the generated tool contracts as an MCP server (stdio):
    python -m semanticlayer serve --in ./out/example
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

from .artifacts import write_artifacts
from .llm import LLMClient
from .pipeline import run_pipeline


def _kv_pairs(items: list[str], *, sep: str = "=") -> dict[str, str]:
    """Parse repeatable 'key=value' CLI args into a dict."""
    out: dict[str, str] = {}
    for item in items or []:
        if sep in item:
            k, v = item.split(sep, 1)
            out[k.strip()] = v.strip()
    return out


def _cmd_build(args: argparse.Namespace) -> int:
    client = LLMClient(provider=args.provider, model=args.model)
    print(f"Mapping with provider={client.provider} model={client.model}", file=sys.stderr)

    result = run_pipeline(
        args.schema,
        args.rules,
        model_id=args.model_id,
        datasource_id=args.datasource_id,
        domain=args.domain,
        version=args.version,
        client=client,
        metrics=_kv_pairs(args.metric, sep="="),
        caller_context=_kv_pairs(args.caller_scope, sep="="),
    )

    print(
        f"entities={len(result.model.entities)} "
        f"relationships={len(result.relationships)} "
        f"sensitive_fields={len(result.sensitivity)} "
        f"intents={len(result.bindings)} templates={len(result.templates)} "
        f"tools={len(result.tools)} model_status={result.model.status}",
        file=sys.stderr,
    )
    for e in result.errors:
        print(f"  ! mapper: {e}", file=sys.stderr)
    for e in result.validation.errors:
        print(f"  ✗ validation: {e}", file=sys.stderr)
    for w in result.validation.warnings:
        print(f"  ⚠ {w}", file=sys.stderr)

    written = write_artifacts(result, args.out)
    print("\nWrote:")
    for path in written.values():
        print(f"  {path}")
    return 0 if result.validation.ok else 1


def _cmd_import_dbt(args: argparse.Namespace) -> int:
    """Translate a customer dbt semantic model + Prefront overlay into the Core 6
    artifacts — the deterministic (no-LLM) counterpart to ``build``."""
    from .catalog import build_catalog_from_dsn, build_catalog_from_file
    from .pipeline import run_import_pipeline

    if not (args.schema or args.dsn):
        print("error: provide the datasource schema via --schema or --dsn", file=sys.stderr)
        return 2
    dbt_text = Path(args.dbt).read_text(encoding="utf-8")
    overlay_text = Path(args.overlay).read_text(encoding="utf-8")
    print(f"Importing dbt model {args.dbt} + overlay {args.overlay}", file=sys.stderr)

    catalog = (
        build_catalog_from_dsn(args.dsn, datasource_id=args.datasource_id or "datasource")
        if args.dsn else build_catalog_from_file(args.schema, datasource_id=args.datasource_id)
    )
    result = run_import_pipeline(
        dbt_text, overlay_text, catalog,
        model_id=args.model_id, domain=args.domain, version=args.version,
    )

    rep = result.report or {}
    print(
        f"entities={len(result.model.entities)} "
        f"relationships(approved)={len(result.relationships)} "
        f"relationships(dropped)={len(rep.get('relationships_dropped', []))} "
        f"sensitive_fields={len(result.sensitivity)} "
        f"intents={len(result.bindings)} templates={len(result.templates)} "
        f"tools={len(result.tools)} model_status={result.model.status}",
        file=sys.stderr,
    )
    for j in rep.get("relationships_dropped", []):
        print(f"  ⤫ dropped join {j.get('from')} -> {j.get('to')}: {j.get('reason')}", file=sys.stderr)
    for w in rep.get("warnings", []):
        print(f"  ⚠ {w}", file=sys.stderr)
    for e in result.validation.errors:
        print(f"  ✗ validation: {e}", file=sys.stderr)

    written = write_artifacts(result, args.out)
    print("\nWrote:")
    for path in written.values():
        print(f"  {path}")
    return 0 if result.validation.ok else 1


def _cmd_validate(args: argparse.Namespace) -> int:
    from .schema import (
        IntentBinding, McpTool, PhysicalCatalog, Relationship, SemanticModel, SensitivityRule,
    )
    from .validate import validate

    d = Path(args.in_dir)

    def _y(name: str) -> dict:
        return yaml.safe_load((d / name).read_text(encoding="utf-8")) or {}

    catalog = _catalog_from_yaml(_y("physical_catalog.yaml"))
    model = _model_from_yaml(_y("semantic_model.yaml"))
    rels = _rels_from_yaml(_y("relationships.yaml"))
    sens = _sens_from_yaml(_y("sensitivity.yaml"))
    bindings = _bindings_from_yaml(_y("intent_bindings.yaml"))
    templates = _templates_from_yaml(_y("query_templates.yaml"))
    tools = _tools_from_yaml(_y("mcp_tools.yaml"))

    result = validate(catalog, model, rels, sens, bindings, templates, tools)
    if result.ok:
        print(f"OK — {d} passes all publish-time checks.")
        return 0
    for e in result.errors:
        print(f"  ✗ {e}", file=sys.stderr)
    print(f"\n{len(result.errors)} error(s).", file=sys.stderr)
    return 1


def _cmd_serve(args: argparse.Namespace) -> int:
    from .mcp_server import serve

    print(f"Serving MCP tools from {args.in_dir} over stdio…", file=sys.stderr)
    serve(args.in_dir)
    return 0


def _cmd_api(args: argparse.Namespace) -> int:
    import uvicorn

    print(f"Serving semantic-layer API on http://{args.host}:{args.port}", file=sys.stderr)
    uvicorn.run("semanticlayer.api:app", host=args.host, port=args.port)
    return 0


# --- YAML -> models (for validate/serve over already-written artifacts) -------


def _catalog_from_yaml(doc: dict):
    from .schema import ForeignKey, PhysicalCatalog, PhysicalColumn, PhysicalTable

    tables = []
    for tname, t in (doc.get("tables") or {}).items():
        pk = t.get("primary_key")
        pk = [pk] if isinstance(pk, str) else (pk or [])
        cols = [
            PhysicalColumn(
                name=cn, type=c.get("type", "text"), nullable=c.get("nullable", True),
                is_primary_key=cn in pk, enum_values=c.get("enum_values"),
                markers=c.get("markers", []),
            )
            for cn, c in (t.get("columns") or {}).items()
        ]
        fks = [
            ForeignKey(from_columns=fk["from"], to_table=fk["to_table"], to_columns=fk["to_columns"])
            for fk in (t.get("foreign_keys") or [])
        ]
        tables.append(PhysicalTable(name=tname, primary_key=pk, columns=cols, foreign_keys=fks))
    return PhysicalCatalog(
        datasource_id=doc.get("datasource_id", "ds"), type=doc.get("type", "postgresql"),
        schema_version=str(doc.get("schema_version", "1.0")), status=doc.get("status", "published"),
        tables=tables,
    )


def _model_from_yaml(doc: dict):
    from .schema import SemanticAttribute, SemanticEntity, SemanticModel

    entities = []
    for ekey, e in (doc.get("entities") or {}).items():
        attrs = [
            SemanticAttribute(
                attribute_key=akey, column=a["column"], type=a.get("type", "string"),
                required=a.get("required", False),
                sensitivity_level=a.get("sensitivity_level", "normal"),
            )
            for akey, a in (e.get("attributes") or {}).items()
        ]
        entities.append(SemanticEntity(
            entity_key=ekey, description=e.get("description", ""),
            primary_table=e["primary_table"], primary_key=e.get("primary_key", ""),
            synonyms=e.get("synonyms", []), restricted=e.get("restricted", False), attributes=attrs,
        ))
    return SemanticModel(
        semantic_model_id=doc.get("semantic_model_id", "model"),
        version=str(doc.get("version", "1.0")), status=doc.get("status", "draft"),
        domain=doc.get("domain", "general"), approved_by=doc.get("approved_by"),
        generated_by=doc.get("generated_by", ""), entities=entities,
    )


def _rels_from_yaml(doc: dict):
    from .schema import Join, Relationship

    out = []
    for key, r in (doc.get("relationships") or {}).items():
        out.append(Relationship(
            relationship_key=key, from_entity=r["from_entity"], to_entity=r["to_entity"],
            join=Join(**{"from": r["join"]["from"], "to": r["join"]["to"]}),
            cardinality=r.get("cardinality", "one_to_many"), approved=r.get("approved", False),
            restricted=r.get("restricted", False), allowed_roles=r.get("allowed_roles", []),
        ))
    return out


def _sens_from_yaml(doc: dict):
    from .schema import SensitivityRule

    out = []
    for key, s in (doc.get("sensitivity") or {}).items():
        out.append(SensitivityRule(
            key=key, physical_column=s["physical_column"],
            classification=s.get("classification", "confidential_business"),
            sensitivity_level=s.get("sensitivity_level", "confidential"),
            default_access=s.get("default_access", "deny"),
            allowed_roles=s.get("allowed_roles", []),
        ))
    return out


def _bindings_from_yaml(doc: dict):
    from .schema import IntentBinding, MandatoryFilter

    out = []
    for intent, b in (doc.get("intent_bindings") or {}).items():
        out.append(IntentBinding(
            intent_id=intent, description=b.get("description", ""),
            required_entities=b.get("required_entities", []),
            optional_entities=b.get("optional_entities", []),
            allowed_attributes=b.get("allowed_attributes", []),
            restricted_attributes=b.get("restricted_attributes", []),
            mandatory_filters=[MandatoryFilter(**f) for f in b.get("mandatory_filters", [])],
            template_ids=b.get("template_ids", []), policies=b.get("policies", []),
            trace_required=b.get("trace_required", True),
        ))
    return out


def _templates_from_yaml(doc: dict):
    from .schema import QueryTemplate, ResultColumn, TemplateParameter, WriteAction

    out = []
    for tid, t in (doc.get("query_templates") or {}).items():
        wa = t.get("write_action")
        out.append(QueryTemplate(
            template_id=tid, intent_id=t.get("intent_id", tid),
            semantic_model_id=t.get("semantic_model_id", "model"),
            semantic_model_version=str(t.get("semantic_model_version", "1.0")),
            kind=t.get("kind", "read"),
            semantic_entities=t.get("semantic_entities", []),
            read_only=t.get("read_only", True), dialect=t.get("dialect", "postgres"),
            sql=t.get("sql", ""),
            parameters=[TemplateParameter(**p) for p in t.get("parameters", [])],
            required_caller_context=t.get("required_caller_context", []),
            result_columns=[ResultColumn(**rc) for rc in t.get("result_columns", [])],
            decision_inputs=[ResultColumn(**rc) for rc in t.get("decision_inputs", [])],
            write_action=(WriteAction(**wa) if wa else None),
            required_policies=t.get("required_policies", []),
            runtime_policy_predicates=t.get("runtime_policy_predicates", []),
            status=t.get("status", "published"),
        ))
    return out


def _tools_from_yaml(doc: dict):
    from .schema import ApprovalBehavior, McpTool, TraceSpec

    out = []
    for name, t in (doc.get("mcp_tools") or {}).items():
        ab = t.get("approval_behavior")
        out.append(McpTool(
            tool_name=name, tool_version=str(t.get("tool_version", "1.0")),
            source_intent=t.get("source_intent", name),
            semantic_model_id=t.get("semantic_model_id", "model"),
            semantic_model_version=str(t.get("semantic_model_version", "1.0")),
            description=t.get("description", ""), allowed_roles=t.get("allowed_roles", []),
            input_schema=t.get("input_schema", {}), output_schema=t.get("output_schema", {}),
            result_shape=t.get("result_shape", {}), policies_enforced=t.get("policies_enforced", []),
            approval_behavior=(ApprovalBehavior(**ab) if ab else None),
            trace=TraceSpec(**(t.get("trace") or {})), status=t.get("status", "draft"),
        ))
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="semanticlayer", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    b = sub.add_parser("build", help="Build the Core 6 semantic-layer artifacts")
    b.add_argument("--schema", required=True, help="Path to the datasource DDL (.sql)")
    b.add_argument("--rules", required=True, help="Path to a skill-builder skill version dir")
    b.add_argument("--model-id", default="semantic_model", help="Semantic model id")
    b.add_argument("--datasource-id", default=None, help="Datasource id (default: schema filename)")
    b.add_argument("--domain", default=None, help="Domain (default: from policy)")
    b.add_argument("--version", default="1.0", help="Semantic model version")
    b.add_argument("--out", default="./out", help="Output directory")
    b.add_argument("--provider", default=None,
                   choices=["nvidia", "deepseek", "grok", "xai", "groq", "openai"])
    b.add_argument("--model", default=None, help="Override mapper model")
    b.add_argument("--metric", action="append", default=[],
                   help="Derived metric, name=expression (repeatable)")
    b.add_argument("--caller-scope", action="append", default=[],
                   help="Caller attribute=scoping column, e.g. region=region_id (repeatable)")
    b.set_defaults(func=_cmd_build)

    i = sub.add_parser("import-dbt",
                       help="Translate a customer dbt semantic model + Prefront overlay (no LLM)")
    i.add_argument("--dbt", required=True, help="Path to the dbt semantic_models YAML")
    i.add_argument("--overlay", required=True, help="Path to the Prefront governance overlay YAML")
    i.add_argument("--schema", default=None, help="Path to the datasource DDL (.sql)")
    i.add_argument("--dsn", default=None, help="Live datasource DSN (alternative to --schema)")
    i.add_argument("--model-id", default="semantic_model", help="Semantic model id")
    i.add_argument("--datasource-id", default=None, help="Datasource id (default: schema filename)")
    i.add_argument("--domain", default=None, help="Domain (default: from overlay)")
    i.add_argument("--version", default="1.0", help="Semantic model version")
    i.add_argument("--out", default="./out", help="Output directory")
    i.set_defaults(func=_cmd_import_dbt)

    v = sub.add_parser("validate", help="Re-run publish-time checks over a generated set")
    v.add_argument("--in", dest="in_dir", required=True, help="Artifact directory")
    v.set_defaults(func=_cmd_validate)

    s = sub.add_parser("serve", help="Serve generated tool contracts as an MCP server")
    s.add_argument("--in", dest="in_dir", required=True, help="Artifact directory")
    s.set_defaults(func=_cmd_serve)

    a = sub.add_parser("api", help="Serve the design-time HTTP API (datasource introspection)")
    a.add_argument("--host", default="0.0.0.0", help="Bind host")
    a.add_argument("--port", type=int, default=8010, help="Bind port")
    a.set_defaults(func=_cmd_api)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
