"""MCP server for IT equipment requests. Thin wrappers around server/tools.py.

Run with: python -m server.server   (from the repo root; stdio transport)

Never print to stdout here: stdout is the MCP protocol channel. Log to stderr only.
"""

import logging
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from mcp.server.mcpserver import MCPServer

from server import tools

# The stdio client only passes HOME/PATH/SHELL/USER/LOGNAME to this process, so load
# REFERENCE_DATE (and later ESCALATIONS_FILE) from the repo's .env ourselves.
# Variables already set in the environment win over .env.
load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)

logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="[server] %(message)s")
log = logging.getLogger(__name__)

mcp = MCPServer("equipment-requests")


@mcp.tool()
def get_employee_info(employee_id: str) -> dict[str, Any]:
    """Look up an employee by ID (e.g. "E1001").

    Returns found, employee_id, name, role, start_date, tenure_months, and equipment
    (one {item, issued_date} record per physical unit on file). If the employee does not
    exist, returns found=false with an error message.
    """
    return tools.get_employee_info(employee_id)


@mcp.tool()
def get_policy_limits(role: str) -> dict[str, Any]:
    """Look up what a role is eligible for and how often (e.g. "manager").

    Roles with a policy: standard, engineer, manager, contractor. Returns found, role, and
    items: for each of laptop, monitor, keyboard, mouse, headset, {allowed, refresh_months,
    note}. refresh_months is the minimum months between issues of that item; allowed=false
    means the role may never receive it (note explains why). An unknown role returns
    found=false with an error. This describes policy only; use check_request_eligibility
    for an actual decision.
    """
    return tools.get_policy_limits(role)


if __name__ == "__main__":
    log.info("starting equipment-requests MCP server on stdio")
    mcp.run()
