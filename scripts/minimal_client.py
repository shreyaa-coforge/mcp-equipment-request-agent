"""Minimal MCP client: launch the one-tool server over stdio, list tools, call ping.

Usage: python scripts/minimal_client.py
"""

import asyncio
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER_SCRIPT = Path(__file__).resolve().parent / "minimal_server.py"


async def main() -> None:
    """Connect to the minimal server, list its tools, and call ping once."""
    params = StdioServerParameters(command=sys.executable, args=[str(SERVER_SCRIPT)])

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = await session.list_tools()
            print("Registered tools:", [tool.name for tool in tools.tools])

            result = await session.call_tool("ping", {"name": "Shreyaa"})
            if result.is_error:
                print("ping failed:", result.content[0].text)
                sys.exit(1)
            print("ping result:", result.content[0].text)


if __name__ == "__main__":
    asyncio.run(main())
