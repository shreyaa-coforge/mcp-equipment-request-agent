"""Tests for the plain tool functions in server/tools.py (not through MCP).

get_employee_info has its own file, tests/test_get_employee_info.py.
"""

import json

import pytest

from server import tools
from server.tools import get_policy_limits

COVERED_ITEMS = {"laptop", "monitor", "keyboard", "mouse", "headset"}

# Spec 3 policy table: refresh cycle in months, None means not allowed.
SPEC_REFRESH = {
    "standard": {"laptop": 48, "monitor": 36, "keyboard": 24, "mouse": 24, "headset": 24},
    "engineer": {"laptop": 36, "monitor": 36, "keyboard": 24, "mouse": 24, "headset": 24},
    "manager": {"laptop": 24, "monitor": 36, "keyboard": 24, "mouse": 24, "headset": 18},
    "contractor": {"laptop": None, "monitor": 36, "keyboard": 36, "mouse": 36, "headset": 24},
}


# ======================= get_policy_limits =======================

# ---------- normal cases ----------


@pytest.mark.parametrize("role", sorted(SPEC_REFRESH))
def test_policy_limits_found_for_every_role(role):
    """Spec 3: each of the 4 roles is found, lists all 5 items, and matches the table exactly."""
    result = get_policy_limits(role)
    assert result["found"] is True
    assert result["role"] == role
    assert set(result["items"]) == COVERED_ITEMS
    assert {
        item: entry["refresh_months"] for item, entry in result["items"].items()
    } == SPEC_REFRESH[role]


@pytest.mark.parametrize("role", sorted(SPEC_REFRESH))
def test_policy_limits_entry_shape(role):
    """Spec 5: every item entry is exactly {allowed, refresh_months, note}."""
    for item, entry in get_policy_limits(role)["items"].items():
        assert set(entry) == {"allowed", "refresh_months", "note"}, item
        assert entry["allowed"] is (entry["refresh_months"] is not None), item


def test_contractor_laptop_not_allowed():
    """R-20: contractor laptop is allowed=false, refresh_months=null, with the agency note."""
    laptop = get_policy_limits("contractor")["items"]["laptop"]
    assert laptop == {
        "allowed": False,
        "refresh_months": None,
        "note": "Contractors receive laptops from their agency only.",
    }


def test_allowed_items_have_no_note():
    """Spec 5: allowed items have note=null; only the prohibition carries a note."""
    for role in SPEC_REFRESH:
        for item, entry in get_policy_limits(role)["items"].items():
            if entry["allowed"]:
                assert entry["note"] is None, (role, item)


# ---------- edge cases ----------


@pytest.mark.parametrize("raw", ["MANAGER", " Manager ", "manager\n"])
def test_policy_role_case_insensitive(raw):
    """R-12: role lookup ignores case and surrounding whitespace; the result reports 'manager'."""
    result = get_policy_limits(raw)
    assert result["found"] is True
    assert result["role"] == "manager"
    assert result["items"]["laptop"]["refresh_months"] == 24


def test_get_policy_limits_unknown_role():
    """R-12: a role with no policy (intern) returns found=false with a clear error."""
    assert get_policy_limits("intern") == {
        "found": False,
        "role": "intern",
        "error": "No policy on file for role 'intern'",
    }


def test_unknown_role_reports_normalized_role():
    """R-12: the not-found error uses the normalized role name."""
    result = get_policy_limits("  Intern ")
    assert result == {
        "found": False,
        "role": "intern",
        "error": "No policy on file for role 'intern'",
    }


@pytest.mark.parametrize("raw", ["", "   ", None, 7])
def test_blank_role_never_raises(raw):
    """R-10: empty, whitespace, None or non-string roles return found=false, 'role is required'."""
    assert get_policy_limits(raw) == {"found": False, "role": "", "error": "role is required"}


# ---------- contract properties ----------


