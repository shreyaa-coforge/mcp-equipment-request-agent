"""MCP client for the agent: launches server/server.py over stdio and calls its tools.

Usage:
    async with MCPClient() as client:
        tools = await client.list_tools()
        result = await client.call_tool("get_employee_info", {"employee_id": "E1001"})
"""

import json
import os
import sys
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

REPO_ROOT = Path(__file__).resolve().parent.parent

# The server never needs these, so they are not passed to its process.
_SECRET_ENV_VARS = frozenset({"OPENAI_API_KEY"})


class MCPClient:
    """Async context manager owning one stdio connection to the equipment-requests server."""

    def __init__(self, extra_env: dict[str, str] | None = None) -> None:
        """Prepare launch parameters.

        Args:
            extra_env: Variables to add for the server process (e.g. ESCALATIONS_FILE).
        """
        env = {k: v for k, v in os.environ.items() if k not in _SECRET_ENV_VARS}
        env.update(extra_env or {})
        self._params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "server.server"],
            cwd=str(REPO_ROOT),
            env=env,
        )
        self._stack: AsyncExitStack | None = None
        self._session: ClientSession | None = None

    async def __aenter__(self) -> "MCPClient":
        """Launch the server, open the session and initialize it."""
        self._stack = AsyncExitStack()
        try:
            read, write = await self._stack.enter_async_context(stdio_client(self._params))
            self._session = await self._stack.enter_async_context(ClientSession(read, write))
            await self._session.initialize()
        except BaseException:
            await self._stack.aclose()
            raise
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        """Close the session and stop the server process."""
        if self._stack is not None:
            await self._stack.aclose()
        self._stack = None
        self._session = None

    def _require_session(self) -> ClientSession:
        """Return the open session, or raise if used outside `async with`."""
        if self._session is None:
            raise RuntimeError("MCPClient must be used inside 'async with'")
        return self._session

    async def list_tools(self) -> list[dict[str, Any]]:
        """Return the server's tools as [{"name", "description", "input_schema"}, ...]."""
        listed = await self._require_session().list_tools()
        return [
            {
                "name": tool.name,
                "description": tool.description or "",
                "input_schema": tool.input_schema,
            }
            for tool in listed.tools
        ]

    async def call_tool(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        """Call a tool and return the parsed JSON dict from result.content[0].text.

        A protocol-level error (unknown tool, bad arguments) is returned as
        {"error": "..."} instead of raising, so the agent can feed it back as an observation.
        """
        result = await self._require_session().call_tool(name, args)
        text = result.content[0].text if result.content else ""
        if result.is_error:
            return {"error": text or f"Tool {name!r} failed"}
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return {"error": f"Tool {name!r} returned non-JSON output: {text[:200]!r}"}
        return parsed if isinstance(parsed, dict) else {"result": parsed}
