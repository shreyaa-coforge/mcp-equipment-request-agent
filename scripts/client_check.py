"""MCP client check: launch the real server over stdio and call its tools.

Usage: python scripts/client_check.py

Current stage: get_employee_info and get_policy_limits are registered. More calls are
added as each tool is built, tested and wrapped.
"""

import asyncio
import json
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

REPO_ROOT = Path(__file__).resolve().parent.parent

CALLS: list[tuple[str, dict]] = [
    ("get_employee_info", {"employee_id": "E1001"}),    # normal case
    ("get_employee_info", {"employee_id": " e1003 "}),  # normalized ID
    ("get_employee_info", {"employee_id": "E9999"}),    # not found
    ("get_policy_limits", {"role": "manager"}),         # normal case
    ("get_policy_limits", {"role": "intern"}),          # role with no policy
]


async def call_and_print(session: ClientSession, tool: str, args: dict) -> bool:
    """Call one tool, print its result, and return True if the call itself succeeded."""
    print(f"\n>>> {tool}({json.dumps(args)})")
    result = await session.call_tool(tool, args)
    if result.is_error:
        print("TOOL ERROR:", result.content[0].text)
        return False
    payload = result.structured_content or json.loads(result.content[0].text)
    print(json.dumps(payload, indent=2))
    return True


async def main() -> None:
    """Connect to the server, list tools with their input schemas, and run CALLS."""
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "server.server"], cwd=str(REPO_ROOT)
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            listed = await session.list_tools()
            print("Registered tools:", [tool.name for tool in listed.tools])
            for tool in listed.tools:
                print(f"  {tool.name} input_schema: {json.dumps(tool.input_schema)}")

            results = [await call_and_print(session, name, args) for name, args in CALLS]

    print(f"\n{sum(results)}/{len(results)} calls succeeded")
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    asyncio.run(main())