@pytest.mark.parametrize("role", ["manager", "intern", ""])
def test_policy_result_is_json_serializable(role):
    """Spec 5: every result (found or not) serializes to JSON for the MCP response."""
    json.dumps(get_policy_limits(role))


# ======================= check_request_eligibility =======================

AS_OF = "2026-11-24"

# Spec 5: every response has exactly these keys.
CONTRACT_KEYS = {
    "status",
    "reason_code",
    "message",
    "employee_id",
    "role",
    "requested_item",
    "item",
    "allowed",
    "refresh_months",
    "last_issued_date",
    "next_eligible_date",
    "days_until_eligible",
    "as_of",
}
DATE_FIELDS = ("last_issued_date", "next_eligible_date", "days_until_eligible")


def check(employee_id, item, as_of=AS_OF):
    """Call the plain eligibility function (looked up at call time so red shows per test)."""
    return tools.check_request_eligibility(employee_id, item, as_of=as_of)


# ---------- R-10 invalid input ----------


@pytest.mark.parametrize(
    "emp_id, item, as_of",
    [
        ("E1001", "", AS_OF),  # empty item
        ("E1001", "   ", AS_OF),  # whitespace item
        ("", "laptop", AS_OF),  # empty employee ID
        ("  ", "laptop", AS_OF),  # whitespace employee ID
        ("E1001", "laptop", "2026-02-30"),  # invalid as_of
        ("E1001", "laptop", "not-a-date"),
    ],
)
def test_invalid_input_escalates(emp_id, item, as_of):
    """R-10: empty item, empty employee ID or invalid as_of gives ESCALATE_INVALID_INPUT."""
    result = check(emp_id, item, as_of)
    assert (result["status"], result["reason_code"]) == ("escalate", "ESCALATE_INVALID_INPUT")


def test_invalid_input_message_names_the_problem():
    """R-10: the message says which input was bad."""
    assert "item is required" in check("E1001", "")["message"]
    assert "employee_id is required" in check("", "laptop")["message"]
    assert "2026-02-30" in check("E1001", "laptop", "2026-02-30")["message"]


@pytest.mark.parametrize(
    "emp_id, item", [(None, "laptop"), ("E1001", None), (1001, "laptop"), ("E1001", ["laptop"])]
)
def test_non_string_inputs_never_raise(emp_id, item):
    """R-10: non-string inputs give ESCALATE_INVALID_INPUT instead of raising."""
    assert check(emp_id, item)["reason_code"] == "ESCALATE_INVALID_INPUT"


def test_invalid_reference_date_escalates(monkeypatch):
    """R-10: with no as_of (the MCP path), a bad REFERENCE_DATE gives ESCALATE_INVALID_INPUT."""
    monkeypatch.setenv("REFERENCE_DATE", "11/24/2026")
    result = tools.check_request_eligibility("E1001", "laptop")
    assert result["reason_code"] == "ESCALATE_INVALID_INPUT"
    assert "REFERENCE_DATE" in result["message"]


# ---------- R-11 / R-12 / R-13 ----------


def test_unknown_employee_escalates():
    """R-11: E9999 gives ESCALATE_EMPLOYEE_NOT_FOUND, with role null."""
    result = check("E9999", "laptop")
    assert (result["status"], result["reason_code"]) == ("escalate", "ESCALATE_EMPLOYEE_NOT_FOUND")
    assert result["role"] is None


def test_role_without_policy_escalates():
    """R-12: E1005 (intern) gives ESCALATE_NO_POLICY_FOR_ROLE."""
    result = check("E1005", "laptop")
    assert (result["status"], result["reason_code"]) == ("escalate", "ESCALATE_NO_POLICY_FOR_ROLE")
    assert result["role"] == "intern"


