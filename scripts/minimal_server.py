"""Minimal MCP server with one trivial tool, used to prove server/client connectivity.

Run indirectly via ``python scripts/minimal_client.py``. Never print to stdout here:
stdout is the MCP protocol channel. Log to stderr only.
"""

import sys

from mcp.server.mcpserver import MCPServer

mcp = MCPServer("minimal-demo")


@mcp.tool()
def ping(name: str) -> str:
    """Return a greeting so the client can confirm a round trip."""
    return f"pong, {name}"


if __name__ == "__main__":
    print("minimal-demo server starting on stdio", file=sys.stderr)
    mcp.run()
