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

# JSON Schema keywords worth showing an operator verbatim. Kept as a list rather
# than dumping the whole spec dict so the display carries the constraints that
# describe a value's SHAPE, not schema plumbing ($schema, $id, title).
_CONSTRAINT_KEYS = (
    "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf",
    "minLength", "maxLength", "pattern", "minItems", "maxItems", "uniqueItems",
)


def _param_type(spec: dict) -> str:
    """An MCP tool's inputSchema property is already JSON Schema — unlike a SQL
    column type, its ``type`` needs no translation, just a safe fallback for the
    shapes Prefront's coarse type vocabulary doesn't model (enum-only, $ref,
    anyOf/oneOf, a type array with 'null').

    Coarsening is for the CATALOG, which has one type vocabulary. For display,
    ``_declared_type`` reports what the server actually said.
    """
    t = spec.get("type")
    if isinstance(t, list):
        t = next((x for x in t if x != "null"), None)
    return t if t in _JSON_SCHEMA_TYPES else "string"


def _declared_type(spec: dict) -> str:
    """The type as the SERVER declared it, for display - never coarsened and
    never guessed. ``array<string>`` for a typed array, ``string|null`` for a
    union, ``""`` when the schema expresses its type some other way ($ref,
    anyOf/oneOf, enum-only). An empty string is honest: the raw schema travels
    alongside, so a caller that needs the exotic shape still has it.
    """
    t = spec.get("type")
    if isinstance(t, list):
        return "|".join(str(x) for x in t) if t else ""
    if t == "array":
        items = spec.get("items")
        inner = _declared_type(items) if isinstance(items, dict) else ""
        return f"array<{inner}>" if inner else "array"
    return str(t) if t else ""


def _field(name: str, spec: dict, *, required: bool) -> dict:
    """One input parameter or one output field, flattened for display.

    Everything here is DECLARED by the upstream server - nothing is inferred.
    An absent key becomes an empty value, never a guess, so a sparse schema
    reads as sparse rather than as a schema Prefront filled in.
    """
    spec = spec or {}
    constraints = {k: spec[k] for k in _CONSTRAINT_KEYS if k in spec}
    return {
        "name": name,
        "type": _declared_type(spec),
        "required": required,
        "description": str(spec.get("description") or "").strip(),
        "enum": list(spec["enum"]) if isinstance(spec.get("enum"), list) else None,
        "default": spec.get("default"),
        "format": str(spec.get("format") or ""),
        "constraints": constraints,
    }


def _fields(schema: Optional[dict]) -> list[dict]:
    """Top-level properties of an object schema, in declaration order.

    Only the top level is flattened. A nested object/array-of-object keeps its
    structure in the raw schema that travels with the tool - flattening deeper
    would invent a dotted field vocabulary the server never declared.
    """
    if not isinstance(schema, dict):
        return []
    props = schema.get("properties")
    if not isinstance(props, dict):
        return []
    required = set(schema.get("required") or [])
    return [_field(n, s if isinstance(s, dict) else {}, required=n in required)
            for n, s in props.items()]


def _annotations(tool) -> dict:
    """The tool's four MCP annotation hints, TRI-STATE.

    ``True``/``False`` mean the server declared the hint; ``None`` means it said
    nothing. Collapsing "unset" into False is what made the old destructive
    formula wrong (see ``_is_destructive``) and it also loses the one thing an
    operator most wants to know about an unfamiliar server: how much of this did
    the author actually declare?
    """
    a = getattr(tool, "annotations", None)

    def hint(attr: str) -> Optional[bool]:
        v = getattr(a, attr, None) if a is not None else None
        return v if isinstance(v, bool) else None

    return {
        "title": str(getattr(a, "title", "") or "") if a is not None else "",
        "read_only": hint("readOnlyHint"),
        "destructive": hint("destructiveHint"),
        "idempotent": hint("idempotentHint"),
        "open_world": hint("openWorldHint"),
    }


def _is_destructive(ann: dict) -> bool:
    """Only a POSITIVE declaration marks a tool destructive; silence never does.

    The previous formula was ``destructiveHint or not readOnlyHint`` with a
    default of True for a missing ``readOnlyHint`` - so a server that set an
    annotations object at all (even just a display ``title``) had EVERY one of
    its tools read as destructive, flipping their default governance from
    ``allow`` to ``approval_required`` in ``policy.policy_hints_from_mcp``. The
    module always claimed absent annotations are "assume not destructive"; this
    is that claim actually holding when the object exists but the hint doesn't.
    """
    if ann["destructive"] is not None:
        return bool(ann["destructive"])
    if ann["read_only"] is not None:
        return not bool(ann["read_only"])
    return False


