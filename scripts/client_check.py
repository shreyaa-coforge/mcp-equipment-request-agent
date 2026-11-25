"""MCP client check: launch the real server over stdio and call its tools.

Usage: python scripts/client_check.py

All four tools are registered. flag_for_human_review writes to a temporary
ESCALATIONS_FILE (deleted afterwards), so no escalations.jsonl is left in the repo.
"""

import asyncio
import json
import os
import sys
import tempfile
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
    # Demo cases 1-4 (docs/REQUIREMENTS.md section 7), at the server's REFERENCE_DATE.
    ("check_request_eligibility", {"employee_id": "E1001", "item": "laptop"}),         # 1 approve
    ("check_request_eligibility", {"employee_id": "E1002", "item": "laptop"}),         # 2 deny
    ("check_request_eligibility", {"employee_id": "E1003", "item": "laptop"}),         # 3 borderline
    ("check_request_eligibility", {"employee_id": "E1004", "item": "standing desk"}),  # 4 unknown item
    # Side-effecting: escalate demo case 3. Writes to the temporary ESCALATIONS_FILE.
    ("flag_for_human_review", {
        "employee_id": "E1003",
        "request": "New laptop",
        "reason": "Borderline: next eligible 2026-12-20, 26 days away.",
    }),
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
    with tempfile.TemporaryDirectory() as tmp_dir:
        esc_file = Path(tmp_dir) / "escalations.jsonl"
        # The child process only inherits a few variables, so pass ESCALATIONS_FILE explicitly.
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "server.server"],
            cwd=str(REPO_ROOT),
            env={**os.environ, "ESCALATIONS_FILE": str(esc_file)},
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()

                listed = await session.list_tools()
                print("Registered tools:", [tool.name for tool in listed.tools])
                for tool in listed.tools:
                    print(f"  {tool.name} input_schema: {json.dumps(tool.input_schema)}")

                results = [await call_and_print(session, name, args) for name, args in CALLS]

        print(f"\nTemporary ESCALATIONS_FILE contents ({esc_file}):")
        print(esc_file.read_text(encoding="utf-8") if esc_file.exists() else "(no file written)")

    print(f"\n{sum(results)}/{len(results)} calls succeeded")
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    asyncio.run(main())
