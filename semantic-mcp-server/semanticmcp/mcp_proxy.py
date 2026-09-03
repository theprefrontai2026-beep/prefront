"""Proxy a GOVERNED call through to an upstream MCP server's tool.

Used by ``server.py:_call_governed`` for a ``kind="mcp"`` template — instead of
running SQL against a local Postgres (``db.run_select``), the "execution" is a
call to a real tool on another MCP server. Governance runs identically either
way (identity/facts/rules/decide are backend-agnostic); only this last step, and
whether the call is allowed to happen at all, differs.
"""

from __future__ import annotations

import json
import os
from typing import Any, Optional

# Imported as a MODULE, not `from mcp.client.sse import sse_client` — the tracing
# layer instruments the module attribute `mcp.client.sse.sse_client`, and a
# from-import binds the original before setup() runs (same trap documented in
# loanpro-demo/ungoverned_server.py and securebank-demo/governed_agent.py).
import mcp.client.sse as mcp_sse
from mcp import ClientSession

# Header template for forwarding the TRUSTED caller identity to the upstream
# server, e.g.
#     MCP_UPSTREAM_HEADERS="X-App-User={caller.email},X-App-Role={caller.role}"
#
# Config, never code: the header NAMES and the attributes they carry are the
# deployment's vocabulary, so nothing here knows any of them (Hard Rule 1).
# Unset => no headers, which is what every deployment did before this existed.
#
# Why it is needed at all: an upstream app server that resolves its own caller
# from a connection header sees NOTHING when Prefront proxies to it, because
# the proxy opens its own connection. An identity-scoped tool would then either
# fail or, worse, silently widen to every record. The value comes from the
# caller bag Prefront already resolved server-side via IDENTITY_QUERY — it is
# still never anything the agent supplied.
_HEADER_TEMPLATE = os.environ.get("MCP_UPSTREAM_HEADERS", "")


def upstream_headers(caller: Any) -> dict[str, str]:
    """Render _HEADER_TEMPLATE against a resolved caller.

    A pair whose placeholder resolves to nothing is DROPPED rather than sent
    empty: a header present but blank reads to the upstream as "this caller has
    no such attribute", which is a claim, where absence is merely silence.
    """
    if not _HEADER_TEMPLATE or caller is None:
        return {}
    attrs = dict(getattr(caller, "attrs", None) or {})
    out: dict[str, str] = {}
    for pair in _HEADER_TEMPLATE.split(","):
        name, _, template = pair.partition("=")
        name, template = name.strip(), template.strip()
        if not name or not template:
            continue
        rendered = template
        for key, value in attrs.items():
            rendered = rendered.replace("{caller.%s}" % key, "" if value is None else str(value))
        # An unresolved placeholder means the IDENTITY_QUERY does not return
        # that attribute — drop the header instead of forwarding a literal
        # "{caller.foo}" upstream.
        if "{" in rendered or not rendered:
            continue
        out[name] = rendered
    return out


async def call_upstream_tool(
    server_url: str, tool_name: str, args: dict[str, Any], headers: Optional[dict[str, str]] = None
) -> dict:
    """Call ``tool_name`` on the upstream MCP server and return its result as data.

    A tool-level error (the upstream tool ran and reported a problem) comes back
    as a dict — never raised. A raised exception here means the CALL failed
    (connection refused, handshake error, timeout) — the caller treats that as an
    execution error, not a governance concern.
    """
    async with mcp_sse.sse_client(server_url, headers=headers or {}) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(tool_name, args or {})
    text = result.content[0].text if result.content else "{}"
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"result": text}
