"""Tests for server/helpers.py: normalization, date math, evaluation date, data loading."""

from datetime import date, datetime, timezone

import pytest

from server import helpers

# ---------- normalize_employee_id ----------


@pytest.mark.parametrize("raw", ["", "   ", None, 1001])
def test_employee_id_blank_or_non_string_becomes_empty(raw):
    """R-10: empty, whitespace, None or non-string IDs normalize to '' (never raise)."""
    assert helpers.normalize_employee_id(raw) == ""


# ---------- normalize_item ----------


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("notebook", "laptop"),
        ("displays", "monitor"),
        ("keyboards", "keyboard"),
        ("mice", "mouse"),
        ("headset", "headset"),
        ("headphones", "headset"),
    ],
)
def test_item_normalization_and_synonyms(raw, expected):
    """R-13: synonyms in Spec 2 map to their canonical covered item."""
    assert helpers.normalize_item(raw) == expected


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("  Laptop ", "laptop"),
        ("SCREEN", "monitor"),
        ("Head   Phones", "head phones"),
        ("standing  desk", "standing desk"),
    ],
)
def test_item_trimmed_lowercased_whitespace_collapsed(raw, expected):
    """R-13: items are trimmed, lowercased and internal whitespace collapsed before lookup."""
    assert helpers.normalize_item(raw) == expected


@pytest.mark.parametrize("raw", ["laptop stand", "ergonomic keyboard", "mouse pad", "monitor arm"])
def test_item_no_substring_matching(raw):
    """R-45: only whole names match. 'laptop stand' is not a laptop; it stays unmapped."""
    assert helpers.normalize_item(raw) == raw
    assert helpers.normalize_item(raw) not in helpers.COVERED_ITEMS


@pytest.mark.parametrize("raw", ["", "   ", None, 42])
def test_item_blank_or_non_string_becomes_empty(raw):
    """R-10: empty, whitespace, None or non-string items normalize to '' (never raise)."""
    assert helpers.normalize_item(raw) == ""


def test_covered_items():
    """R-13: the covered items are exactly the five in the Spec 3 policy table."""
    assert helpers.COVERED_ITEMS == frozenset({"laptop", "monitor", "keyboard", "mouse", "headset"})


# ---------- parse_iso_date ----------


def test_parse_iso_date_valid():
    """R-10: a valid YYYY-MM-DD string parses to a date."""
    assert helpers.parse_iso_date("2026-11-24") == date(2026, 11, 24)


@pytest.mark.parametrize("raw", ["2026-13-01", None, 20261124])
def test_parse_iso_date_invalid_returns_none(raw):
    """R-10: invalid, empty, None or non-string dates return None (never raise)."""
    assert helpers.parse_iso_date(raw) is None


# ---------- add_months (next eligible date) ----------


@pytest.mark.parametrize(
    "start, months, expected",
    [
        (date(2023, 6, 15), 36, date(2026, 6, 15)),  # E1001 laptop
        (date(2025, 2, 1), 48, date(2029, 2, 1)),  # E1002 laptop
        (date(2024, 12, 20), 24, date(2026, 12, 20)),  # E1003 laptop
        (date(2024, 11, 15), 3, date(2025, 2, 15)),  # crosses a year boundary
        (date(2024, 3, 10), 0, date(2024, 3, 10)),
    ],
)
def test_next_eligible_date_adds_calendar_months(start, months, expected):
    """R-21 / R-15 / R-31: next eligible = last issue date + refresh cycle in calendar months."""
    assert helpers.add_months(start, months) == expected


@pytest.mark.parametrize(
    "start, months, expected",
    [
        (date(2024, 1, 31), 1, date(2024, 2, 29)),  # leap year
        (date(2023, 1, 31), 1, date(2023, 2, 28)),  # non-leap year
        (date(2024, 2, 29), 12, date(2025, 2, 28)),
        (date(2024, 8, 31), 1, date(2024, 9, 30)),
    ],
)
def test_next_eligible_date_clamps_month_end(start, months, expected):
    """R-21 / R-15 / R-31: a day missing from the target month clamps to that month's last day."""
    assert helpers.add_months(start, months) == expected


# ---------- months_between (tenure) ----------


def test_tenure_months_consistent_with_month_end_clamping():
    """Spec 5: Jan 31 to Feb 29 is one whole month, matching add_months' clamping."""
    assert helpers.months_between(date(2024, 1, 31), date(2024, 2, 29)) == 1


# ---------- resolve_as_of (evaluation date) ----------


def test_explicit_as_of_wins_over_reference_date(monkeypatch):
    """R-10 / Spec 2: an explicit as_of is used even when REFERENCE_DATE is set."""
    monkeypatch.setenv("REFERENCE_DATE", "2030-01-01")
    assert helpers.resolve_as_of("2026-11-24") == date(2026, 11, 24)


def test_today_utc_used_when_nothing_set(monkeypatch):
    """Spec 2: with no as_of and no REFERENCE_DATE, today's UTC date is used."""
    monkeypatch.delenv("REFERENCE_DATE", raising=False)
    assert helpers.resolve_as_of(None) == datetime.now(timezone.utc).date()


# ---------- data loading ----------


def test_load_employees_returns_mock_data():
    """Spec 7: load_employees reads server/data/employees.json keyed by employee ID."""
    employees = helpers.load_employees()
    assert employees["E1001"]["role"] == "engineer"
    assert "E9999" not in employees


def test_load_policies_returns_mock_data():
    """Spec 3: load_policies reads server/data/policies.json keyed by role then item."""
    policies = helpers.load_policies()
    assert policies["manager"]["laptop"]["refresh_months"] == 24


def test_loaders_return_independent_copies():
    """Spec 5: callers mutating loaded data must not change what the next caller sees."""
    helpers.load_employees()["E1001"]["equipment"].clear()
    helpers.load_policies()["standard"].clear()
    assert len(helpers.load_employees()["E1001"]["equipment"]) == 3
    assert len(helpers.load_policies()["standard"]) == 5