async def list_mcp_tools(server_url: str, headers: Optional[dict[str, str]] = None) -> list[dict]:
    """Connect to ``server_url`` and return the COMPLETE record for each tool.

    ``{name, title, description, input_schema, output_schema, parameters,
    output_fields, annotations, destructive, meta}`` - everything the MCP
    protocol exposes about a tool, so nothing is silently dropped between the
    server and the operator reviewing it.

    Two distinctions the shape preserves deliberately, because both are real
    findings about an upstream server rather than presentation details:

    * ``output_schema is None`` means the server declared NO output schema (most
      don't yet); ``{}`` with no properties means it declared one that is empty.
      "We don't know what this tool returns" and "it returns nothing" are
      different facts and must not render the same.
    * every ``annotations`` hint is tri-state - see ``_annotations``.

    ``destructive`` keeps its original meaning and stays the key the catalog and
    ``policy.policy_hints_from_mcp`` read; only its edge-case handling changed
    (see ``_is_destructive``).
    """
    async with mcp_sse.sse_client(server_url, headers=headers or {}) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            listing = await session.list_tools()
    return [tool_record(t) for t in listing.tools]


def tool_record(t) -> dict:
    """One MCP ``Tool`` -> the complete plain-dict record described above.

    Split out of ``list_mcp_tools`` so it can be exercised against synthetic
    ``mcp.types.Tool`` objects without standing up a server - the transport is
    the only part of that function that needs one.
    """
    input_schema = t.inputSchema or {"type": "object", "properties": {}}
    output_schema = getattr(t, "outputSchema", None)
    ann = _annotations(t)
    meta = getattr(t, "meta", None)
    return {
        "name": t.name,
        # MCP's display-name precedence: an explicit `title`, else the
        # annotations' title, else nothing (the UI falls back to `name`).
        "title": str(getattr(t, "title", "") or "") or ann["title"],
        "description": (t.description or "").strip(),
        "input_schema": input_schema,
        "output_schema": output_schema if isinstance(output_schema, dict) else None,
        "parameters": _fields(input_schema),
        "output_fields": _fields(output_schema),
        "annotations": ann,
        "destructive": _is_destructive(ann),
        "meta": meta if isinstance(meta, dict) else None,
    }


def list_mcp_tools_sync(server_url: str, headers: Optional[dict[str, str]] = None) -> list[dict]:
    """Sync wrapper for the FastAPI (plain ``def``) handlers in ``api.py`` — those
    run in a worker thread with no event loop of their own, so ``asyncio.run`` is
    safe here."""
    return asyncio.run(list_mcp_tools(server_url, headers))


def _column(name: str, spec: dict, *, required: bool) -> PhysicalColumn:
    """One JSON Schema property -> one PhysicalColumn.

    `enum_values` is carried across because `PhysicalColumn` already models it
    and a declared enum is a real constraint on the value — dropping it made the
    catalog claim less than the server actually told us.
    """
    enum = spec.get("enum")
    return PhysicalColumn(
        name=name,
        type=_param_type(spec),
        nullable=not required,
        enum_values=[str(v) for v in enum] if isinstance(enum, list) and enum else None,
    )


def build_catalog_from_mcp(
    server_url: str, tools: list[dict], *, datasource_id: str
) -> PhysicalCatalog:
    """One ``PhysicalTable`` per tool, one ``PhysicalColumn`` per input-schema
    property. No primary key / foreign keys — tools aren't joinable; the rest of
    the pipeline already tolerates a table with no key (``querygen._pk_bare``).

    The tool's DECLARED OUTPUT fields ride along in ``mcp_output_columns`` rather
    than in ``columns``: they are not filterable inputs, and folding them in would
    make them look like request parameters to every downstream consumer. Kept
    separate, they are what an intent catalog's ``fields`` (what the tool returns)
    can finally be derived from instead of transcribed by hand.
    """
    tables: list[PhysicalTable] = []
    for t in tools:
        schema = t.get("input_schema") or {}
        props: dict[str, Any] = schema.get("properties") or {}
        required = set(schema.get("required") or [])
        columns = [_column(name, spec or {}, required=name in required)
                   for name, spec in props.items()]

        out_schema = t.get("output_schema")
        out_props: dict[str, Any] = (out_schema or {}).get("properties") or {}
        out_required = set((out_schema or {}).get("required") or [])
        out_columns = [_column(name, spec or {}, required=name in out_required)
                       for name, spec in out_props.items()]

        tables.append(PhysicalTable(
            name=t["name"],
            description=t.get("description", ""),
            mcp_destructive=bool(t.get("destructive")),
            columns=columns,
            mcp_output_columns=out_columns,
            mcp_output_declared=out_schema is not None,
        ))
    log.debug("build_catalog_from_mcp: server=%s tools=%d", server_url, len(tables))
    return PhysicalCatalog(
        datasource_id=datasource_id, type="mcp", tables=tables, mcp_server_url=server_url,
    )
