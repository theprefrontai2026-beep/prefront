"""Learn an arbitrary MCP server's tools and turn them into a physical catalog.

Client-side analog of ``catalog.py``: where that module parses DDL or introspects
a live Postgres database into a ``PhysicalCatalog``, this module connects to any
API-based MCP server, lists its tools, and represents each one as a table so the
EXISTING deterministic pipeline (bindings -> query templates -> tools -> validate)
runs unmodified — a tool's name is the table name, and each input-schema property
is a column. Tools aren't joinable, so no primary key / foreign keys are invented.

Deterministic: no LLM. Nothing here executes a tool — that's the runtime's job
(``semantic-mcp-server/semanticmcp/mcp_proxy.py``), gated by governance.
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional

# Imported as a MODULE, not `from mcp.client.sse import sse_client`: the tracing
# layer instruments the module attribute `mcp.client.sse.sse_client`, and a
# from-import binds the original before setup() runs — see loanpro-demo/
# ungoverned_server.py and securebank-demo/governed_agent.py for the same trap.
import mcp.client.sse as mcp_sse
from mcp import ClientSession

from .logutil import get_logger
from .schema import PhysicalCatalog, PhysicalColumn, PhysicalTable

log = get_logger(__name__)

_JSON_SCHEMA_TYPES = {"string", "integer", "number", "boolean", "array", "object"}


def _param_type(spec: dict) -> str:
    """An MCP tool's inputSchema property is already JSON Schema — unlike a SQL
    column type, its ``type`` needs no translation, just a safe fallback for the
    shapes Prefront's coarse type vocabulary doesn't model (enum-only, $ref,
    anyOf/oneOf, a type array with 'null')."""
    t = spec.get("type")
    if isinstance(t, list):
        t = next((x for x in t if x != "null"), None)
    return t if t in _JSON_SCHEMA_TYPES else "string"


async def list_mcp_tools(server_url: str, headers: Optional[dict[str, str]] = None) -> list[dict]:
    """Connect to ``server_url`` and return each tool as a plain dict:
    ``{name, description, input_schema, destructive}``.

    ``destructive`` reflects the tool's own MCP annotations (``destructiveHint`` /
    ``readOnlyHint``) when the upstream server sets them; absent annotations are
    treated as "unknown, assume not destructive" rather than guessed.
    """
    async with mcp_sse.sse_client(server_url, headers=headers or {}) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            listing = await session.list_tools()
    tools: list[dict] = []
    for t in listing.tools:
        annotations = getattr(t, "annotations", None)
        destructive = bool(getattr(annotations, "destructiveHint", False)) or not bool(
            getattr(annotations, "readOnlyHint", True) if annotations else True
        )
        tools.append({
            "name": t.name,
            "description": (t.description or "").strip(),
            "input_schema": t.inputSchema or {"type": "object", "properties": {}},
            "destructive": destructive,
        })
    return tools


def list_mcp_tools_sync(server_url: str, headers: Optional[dict[str, str]] = None) -> list[dict]:
    """Sync wrapper for the FastAPI (plain ``def``) handlers in ``api.py`` — those
    run in a worker thread with no event loop of their own, so ``asyncio.run`` is
    safe here."""
    return asyncio.run(list_mcp_tools(server_url, headers))


def build_catalog_from_mcp(
    server_url: str, tools: list[dict], *, datasource_id: str
) -> PhysicalCatalog:
    """One ``PhysicalTable`` per tool, one ``PhysicalColumn`` per input-schema
    property. No primary key / foreign keys — tools aren't joinable; the rest of
    the pipeline already tolerates a table with no key (``querygen._pk_bare``)."""
    tables: list[PhysicalTable] = []
    for t in tools:
        schema = t.get("input_schema") or {}
        props: dict[str, Any] = schema.get("properties") or {}
        required = set(schema.get("required") or [])
        columns = [
            PhysicalColumn(
                name=name,
                type=_param_type(spec or {}),
                nullable=name not in required,
            )
            for name, spec in props.items()
        ]
        tables.append(PhysicalTable(
            name=t["name"],
            description=t.get("description", ""),
            mcp_destructive=bool(t.get("destructive")),
            columns=columns,
        ))
    log.debug("build_catalog_from_mcp: server=%s tools=%d", server_url, len(tables))
    return PhysicalCatalog(
        datasource_id=datasource_id, type="mcp", tables=tables, mcp_server_url=server_url,
    )
