"""Business logic for the IT equipment request tools, as plain functions.

server/server.py only wraps these with @mcp.tool(). Unit tests import this module
directly. Every function returns a JSON-serializable dict and never raises on bad input.
See docs/REQUIREMENTS.md section 5 for the contracts.
"""

import os
from typing import Any

from server.helpers import (
    load_employees,
    load_policies,
    months_between,
    normalize_employee_id,
    normalize_role,
    parse_iso_date,
    resolve_as_of,
)


def _invalid_date_error(as_of: str | None) -> str:
    """Describe which date source was invalid: the as_of argument or REFERENCE_DATE."""
    if as_of is not None:
        return f"Invalid as_of date: {as_of!r}"
    return f"Invalid REFERENCE_DATE: {os.environ.get('REFERENCE_DATE', '').strip()!r}"


def get_employee_info(employee_id: str, as_of: str | None = None) -> dict[str, Any]:
    """Return an employee's role, tenure and equipment on file.

    Args:
        employee_id: ID like "E1001". Trimmed and uppercased before lookup.
        as_of: Optional ISO date for tenure. Defaults to REFERENCE_DATE, then today (UTC).

    Returns:
        {"found": True, "employee_id", "name", "role", "start_date", "tenure_months",
         "equipment": [{"item", "issued_date"}, ...]} or
        {"found": False, "employee_id", "error"}.
    """
    emp_id = normalize_employee_id(employee_id)
    if not emp_id:
        return {"found": False, "employee_id": "", "error": "employee_id is required"}

    evaluation_date = resolve_as_of(as_of)
    if evaluation_date is None:
        return {"found": False, "employee_id": emp_id, "error": _invalid_date_error(as_of)}

    record = load_employees().get(emp_id)
    if record is None:
        return {"found": False, "employee_id": emp_id, "error": f"Employee {emp_id} not found"}

    start = parse_iso_date(record["start_date"])
    return {
        "found": True,
        "employee_id": emp_id,
        "name": record["name"],
        "role": record["role"],
        "start_date": record["start_date"],
        "tenure_months": months_between(start, evaluation_date),
        "equipment": [
            {"item": unit["item"], "issued_date": unit["issued_date"]}
            for unit in record["equipment"]
        ],
    }


def get_policy_limits(role: str) -> dict[str, Any]:
    """Return what a role is eligible for and how often.

    Args:
        role: Role name like "manager". Trimmed and lowercased before lookup.

    Returns:
        {"found": True, "role", "items": {item: {"allowed", "refresh_months", "note"}}}
        listing all five covered items, or {"found": False, "role", "error"}.
    """
    role_name = normalize_role(role)
    if not role_name:
        return {"found": False, "role": "", "error": "role is required"}

    policy = load_policies().get(role_name)
    if policy is None:
        return {"found": False, "role": role_name, "error": f"No policy on file for role '{role_name}'"}

    return {
        "found": True,
        "role": role_name,
        "items": {
            item: {
                "allowed": entry["allowed"],
                "refresh_months": entry["refresh_months"],
                "note": entry["note"],
            }
            for item, entry in policy.items()
        },
    }