@pytest.mark.parametrize("item", ["standing desk", "laptop stand"])
def test_unknown_item_escalates(item):
    """R-13 / R-45: 'standing desk' and 'laptop stand' are ESCALATE_UNKNOWN_ITEM (no substrings)."""
    result = check("E1001", item)
    assert (result["status"], result["reason_code"]) == ("escalate", "ESCALATE_UNKNOWN_ITEM")
    assert result["item"] == item


# ---------- R-20 / R-30 ----------


def test_not_allowed_checked_before_never_issued():
    """R-20: E1004 (contractor) laptop is denied even though E1004 never had one (ordering)."""
    result = check("E1004", "laptop")
    assert (result["status"], result["reason_code"]) == ("deny", "DENY_NOT_ALLOWED_FOR_ROLE")
    assert result["allowed"] is False


@pytest.mark.parametrize("emp_id, item", [("E1004", "mouse"), ("E1006", "laptop")])
def test_never_issued_approves(emp_id, item):
    """R-30: never had the item gives APPROVE_NEVER_ISSUED with all three date fields null."""
    result = check(emp_id, item)
    assert (result["status"], result["reason_code"]) == ("approve", "APPROVE_NEVER_ISSUED")
    assert all(result[field] is None for field in DATE_FIELDS)


# ---------- R-21 / R-15 / R-31 ----------


def test_too_early_denied():
    """R-21: E1002 laptop gives DENY_TOO_EARLY, next eligible 2029-02-01, 800 days away."""
    result = check("E1002", "laptop")
    assert (result["status"], result["reason_code"]) == ("deny", "DENY_TOO_EARLY")
    assert result["last_issued_date"] == "2025-02-01"
    assert result["next_eligible_date"] == "2029-02-01"
    assert result["days_until_eligible"] == 800


def test_borderline_escalates():
    """R-15: E1003 laptop gives ESCALATE_BORDERLINE, 26 days until eligible."""
    result = check("E1003", "laptop")
    assert (result["status"], result["reason_code"]) == ("escalate", "ESCALATE_BORDERLINE")
    assert result["next_eligible_date"] == "2026-12-20"
    assert result["days_until_eligible"] == 26


def test_refresh_due_approves():
    """R-31: E1001 laptop gives APPROVE_REFRESH_DUE (eligible since 2026-06-15)."""
    result = check("E1001", "laptop")
    assert (result["status"], result["reason_code"]) == ("approve", "APPROVE_REFRESH_DUE")
    assert result["next_eligible_date"] == "2026-06-15"
    assert result["days_until_eligible"] == -162


def test_most_recent_issue_date_used():
    """R-15: E1003 uses its newest laptop (2024-12-20), not the older 2022-11-15 listed last."""
    result = check("E1003", "laptop")
    assert result["last_issued_date"] == "2024-12-20"
    assert result["reason_code"] == "ESCALATE_BORDERLINE"  # the old record would wrongly approve


@pytest.mark.parametrize(
    "as_of, status, reason_code, days",
    [
        ("2026-10-20", "deny", "DENY_TOO_EARLY", 61),
        ("2026-10-21", "escalate", "ESCALATE_BORDERLINE", 60),
        ("2026-12-19", "escalate", "ESCALATE_BORDERLINE", 1),
        ("2026-12-20", "approve", "APPROVE_REFRESH_DUE", 0),
    ],
)
def test_borderline_boundaries(as_of, status, reason_code, days):
    """R-21 / R-15 / R-31: E1003 laptop at 61, 60, 1 and 0 days until eligible."""
    result = check("E1003", "laptop", as_of)
    assert (result["status"], result["reason_code"], result["days_until_eligible"]) == (
        status,
        reason_code,
        days,
    )


# ---------- normalization and additional units ----------


def test_synonym_screen_resolves_to_monitor():
    """R-13: E1001 'Screen' resolves to monitor; requested_item keeps the raw text."""
    result = check("E1001", "Screen")
    assert result["item"] == "monitor"
    assert result["requested_item"] == "Screen"
    assert result["reason_code"] != "ESCALATE_UNKNOWN_ITEM"


