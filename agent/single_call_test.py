"""Single tool call: OpenAI picks ONE MCP tool for a hardcoded request, and we run it.

This proves the agent-side wiring (OpenAI + MCP client) before the full ReAct loop.

Usage: python -m agent.single_call_test
"""

import asyncio
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

from agent.mcp_client import MCPClient

REQUEST = (
    "Employee E1001: My laptop is over three years old and the battery dies by lunch. "
    "Can I get a new one?"
)

SYSTEM_PROMPT = (
    "You handle IT equipment requests. Choose exactly ONE tool that is the best first "
    "step for the request, and fill in its arguments from the request text."
)


def to_openai_tools(mcp_tools: list[dict]) -> list[dict]:
    """Convert MCP tool listings into OpenAI Chat Completions function-tool specs."""
    return [
        {
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool["description"],
                "parameters": tool["input_schema"],
            },
        }
        for tool in mcp_tools
    ]


async def main() -> None:
    """Connect to MCP, let the model pick one tool, call it, and print the result."""
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    if not os.environ.get("OPENAI_API_KEY"):
        sys.exit("OPENAI_API_KEY is not set (add it to .env).")
    model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

    print(f"Evaluation date: {os.environ.get('REFERENCE_DATE') or 'today (UTC)'}")
    print(f"Model: {model}")
    print(f"Request: {REQUEST}")

    async with MCPClient() as client:
        mcp_tools = await client.list_tools()
        print("Tools offered to the model:", [tool["name"] for tool in mcp_tools])

        response = OpenAI().chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": REQUEST},
            ],
            tools=to_openai_tools(mcp_tools),
            tool_choice="required",
            parallel_tool_calls=False,
        )
        tool_call = response.choices[0].message.tool_calls[0]
        name = tool_call.function.name
        args = json.loads(tool_call.function.arguments or "{}")

        print(f"\nTool chosen: {name}")
        print(f"Arguments: {json.dumps(args)}")
        result = await client.call_tool(name, args)
        print("Result:")
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
