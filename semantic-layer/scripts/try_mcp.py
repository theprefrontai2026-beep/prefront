"""Smoke-test the Prefront semantic-layer MCP server over stdio.

Spawns `python -m semanticlayer serve --in <dir>`, lists the tools, and calls a
couple of them — printing the decision-trace stubs. No API key needed (serving
reads the already-generated artifacts; it does not call the LLM).

    python scripts/try_mcp.py [artifact_dir]   # default: out/example
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ARTIFACTS = sys.argv[1] if len(sys.argv) > 1 else "out/example"
ROOT = Path(__file__).resolve().parent.parent  # semantic-layer/


async def main() -> None:
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "semanticlayer", "serve", "--in", ARTIFACTS],
        env={"PYTHONPATH": str(ROOT)},
        cwd=str(ROOT),
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as s:
            await s.initialize()

            tools = (await s.list_tools()).tools
            print(f"\n{len(tools)} tools:")
            for t in tools:
                props = list((t.inputSchema or {}).get("properties", {}))
                print(f"  • {t.name}({', '.join(props)}) — {t.description}")

            print("\n── call get_customer_credit {customer_id: C-100} ──")
            r = await s.call_tool("get_customer_credit", {"customer_id": "C-100"})
            print(r.content[0].text)

            print("\n── call create_order (missing required customer_id) ──")
            r = await s.call_tool("create_order", {"order_value": 1000})
            print(r.content[0].text if not r.isError else f"[blocked] {r.content[0].text}")


if __name__ == "__main__":
    asyncio.run(main())
