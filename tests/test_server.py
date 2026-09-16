"""A real stdio MCP handshake against `python -m olondunge serve`."""

from __future__ import annotations

import json
import os
import sys

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

EXPECTED_TOOLS = {
    "alloc_status",
    "alloc_plan",
    "alloc_dispatch",
    "alloc_poll",
    "alloc_collect",
    "alloc_verify",
    "alloc_call",
    "tri_models",
    "tri_preflight",
}


def test_stdio_handshake_lists_tools_and_ledger() -> None:
    async def run() -> None:
        params = StdioServerParameters(
            command=sys.executable, args=["-m", "olondunge", "serve"], env=dict(os.environ)
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                init = await session.initialize()
                assert init.server_info.name == "olondunge"
                tools = {tool.name for tool in (await session.list_tools()).tools}
                assert EXPECTED_TOOLS <= tools
                resources = {str(r.uri) for r in (await session.list_resources()).resources}
                assert "alloc://ledger" in resources
                result = await session.call_tool(
                    "alloc_plan", {"packet": {"objective": "x", "lane": "nope"}}
                )
                text = "".join(getattr(block, "text", "") for block in result.content)
                assert json.loads(text)["status"] == "blocked"

    anyio.run(run)
