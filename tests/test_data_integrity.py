"""Data integrity tests for server/data/*.json.

These read the JSON files directly (no server code), so the mock data is checked
against docs/REQUIREMENTS.md before any tool is built on top of it.
"""

import json
import re
from datetime import date
from pathlib import Path

import pytest

DATA_DIR = Path(__file__).resolve().parent.parent / "server" / "data"
REFERENCE_DATE = date(2026, 11, 24)
COVERED_ITEMS = {"laptop", "monitor", "keyboard", "mouse", "headset"}
POLICY_ROLES = {"standard", "engineer", "manager", "contractor"}

# Spec 3 policy table: refresh cycle in months, None means not allowed.
EXPECTED_REFRESH = {
    "standard":   {"laptop": 48, "monitor": 36, "keyboard": 24, "mouse": 24, "headset": 24},
    "engineer":   {"laptop": 36, "monitor": 36, "keyboard": 24, "mouse": 24, "headset": 24},
    "manager":    {"laptop": 24, "monitor": 36, "keyboard": 24, "mouse": 24, "headset": 18},
    "contractor": {"laptop": None, "monitor": 36, "keyboard": 36, "mouse": 36, "headset": 24},
}

# Spec 7 mock employees table: id -> (role, start_date, [(item, issued_date), ...] in file order).
EXPECTED_EMPLOYEES = {
    "E1001": ("engineer", "2021-03-01",
              [("laptop", "2023-06-15"), ("monitor", "2024-01-10"), ("monitor", "2024-05-20")]),
    "E1002": ("standard", "2022-08-01",
              [("laptop", "2025-02-01"), ("keyboard", "2023-03-01")]),
    "E1003": ("manager", "2019-08-12",
              [("laptop", "2024-12-20"), ("headset", "2025-09-01"), ("laptop", "2022-11-15")]),
    "E1004": ("contractor", "2025-06-02", [("monitor", "2025-06-02")]),
    "E1005": ("intern", "2026-09-01", []),
    "E1006": ("standard", "2026-10-01", []),
}


@pytest.fixture(scope="module")
def policies() -> dict:
    return json.loads((DATA_DIR / "policies.json").read_text())


@pytest.fixture(scope="module")
def employees() -> dict:
    return json.loads((DATA_DIR / "employees.json").read_text())


# ---------- policies.json ----------

def test_policy_roles_are_exactly_the_four_roles(policies):
    """Spec 3 / R-12: exactly standard, engineer, manager, contractor have a policy."""
    assert set(policies) == POLICY_ROLES


@pytest.mark.parametrize("role", sorted(POLICY_ROLES))
def test_each_role_covers_exactly_the_five_items(policies, role):
    """R-13: every role lists exactly the five covered items, nothing else."""
    assert set(policies[role]) == COVERED_ITEMS


@pytest.mark.parametrize("role", sorted(POLICY_ROLES))
def test_policy_entry_shape(policies, role):
    """Spec 5: each entry is exactly {allowed, refresh_months, note} with consistent types."""
    for item, entry in policies[role].items():
        assert set(entry) == {"allowed", "refresh_months", "note"}, (role, item)
        assert isinstance(entry["allowed"], bool), (role, item)
        if entry["allowed"]:
            assert isinstance(entry["refresh_months"], int) and entry["refresh_months"] > 0
        else:
            assert entry["refresh_months"] is None, (role, item)
            assert isinstance(entry["note"], str) and entry["note"].strip(), (role, item)


def test_policy_refresh_values_match_spec_table(policies):
    """R-21 / R-15 / R-31: refresh cycles equal the Spec 3 table exactly."""
    actual = {
        role: {item: (e["refresh_months"] if e["allowed"] else None) for item, e in items.items()}
        for role, items in policies.items()
    }
    assert actual == EXPECTED_REFRESH


def test_contractor_laptop_not_allowed_with_note(policies):
    """R-20: contractor laptop is allowed=false with the agency note."""
    entry = policies["contractor"]["laptop"]
    assert entry["allowed"] is False
    assert entry["note"] == "Contractors receive laptops from their agency only."


def test_only_contractor_laptop_is_prohibited(policies):
    """R-20: no other role/item combination is prohibited."""
    prohibited = {(r, i) for r, items in policies.items() for i, e in items.items() if not e["allowed"]}
    assert prohibited == {("contractor", "laptop")}


# ---------- employees.json ----------

def test_employee_ids_match_spec_table(employees):
    """Spec 7: the file contains exactly E1001 to E1006."""
    assert set(employees) == set(EXPECTED_EMPLOYEES)


def test_e9999_is_absent(employees):
    """R-11: E9999 must not exist, so it can test the not-found path."""
    assert "E9999" not in employees


def test_employee_id_format(employees):
    """R-11: IDs are stored normalized: 'E' + 4 digits, uppercase."""
    for emp_id in employees:
        assert re.fullmatch(r"E\d{4}", emp_id), emp_id


def test_employee_record_shape(employees):
    """Spec 5: each employee has exactly name, role, start_date, equipment."""
    for emp_id, emp in employees.items():
        assert set(emp) == {"name", "role", "start_date", "equipment"}, emp_id
        assert isinstance(emp["name"], str) and emp["name"].strip(), emp_id
        assert isinstance(emp["equipment"], list), emp_id


@pytest.mark.parametrize("emp_id", sorted(EXPECTED_EMPLOYEES))
def test_employee_matches_spec_table(employees, emp_id):
    """Spec 7: role, start date and equipment (in file order) match the mock employees table."""
    role, start, records = EXPECTED_EMPLOYEES[emp_id]
    emp = employees[emp_id]
    assert emp["role"] == role
    assert emp["start_date"] == start
    assert [(r["item"], r["issued_date"]) for r in emp["equipment"]] == records


def test_equipment_records_are_one_unit_each(employees):
    """Spec 7: every equipment record is exactly {item, issued_date}; no quantity field."""
    for emp_id, emp in employees.items():
        for record in emp["equipment"]:
            assert set(record) == {"item", "issued_date"}, (emp_id, record)


def test_equipment_items_are_covered_items(employees):
    """R-13: equipment on file only uses canonical covered item names."""
    for emp_id, emp in employees.items():
        for record in emp["equipment"]:
            assert record["item"] in COVERED_ITEMS, (emp_id, record)


def test_dates_are_valid_iso_and_not_after_reference_date(employees):
    """R-10: all dates parse as ISO dates and none is after the 2026-11-24 reference date."""
    for emp_id, emp in employees.items():
        dates = [emp["start_date"]] + [r["issued_date"] for r in emp["equipment"]]
        for value in dates:
            assert date.fromisoformat(value) <= REFERENCE_DATE, (emp_id, value)


def test_nothing_issued_before_start_date(employees):
    """R-21 / R-31: no equipment is issued before the employee's start date."""
    for emp_id, emp in employees.items():
        start = date.fromisoformat(emp["start_date"])
        for record in emp["equipment"]:
            assert date.fromisoformat(record["issued_date"]) >= start, (emp_id, record)


def test_every_role_but_intern_has_a_policy(employees, policies):
    """R-12: E1005 (intern) is the only employee whose role has no policy."""
    no_policy = {emp_id for emp_id, emp in employees.items() if emp["role"] not in policies}
    assert no_policy == {"E1005"}


def test_e1003_older_laptop_is_listed_last(employees):
    """R-21 / R-15: E1003's older laptop comes after the newer one, so taking the last record is wrong."""
    laptops = [r["issued_date"] for r in employees["E1003"]["equipment"] if r["item"] == "laptop"]
    assert laptops == ["2024-12-20", "2022-11-15"]