def test_additional_monitor_treated_as_refresh():
    """R-21 / Spec 4: a second monitor is a refresh, from E1001's newest monitor (2024-05-20)."""
    result = check("E1001", "monitor")
    assert result["last_issued_date"] == "2024-05-20"
    assert result["refresh_months"] == 36
    assert result["next_eligible_date"] == "2027-05-20"
    assert (result["reason_code"], result["days_until_eligible"]) == ("DENY_TOO_EARLY", 177)
    # The older monitor (2024-01-10) would give 2027-01-10, 47 days: borderline. Newest must win.


# ---------- demo lock ----------


@pytest.mark.parametrize(
    "case, emp_id, item, status, reason_code",
    [
        ("1", "E1001", "laptop", "approve", "APPROVE_REFRESH_DUE"),
        ("2", "E1002", "laptop", "deny", "DENY_TOO_EARLY"),
        ("3", "E1003", "laptop", "escalate", "ESCALATE_BORDERLINE"),
        ("4", "E1004", "standing desk", "escalate", "ESCALATE_UNKNOWN_ITEM"),
        (
            "bonus",
            "E1002",
            "keyboard",
            "approve",
            "APPROVE_REFRESH_DUE",
        ),  # R-40 escalates in the agent
    ],
)
def test_demo_cases_tool_level(case, emp_id, item, status, reason_code):
    """Spec 7 demo lock (R-31, R-21, R-15, R-13): tool-level status per demo case at 2026-11-24."""
    result = check(emp_id, item)
    assert (result["status"], result["reason_code"]) == (status, reason_code)


# ---------- messages (R-50) ----------


def test_deny_too_early_message_cites_rule_and_date():
    """R-50: a too-early deny message mentions the refresh cycle and the next eligible date."""
    message = check("E1002", "laptop")["message"]
    assert "48 months" in message
    assert "2029-02-01" in message


def test_deny_not_allowed_message_cites_rule():
    """R-50: a not-allowed deny message says it is not allowed and gives the policy note."""
    message = check("E1004", "laptop")["message"]
    assert "not allowed" in message
    assert "agency" in message


# ---------- contract (Spec 5) ----------

CONTRACT_CASES = [
    ("", "laptop", AS_OF),
    ("E1001", "laptop", "bad"),
    ("E9999", "laptop", AS_OF),
    ("E1005", "laptop", AS_OF),
    ("E1001", "standing desk", AS_OF),
    ("E1004", "laptop", AS_OF),
    ("E1006", "laptop", AS_OF),
    ("E1002", "laptop", AS_OF),
    ("E1003", "laptop", AS_OF),
    ("E1001", "laptop", AS_OF),
    (None, None, AS_OF),
]


@pytest.mark.parametrize("emp_id, item, as_of", CONTRACT_CASES)
def test_every_result_has_all_contract_keys(emp_id, item, as_of):
    """Spec 5: every response on every path has exactly the contract keys and is JSON-safe."""
    result = check(emp_id, item, as_of)
    assert set(result) == CONTRACT_KEYS
    assert result["status"] in {"approve", "deny", "escalate"}
    assert isinstance(result["message"], str) and result["message"]
    json.dumps(result)


def test_status_matches_reason_code_prefix():
    """Spec 5: status always agrees with the reason_code prefix."""
    for emp_id, item, as_of in CONTRACT_CASES:
        result = check(emp_id, item, as_of)
        assert result["reason_code"].startswith(result["status"].upper() + "_")


def test_normalized_fields_reported():
    """Spec 5: employee_id and item are reported normalized; as_of is the evaluation date."""
    result = check(" e1002 ", "  LAPTOPS ")
    assert (result["employee_id"], result["item"], result["requested_item"]) == (
        "E1002",
        "laptop",
        "  LAPTOPS ",
    )
    assert result["as_of"] == AS_OF
