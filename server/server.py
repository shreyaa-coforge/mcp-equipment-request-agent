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


if __name__ == "__main__":
    log.info("starting equipment-requests MCP server on stdio")
    mcp.run()
