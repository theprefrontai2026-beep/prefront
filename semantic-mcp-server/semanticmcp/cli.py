"""Command-line interface.

    # Serve the query templates as an MCP server (stdio):
    python -m semanticmcp serve \\
        --templates ../semantic-layer/out/example/query_templates.yaml \\
        --database-url postgresql://example:example@localhost:5432/example

    # Run one template directly (no MCP client needed) — handy for testing:
    python -m semanticmcp call get_customer_credit \\
        --args '{"customer_id": 4, "caller_region": "EMEA"}'

    # Check DB connectivity + that templates load:
    python -m semanticmcp doctor
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from . import db
from .server import call_template, load_templates, serve, serve_http

DEFAULT_TEMPLATES = "../semantic-layer/out/example/query_templates.yaml"


def _dsn(args: argparse.Namespace) -> str:
    return args.database_url or os.environ.get("DATABASE_URL") or db.DEFAULT_DSN


def _cmd_serve(args: argparse.Namespace) -> int:
    # Governance config flows via env so the whole stack (server, identity,
    # writes, traces) reads one source of truth.
    if args.policy:
        os.environ["POLICY_PATH"] = args.policy
    if args.act_as:
        os.environ["ACT_AS"] = args.act_as
    if args.enable_writes:
        os.environ["ENABLE_WRITES"] = "1"
    if args.http:
        print(f"Serving templates {args.templates} -> MCP over HTTP "
              f"http://{args.host}:{args.port}/sse; db={_dsn(args)}", file=sys.stderr)
        serve_http(_dsn(args), args.templates, host=args.host, port=args.port)
    else:
        print(f"Serving templates {args.templates} -> MCP (stdio); db={_dsn(args)}", file=sys.stderr)
        serve(_dsn(args), args.templates)
    return 0


def _cmd_call(args: argparse.Namespace) -> int:
    tools = load_templates(args.templates)
    call_args = json.loads(args.args) if args.args else {}
    result = call_template(tools, _dsn(args), args.tool, call_args)
    print(json.dumps(result, indent=2, default=str))
    return 1 if "error" in result else 0


def _cmd_doctor(args: argparse.Namespace) -> int:
    ok = True
    try:
        tools = load_templates(args.templates)
        print(f"templates: {len(tools)} loaded -> {', '.join(tools)}")
    except Exception as e:
        ok = False
        print(f"templates: FAILED to load {args.templates}: {e}", file=sys.stderr)
    try:
        print(f"database: OK -> {db.ping(_dsn(args))}")
    except Exception as e:
        ok = False
        print(f"database: UNREACHABLE ({_dsn(args)}): {e}", file=sys.stderr)
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="semanticmcp", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--templates", default=DEFAULT_TEMPLATES,
                        help="Path to query_templates.yaml")
    parser.add_argument("--database-url", default=None,
                        help="Postgres DSN (default: $DATABASE_URL or the demo DSN)")
    sub = parser.add_subparsers(dest="command", required=True)

    s = sub.add_parser("serve", help="Expose templates as an MCP server (stdio or --http)")
    s.add_argument("--http", action="store_true", help="Serve over HTTP (SSE) instead of stdio")
    s.add_argument("--host", default="0.0.0.0", help="HTTP bind host (with --http)")
    s.add_argument("--port", type=int, default=8090, help="HTTP port (with --http)")
    s.add_argument("--policy", default=None,
                   help="Policy bundle path (default: $POLICY_PATH or policy.yaml beside templates)")
    s.add_argument("--act-as", default=None,
                   help="Caller identity bound to :who in $IDENTITY_QUERY (default: $ACT_AS)")
    s.add_argument("--enable-writes", action="store_true",
                   help="Execute write_actions on ALLOWED decisions (default: dry-run)")
    s.set_defaults(func=_cmd_serve)

    c = sub.add_parser("call", help="Run one template directly")
    c.add_argument("tool", help="Tool/intent name")
    c.add_argument("--args", default=None, help="JSON object of arguments")
    c.set_defaults(func=_cmd_call)

    sub.add_parser("doctor", help="Check DB connectivity + template load").set_defaults(func=_cmd_doctor)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
