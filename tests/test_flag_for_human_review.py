"""Tests for the plain flag_for_human_review function (not through MCP).

Every test points ESCALATIONS_FILE at pytest's tmp_path, so the real escalations.jsonl
is never touched.
"""

import json
from datetime import datetime
from pathlib import Path

import pytest

from server import tools

REQUEST = "New laptop"
REASON = "Borderline: next eligible 2026-12-20, 26 days away."


@pytest.fixture
def esc_file(tmp_path, monkeypatch) -> Path:
    """Point ESCALATIONS_FILE at a fresh file under tmp_path (not created yet)."""
    path = tmp_path / "escalations.jsonl"
    monkeypatch.setenv("ESCALATIONS_FILE", str(path))
    return path


def read_lines(path: Path) -> list[dict]:
    """Parse every JSON line in the escalations file."""
    return [json.loads(line) for line in path.read_text().splitlines()]


# ---------- normal cases ----------


def test_writes_one_json_line_matching_inputs(esc_file):
    """Spec 5: one JSON line is written, matching the inputs, with status pending_review."""
    tools.flag_for_human_review(" e1003 ", REQUEST, REASON)
    lines = read_lines(esc_file)
    assert len(lines) == 1
    record = lines[0]
    assert record["employee_id"] == "E1003"
    assert record["request"] == REQUEST
    assert record["reason"] == REASON
    assert record["status"] == "pending_review"
    assert record["ticket_id"] == "ESC-0001"


def test_returns_flagged_and_first_ticket_id(esc_file):
    """R-51: a successful flag returns flagged=true and ticket_id ESC-0001."""
    result = tools.flag_for_human_review("E1003", REQUEST, REASON)
    assert result["flagged"] is True
    assert result["ticket_id"] == "ESC-0001"


def test_second_call_gets_next_ticket_id(esc_file):
    """Spec 5: ticket numbers count up; a second call returns ESC-0002 and adds a line."""
    first = tools.flag_for_human_review("E1003", REQUEST, REASON)
    second = tools.flag_for_human_review("E1004", "Standing desk", "Not a covered item.")
    assert (first["ticket_id"], second["ticket_id"]) == ("ESC-0001", "ESC-0002")
    assert [r["ticket_id"] for r in read_lines(esc_file)] == ["ESC-0001", "ESC-0002"]


def test_creates_file_if_missing(esc_file):
    """Spec 5: the escalations file is created on the first flag."""
    assert not esc_file.exists()
    tools.flag_for_human_review("E1003", REQUEST, REASON)
    assert esc_file.exists()


def test_created_at_is_valid_iso_timestamp(esc_file):
    """Spec 5: created_at parses as an ISO 8601 timestamp, in both the result and the file."""
    result = tools.flag_for_human_review("E1003", REQUEST, REASON)
    datetime.fromisoformat(result["created_at"])
    assert read_lines(esc_file)[0]["created_at"] == result["created_at"]


# ---------- edge cases ----------


def test_unknown_employee_is_still_flagged(esc_file):
    """R-11: E9999 is accepted and flagged, so a human sees the request."""
    result = tools.flag_for_human_review("E9999", REQUEST, "Employee E9999 not found.")
    assert result["flagged"] is True
    assert read_lines(esc_file)[0]["employee_id"] == "E9999"


def test_empty_reason_writes_nothing(esc_file):
    """Spec 5: an empty reason returns flagged=false, 'reason is required', and writes no file."""
    assert tools.flag_for_human_review("E1003", REQUEST, "") == {
        "flagged": False,
        "error": "reason is required",
    }
    assert not esc_file.exists()


def test_empty_request_writes_nothing(esc_file):
    """Spec 5: an empty request returns flagged=false, 'request is required', and writes no file."""
    assert tools.flag_for_human_review("E1003", "", REASON) == {
        "flagged": False,
        "error": "request is required",
    }
    assert not esc_file.exists()


def test_whitespace_only_reason_is_empty(esc_file):
    """Spec 5: a whitespace-only reason is treated as empty."""
    result = tools.flag_for_human_review("E1003", REQUEST, "   \n\t ")
    assert result == {"flagged": False, "error": "reason is required"}
    assert not esc_file.exists()


@pytest.mark.parametrize("bad_id", ["", "   ", None])
def test_empty_employee_id_is_rejected(esc_file, bad_id):
    """R-10: an empty employee_id returns flagged=false with an error naming the field."""
    result = tools.flag_for_human_review(bad_id, REQUEST, REASON)
    assert result["flagged"] is False
    assert "employee_id" in result["error"]
    assert not esc_file.exists()
