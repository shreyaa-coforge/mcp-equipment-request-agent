"""Tests for the plain get_employee_info function (not through MCP)."""

import json

import pytest

from server.tools import get_employee_info

AS_OF = "2026-11-24"


# ---------- normal cases ----------

def test_get_employee_info_found_e1001():
    """Spec 5: a valid lookup returns the full contract for E1001 at the reference date."""
    assert get_employee_info("E1001", as_of=AS_OF) == {
        "found": True,
        "employee_id": "E1001",
        "name": "Alex Rivera",
        "role": "engineer",
        "start_date": "2021-03-01",
        "tenure_months": 68,
        "equipment": [
            {"item": "laptop", "issued_date": "2023-06-15"},
            {"item": "monitor", "issued_date": "2024-01-10"},
            {"item": "monitor", "issued_date": "2024-05-20"},
        ],
    }


@pytest.mark.parametrize("emp_id, role, tenure", [
    ("E1001", "engineer", 68),
    ("E1002", "standard", 51),
    ("E1003", "manager", 87),
    ("E1004", "contractor", 17),
    ("E1005", "intern", 2),
    ("E1006", "standard", 1),
])
def test_every_mock_employee_is_found(emp_id, role, tenure):
    """Spec 5: every mock employee is found with the right role and tenure at 2026-11-24."""
    result = get_employee_info(emp_id, as_of=AS_OF)
    assert result["found"] is True
    assert (result["role"], result["tenure_months"]) == (role, tenure)


def test_employee_id_is_trimmed_and_uppercased():
    """R-11: ' e1001 ' finds E1001 and the result reports the normalized ID."""
    result = get_employee_info(" e1001 ", as_of=AS_OF)
    assert result["found"] is True
    assert result["employee_id"] == "E1001"


def test_equipment_keeps_file_order_and_every_unit():
    """Spec 7: E1003 returns all three records, both laptops, in file order."""
    result = get_employee_info("E1003", as_of=AS_OF)
    assert result["equipment"] == [
        {"item": "laptop", "issued_date": "2024-12-20"},
        {"item": "headset", "issued_date": "2025-09-01"},
        {"item": "laptop", "issued_date": "2022-11-15"},
    ]


def test_employee_with_no_equipment():
    """R-30: E1006 is found with an empty equipment list (never had anything)."""
    result = get_employee_info("E1006", as_of=AS_OF)
    assert result["found"] is True
    assert result["equipment"] == []
    assert result["tenure_months"] == 1


def test_role_without_policy_is_still_found():
    """R-12: E1005 (intern) is found here; the missing policy is caught by eligibility, not lookup."""
    result = get_employee_info("E1005", as_of=AS_OF)
    assert result["found"] is True
    assert result["role"] == "intern"


@pytest.mark.parametrize("as_of, tenure", [
    ("2021-03-01", 0),   # start day
    ("2021-03-31", 0),   # one day short of a month
    ("2021-04-01", 1),   # exactly one month
    ("2020-01-01", 0),   # before start date: never negative
])
def test_tenure_follows_as_of(as_of, tenure):
    """Spec 5: tenure_months is whole months from start_date to as_of, never negative."""
    assert get_employee_info("E1001", as_of=as_of)["tenure_months"] == tenure


# ---------- edge cases ----------

def test_get_employee_info_not_found():
    """R-11: an unknown ID returns found=false with a clear error, no exception."""
    assert get_employee_info("E9999", as_of=AS_OF) == {
        "found": False,
        "employee_id": "E9999",
        "error": "Employee E9999 not found",
    }


def test_not_found_reports_normalized_id():
    """R-11: the not-found error uses the normalized ID."""
    result = get_employee_info("  e9999 ", as_of=AS_OF)
    assert result == {"found": False, "employee_id": "E9999", "error": "Employee E9999 not found"}


@pytest.mark.parametrize("bad_id", ["", "   ", None])
def test_blank_employee_id(bad_id):
    """R-10: an empty or missing employee_id returns found=false, 'employee_id is required'."""
    assert get_employee_info(bad_id, as_of=AS_OF) == {
        "found": False,
        "employee_id": "",
        "error": "employee_id is required",
    }


@pytest.mark.parametrize("bad_id", [1001, ["E1001"], {"id": "E1001"}])
def test_non_string_employee_id_never_raises(bad_id):
    """R-10: non-string IDs return found=false instead of raising."""
    result = get_employee_info(bad_id, as_of=AS_OF)
    assert result["found"] is False
    assert result["error"] == "employee_id is required"


@pytest.mark.parametrize("bad_date", ["2026-02-30", "not-a-date", ""])
def test_invalid_as_of(bad_date):
    """R-10: an invalid as_of returns found=false with an error naming the bad value."""
    assert get_employee_info("E1001", as_of=bad_date) == {
        "found": False,
        "employee_id": "E1001",
        "error": f"Invalid as_of date: {bad_date!r}",
    }


def test_reference_date_used_when_no_as_of(monkeypatch):
    """Spec 2: with no as_of (the MCP path), REFERENCE_DATE sets tenure."""
    monkeypatch.setenv("REFERENCE_DATE", "2026-11-24")
    assert get_employee_info("E1001")["tenure_months"] == 68


def test_invalid_reference_date(monkeypatch):
    """R-10: a bad REFERENCE_DATE returns found=false instead of silently using today."""
    monkeypatch.setenv("REFERENCE_DATE", "11/24/2026")
    assert get_employee_info("E1001") == {
        "found": False,
        "employee_id": "E1001",
        "error": "Invalid REFERENCE_DATE: '11/24/2026'",
    }


# ---------- contract properties ----------

def test_result_is_an_independent_copy():
    """Spec 5: mutating a result must not change what the next call returns."""
    first = get_employee_info("E1001", as_of=AS_OF)
    first["equipment"].clear()
    first["role"] = "manager"
    second = get_employee_info("E1001", as_of=AS_OF)
    assert len(second["equipment"]) == 3
    assert second["role"] == "engineer"


@pytest.mark.parametrize("emp_id", ["E1001", "E9999", ""])
def test_result_is_json_serializable(emp_id):
    """Spec 5: every result (found or not) serializes to JSON for the MCP response."""
    json.dumps(get_employee_info(emp_id, as_of=AS_OF))
