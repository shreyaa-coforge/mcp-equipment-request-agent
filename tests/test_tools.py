"""Tests for the plain tool functions in server/tools.py (not through MCP).

get_employee_info has its own file, tests/test_get_employee_info.py.
"""

import json

import pytest

from server.tools import get_policy_limits

COVERED_ITEMS = {"laptop", "monitor", "keyboard", "mouse", "headset"}

# Spec 3 policy table: refresh cycle in months, None means not allowed.
SPEC_REFRESH = {
    "standard":   {"laptop": 48, "monitor": 36, "keyboard": 24, "mouse": 24, "headset": 24},
    "engineer":   {"laptop": 36, "monitor": 36, "keyboard": 24, "mouse": 24, "headset": 24},
    "manager":    {"laptop": 24, "monitor": 36, "keyboard": 24, "mouse": 24, "headset": 18},
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
    assert {item: entry["refresh_months"] for item, entry in result["items"].items()} == SPEC_REFRESH[role]


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
    assert result == {"found": False, "role": "intern", "error": "No policy on file for role 'intern'"}


@pytest.mark.parametrize("raw", ["", "   ", None, 7])
def test_blank_role_never_raises(raw):
    """R-10: empty, whitespace, None or non-string roles return found=false, 'role is required'."""
    assert get_policy_limits(raw) == {"found": False, "role": "", "error": "role is required"}


# ---------- contract properties ----------

def test_policy_result_is_an_independent_copy():
    """Spec 5: mutating a result must not change what the next call returns."""
    first = get_policy_limits("standard")
    first["items"]["laptop"]["refresh_months"] = 1
    first["items"].pop("mouse")
    second = get_policy_limits("standard")
    assert second["items"]["laptop"]["refresh_months"] == 48
    assert "mouse" in second["items"]


@pytest.mark.parametrize("role", ["manager", "intern", ""])
def test_policy_result_is_json_serializable(role):
    """Spec 5: every result (found or not) serializes to JSON for the MCP response."""
    json.dumps(get_policy_limits(role))
