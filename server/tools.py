"""Business logic for the IT equipment request tools, as plain functions.

server/server.py only wraps these with @mcp.tool(). Unit tests import this module
directly. Every function returns a JSON-serializable dict and never raises on bad input.
See docs/REQUIREMENTS.md section 5 for the contracts.
"""

import os
from typing import Any

from server.helpers import (
    COVERED_ITEMS,
    add_months,
    load_employees,
    load_policies,
    months_between,
    normalize_employee_id,
    normalize_item,
    normalize_role,
    parse_iso_date,
    resolve_as_of,
)

# R-15 / R-21: 1..BORDERLINE_DAYS days until eligible escalates; more than that denies.
BORDERLINE_DAYS = 60

ELIGIBILITY_KEYS = (
    "status", "reason_code", "message", "employee_id", "role", "requested_item", "item",
    "allowed", "refresh_months", "last_issued_date", "next_eligible_date",
    "days_until_eligible", "as_of",
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


def _decide(result: dict[str, Any], reason_code: str, message: str) -> dict[str, Any]:
    """Fill status (from the reason_code prefix), reason_code and message, then return result."""
    result["status"] = reason_code.split("_", 1)[0].lower()
    result["reason_code"] = reason_code
    result["message"] = message
    return result


def check_request_eligibility(
    employee_id: str, item: str, as_of: str | None = None
) -> dict[str, Any]:
    """Decide whether a request falls within policy. Deterministic; see Spec 3 rule table.

    Rules are checked in order and the first match wins:
    R-10 invalid input, R-11 employee not found, R-12 no policy for role, R-13 unknown item
    (all escalate); R-20 not allowed for role (deny); R-30 never issued (approve);
    R-21 more than 60 days until eligible (deny); R-15 1 to 60 days (escalate);
    R-31 0 or fewer days (approve).

    Args:
        employee_id: ID like "E1001". Trimmed and uppercased.
        item: Item name. Normalized through the synonym table (whole names only).
        as_of: Optional ISO evaluation date. Defaults to REFERENCE_DATE, then today (UTC).

    Returns:
        A dict with every key in ELIGIBILITY_KEYS. Keys that don't apply are None.
    """
    emp_id = normalize_employee_id(employee_id)
    item_name = normalize_item(item)
    evaluation_date = resolve_as_of(as_of)

    result: dict[str, Any] = dict.fromkeys(ELIGIBILITY_KEYS)
    result.update(
        employee_id=emp_id or None,
        requested_item=item if isinstance(item, str) else None,
        item=item_name or None,
        as_of=evaluation_date.isoformat() if evaluation_date else None,
    )

    # Stage 1: can policy be applied at all?
    if not emp_id:
        return _decide(result, "ESCALATE_INVALID_INPUT", "employee_id is required.")
    if not item_name:
        return _decide(result, "ESCALATE_INVALID_INPUT", "item is required.")
    if evaluation_date is None:
        return _decide(result, "ESCALATE_INVALID_INPUT", _invalid_date_error(as_of) + ".")

    record = load_employees().get(emp_id)
    if record is None:
        return _decide(result, "ESCALATE_EMPLOYEE_NOT_FOUND",
                       f"Employee {emp_id} not found; policy cannot be applied.")
    role = record["role"]
    result["role"] = role

    policy = load_policies().get(role)
    if policy is None:
        return _decide(result, "ESCALATE_NO_POLICY_FOR_ROLE",
                       f"No policy on file for role '{role}'; policy cannot be applied.")

    entry = policy.get(item_name)
    if entry is None:
        covered = ", ".join(sorted(COVERED_ITEMS))
        return _decide(result, "ESCALATE_UNKNOWN_ITEM",
                       f"'{item_name}' is not a covered item (covered: {covered}).")
    refresh = entry["refresh_months"]
    result.update(allowed=entry["allowed"], refresh_months=refresh)

    # Stage 2: explicit prohibition.
    if not entry["allowed"]:
        return _decide(result, "DENY_NOT_ALLOWED_FOR_ROLE",
                       f"{role.capitalize()} employees are not allowed a {item_name}. {entry['note']}")

    # Stage 3: date rules, only when an issue date exists.
    issued = [parse_iso_date(u["issued_date"]) for u in record["equipment"] if u["item"] == item_name]
    issued = [d for d in issued if d is not None]
    if not issued:
        return _decide(result, "APPROVE_NEVER_ISSUED",
                       f"No {item_name} on file for {emp_id}. {role.capitalize()} employees may "
                       f"receive a {item_name} (refresh every {refresh} months).")

    last_issued = max(issued)
    next_eligible = add_months(last_issued, refresh)
    days = (next_eligible - evaluation_date).days
    result.update(
        last_issued_date=last_issued.isoformat(),
        next_eligible_date=next_eligible.isoformat(),
        days_until_eligible=days,
    )
    rule = (f"{role.capitalize()} employees may refresh a {item_name} every {refresh} months. "
            f"Last issued {last_issued.isoformat()}; next eligible {next_eligible.isoformat()}")

    if days > BORDERLINE_DAYS:
        return _decide(result, "DENY_TOO_EARLY", f"{rule} ({days} days away).")
    if days >= 1:
        return _decide(result, "ESCALATE_BORDERLINE",
                       f"{rule}, {days} days away (within the {BORDERLINE_DAYS}-day borderline window).")
    return _decide(result, "APPROVE_REFRESH_DUE", f"{rule}; the refresh is due.")
