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


@mcp.tool()
def check_request_eligibility(employee_id: str, item: str) -> dict[str, Any]:
    """Decide whether an employee's request for one item falls within policy.

    This is the authoritative decision. Call it before giving any final answer.
    Pass the core item noun: "keyboard", not "ergonomic keyboard". Covered items are
    laptop, monitor, keyboard, mouse, headset (synonyms like "screen" are accepted).
    A second unit of an item is evaluated the same as a refresh.

    Returns status ("approve" | "deny" | "escalate"), reason_code, and a message citing
    the rule, plus role, item, allowed, refresh_months, last_issued_date,
    next_eligible_date, days_until_eligible and as_of (null when not applicable).
    reason_code is one of: APPROVE_NEVER_ISSUED, APPROVE_REFRESH_DUE,
    DENY_NOT_ALLOWED_FOR_ROLE, DENY_TOO_EARLY (more than 60 days until eligible),
    ESCALATE_BORDERLINE (1 to 60 days), ESCALATE_UNKNOWN_ITEM, ESCALATE_EMPLOYEE_NOT_FOUND,
    ESCALATE_NO_POLICY_FOR_ROLE, ESCALATE_INVALID_INPUT.
    The evaluation date is the server's reference date (as_of in the result).
    """
    return tools.check_request_eligibility(employee_id, item)


@mcp.tool()
def flag_for_human_review(employee_id: str, request: str, reason: str) -> dict[str, Any]:
    """Escalate a request to a human reviewer. SIDE-EFFECTING: records a review ticket.

    You MUST call this for every escalation, before giving a final answer with
    decision "escalate", and include the returned ticket_id in that answer. Never call it
    for approvals or denials. Unknown employee IDs are accepted so they can be escalated.

    Args: employee_id (e.g. "E1003"); request (what the employee asked for, in a short
    phrase); reason (plain-language reason a human must decide, e.g. the eligibility
    message or the accommodation need).

    Returns flagged=true with ticket_id (e.g. "ESC-0001"), employee_id, request, reason,
    status "pending_review" and created_at. An empty employee_id, request or reason writes
    nothing and returns flagged=false with an error naming the missing field.
    """
    result = tools.flag_for_human_review(employee_id, request, reason)
    if result["flagged"]:
        log.info("escalation %s recorded for %s", result["ticket_id"], result["employee_id"])
    return result


if __name__ == "__main__":
    log.info("starting equipment-requests MCP server on stdio")
    mcp.run()
