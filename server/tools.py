"""Business logic for the IT equipment request tools, as plain functions.

server/server.py only wraps these with @mcp.tool(). Unit tests import this module
directly. Every function returns a JSON-serializable dict and never raises on bad input.
See docs/REQUIREMENTS.md section 5 for the contracts.
"""

import os
from typing import Any

from server.helpers import (
    load_employees,
    months_between,
    normalize_employee_id,
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
