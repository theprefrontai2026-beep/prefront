"""Talk to the semantic-mcp-server over stdio (a minimal MCP client).

Spawns `python -m semanticmcp serve`, lists the tools, and calls one — printing
the rows. Requires the CommerceRisk Postgres to be up (docker compose up -d).

    python scripts/try_mcp.py                      # list tools + demo call
    python scripts/try_mcp.py find_customers '{"caller_region":"APAC"}'
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parent.parent  # semantic-mcp-server/
TEMPLATES = ROOT.parent / "semantic-layer/out/commercerisk/query_templates.yaml"

TOOL = sys.argv[1] if len(sys.argv) > 1 else "find_customers"
ARGS = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {"caller_region": "EMEA"}


async def main() -> None:
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "semanticmcp", "--templates", str(TEMPLATES), "serve"],
        env={"PYTHONPATH": str(ROOT)},
        cwd=str(ROOT),
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as s:
            await s.initialize()
            tools = (await s.list_tools()).tools
            print("tools:")
            for t in tools:
                print(f"  • {t.name}({', '.join((t.inputSchema or {}).get('properties', {}))})")
            print(f"\ncall {TOOL} {ARGS}")
            res = await s.call_tool(TOOL, ARGS)
            print(res.content[0].text)


if __name__ == "__main__":
    asyncio.run(main())
