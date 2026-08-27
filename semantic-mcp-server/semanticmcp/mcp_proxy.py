"""Proxy a GOVERNED call through to an upstream MCP server's tool.

Used by ``server.py:_call_governed`` for a ``kind="mcp"`` template — instead of
running SQL against a local Postgres (``db.run_select``), the "execution" is a
call to a real tool on another MCP server. Governance runs identically either
way (identity/facts/rules/decide are backend-agnostic); only this last step, and
whether the call is allowed to happen at all, differs.
"""

from __future__ import annotations

import json
from typing import Any, Optional

# Imported as a MODULE, not `from mcp.client.sse import sse_client` — the tracing
# layer instruments the module attribute `mcp.client.sse.sse_client`, and a
# from-import binds the original before setup() runs (same trap documented in
# loanpro-demo/ungoverned_server.py and securebank-demo/governed_agent.py).
import mcp.client.sse as mcp_sse
from mcp import ClientSession


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
