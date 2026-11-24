"""Shared helpers for the tool functions: normalization, date math, and data loading.

Nothing here raises on bad input. Invalid values normalize to "" or return None,
so the tool functions can turn them into {"found": False, "error": ...} results.
"""

import calendar
import json
import os
from datetime import date, datetime, timezone
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent / "data"

COVERED_ITEMS: frozenset[str] = frozenset({"laptop", "monitor", "keyboard", "mouse", "headset"})

# Whole-name synonyms only (R-45). Keys are already trimmed and lowercased.
ITEM_SYNONYMS: dict[str, str] = {
    "laptops": "laptop", "notebook": "laptop",
    "monitors": "monitor", "screen": "monitor", "screens": "monitor",
    "display": "monitor", "displays": "monitor",
    "keyboards": "keyboard",
    "mice": "mouse",
    "headsets": "headset", "headphone": "headset", "headphones": "headset",
}


def _clean(value: object) -> str:
    """Trim, lowercase and collapse internal whitespace; non-strings become ''."""
    if not isinstance(value, str):
        return ""
    return " ".join(value.split()).lower()


def normalize_employee_id(value: object) -> str:
    """Return the employee ID trimmed and uppercased, or '' if blank or not a string."""
    if not isinstance(value, str):
        return ""
    return value.strip().upper()


def normalize_item(value: object) -> str:
    """Return the canonical item name. Unknown names come back cleaned but unmapped."""
    cleaned = _clean(value)
    return ITEM_SYNONYMS.get(cleaned, cleaned)


def normalize_role(value: object) -> str:
    """Return the role trimmed and lowercased, or '' if blank or not a string."""
    return _clean(value)


def parse_iso_date(value: object) -> date | None:
    """Parse a strict YYYY-MM-DD string. Return None for anything else."""
    if not isinstance(value, str) or len(value) != 10:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def resolve_as_of(as_of: str | None) -> date | None:
    """Return the evaluation date: as_of, else REFERENCE_DATE env var, else today (UTC).

    Returns None when the chosen source is invalid, so the caller can report it.
    """
    if as_of is not None:
        return parse_iso_date(as_of)
    reference = os.environ.get("REFERENCE_DATE", "").strip()
    if reference:
        return parse_iso_date(reference)
    return datetime.now(timezone.utc).date()


def add_months(start: date, months: int) -> date:
    """Add calendar months, clamping the day to the end of the target month."""
    month_index = start.month - 1 + months
    year = start.year + month_index // 12
    month = month_index % 12 + 1
    day = min(start.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def months_between(start: date, end: date) -> int:
    """Return whole calendar months from start to end (0 if end is before start).

    Uses the same clamping as add_months: the largest n with add_months(start, n) <= end.
    """
    if end <= start:
        return 0
    months = (end.year - start.year) * 12 + (end.month - start.month)
    if add_months(start, months) > end:
        months -= 1
    return months


def _load_json(filename: str) -> dict:
    """Read a JSON file from the data directory. A fresh copy is returned every call."""
    return json.loads((DATA_DIR / filename).read_text(encoding="utf-8"))


def load_employees() -> dict:
    """Return employees.json as {employee_id: record}."""
    return _load_json("employees.json")


def load_policies() -> dict:
    """Return policies.json as {role: {item: entry}}."""
    return _load_json("policies.json")
