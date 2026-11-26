"""Tests for reflection Layer 1: the deterministic check_draft (R-44). No API calls.

Observations are records {"tool", "input", "output"}, one per tool call the agent made.
"""

import pytest

from agent import reflection

# ---------- observation builders (outputs copied from real tool results) ----------

E1001_LAPTOP = {
    "tool": "check_request_eligibility",
    "input": {"employee_id": "E1001", "item": "laptop"},
    "output": {
        "status": "approve",
        "reason_code": "APPROVE_REFRESH_DUE",
        "message": "Engineer employees may refresh a laptop every 36 months. Last issued "
        "2023-06-15; next eligible 2026-06-15; the refresh is due.",
        "employee_id": "E1001",
        "role": "engineer",
        "requested_item": "laptop",
        "item": "laptop",
        "allowed": True,
        "refresh_months": 36,
        "last_issued_date": "2023-06-15",
        "next_eligible_date": "2026-06-15",
        "days_until_eligible": -162,
        "as_of": "2026-11-24",
    },
}

E1002_LAPTOP = {
    "tool": "check_request_eligibility",
    "input": {"employee_id": "E1002", "item": "laptop"},
    "output": {
        "status": "deny",
        "reason_code": "DENY_TOO_EARLY",
        "message": "Standard employees may refresh a laptop every 48 months. Last issued "
        "2025-02-01; next eligible 2029-02-01 (800 days away).",
        "employee_id": "E1002",
        "role": "standard",
        "requested_item": "laptop",
        "item": "laptop",
        "allowed": True,
        "refresh_months": 48,
        "last_issued_date": "2025-02-01",
        "next_eligible_date": "2029-02-01",
        "days_until_eligible": 800,
        "as_of": "2026-11-24",
    },
}

E1002_KEYBOARD = {
    "tool": "check_request_eligibility",
    "input": {"employee_id": "E1002", "item": "keyboard"},
    "output": {
        "status": "approve",
        "reason_code": "APPROVE_REFRESH_DUE",
        "message": "Standard employees may refresh a keyboard every 24 months. Last issued "
        "2023-03-01; next eligible 2025-03-01; the refresh is due.",
        "employee_id": "E1002",
        "role": "standard",
        "requested_item": "keyboard",
        "item": "keyboard",
        "allowed": True,
        "refresh_months": 24,
        "last_issued_date": "2023-03-01",
        "next_eligible_date": "2025-03-01",
        "days_until_eligible": -634,
        "as_of": "2026-11-24",
    },
}


def flag_obs(ticket_id: str, employee_id: str = "E1002") -> dict:
    """A successful flag_for_human_review observation with the given ticket."""
    return {
        "tool": "flag_for_human_review",
        "input": {"employee_id": employee_id, "request": "x", "reason": "y"},
        "output": {
            "flagged": True,
            "ticket_id": ticket_id,
            "employee_id": employee_id,
            "request": "x",
            "reason": "y",
            "status": "pending_review",
            "created_at": "2026-11-26T10:00:00Z",
        },
    }


def draft(decision: str, response: str, basis: str = "R-31 APPROVE_REFRESH_DUE: ...", **extra):
    """Build a final-answer draft with all five keys."""
    return {
        "decision": decision,
        "response_to_employee": response,
        "policy_basis": basis,
        "escalation_reason": extra.get("escalation_reason"),
        "ticket_id": extra.get("ticket_id"),
    }


def check(d, observations, accommodation=False):
    """Call through the module so a missing function fails each test separately."""
    return reflection.check_draft(d, observations, accommodation)


# ---------- required cases ----------


def test_correct_approve_passes():
    """R-44: a draft that matches the approve status and states only observed facts passes."""
    d = draft(
        "approve",
        "Your laptop refresh is approved: your current laptop was issued 2023-06-15 and "
        "you have been eligible since 2026-06-15.",
    )
    assert check(d, [E1001_LAPTOP]) == []


def test_wrong_decision_is_caught():
    """R-44: a deny when check_request_eligibility said approve is caught."""
    d = draft("deny", "Sorry, you are not eligible for a new laptop yet.", "R-21 DENY_TOO_EARLY")
    issues = check(d, [E1001_LAPTOP])
    assert any("R-44" in issue and "approve" in issue for issue in issues)


def test_missing_ticket_is_caught():
    """R-51: an escalation without a ticket_id from flag_for_human_review is caught."""
    d = draft(
        "escalate",
        "Your request has been sent to the IT team.",
        "R-40 ESCALATE_ACCOMMODATION",
        escalation_reason="Doctor-recommended ergonomic keyboard.",
    )
    issues = check(d, [E1002_KEYBOARD], accommodation=True)
    assert any("R-51" in issue and "ticket" in issue for issue in issues)


def test_invented_ticket_is_caught():
    """R-51: a ticket_id that no flag_for_human_review observation returned is caught."""
    d = draft(
        "escalate",
        "Your request has been sent to the IT team.",
        "R-40 ESCALATE_ACCOMMODATION",
        escalation_reason="Doctor-recommended ergonomic keyboard.",
        ticket_id="ESC-0042",
    )
    issues = check(d, [E1002_KEYBOARD, flag_obs("ESC-0001")], accommodation=True)
    assert any("R-51" in issue and "ESC-0042" in issue for issue in issues)


def test_invented_date_is_caught():
    """R-52: a YYYY-MM-DD date in the reply that no observation contains is caught."""
    d = draft("approve", "Approved! Your new laptop will arrive on 2026-12-01.")
    issues = check(d, [E1001_LAPTOP])
    assert any("R-52" in issue and "2026-12-01" in issue for issue in issues)


def test_r40_escalation_is_not_flagged_when_tool_said_approve():
    """R-40 / R-44: an accommodation escalation passes even though the tool said approve."""
    d = draft(
        "escalate",
        "Because you mentioned a medical need, a person on the IT team will review this.",
        "R-40 ESCALATE_ACCOMMODATION: accommodation needs are decided by a person.",
        escalation_reason="Doctor diagnosed RSI and recommended an ergonomic keyboard.",
        ticket_id="ESC-0001",
    )
    assert check(d, [E1002_KEYBOARD, flag_obs("ESC-0001")], accommodation=True) == []


# ---------- further rules ----------


def test_r40_non_escalate_decision_is_caught():
    """R-40 / R-44: with an accommodation need, approving (as the tool said) is still wrong."""
    d = draft("approve", "Your keyboard refresh is approved.")
    issues = check(d, [E1002_KEYBOARD], accommodation=True)
    assert any("R-40" in issue for issue in issues)


def test_too_early_deny_without_next_date_is_caught():
    """R-50: a DENY_TOO_EARLY deny must give the next_eligible_date in the reply."""
    d = draft("deny", "Sorry, it is too early for a new laptop.", "R-21 DENY_TOO_EARLY")
    issues = check(d, [E1002_LAPTOP])
    assert any("R-50" in issue and "2029-02-01" in issue for issue in issues)


def test_too_early_deny_with_next_date_passes():
    """R-50: a DENY_TOO_EARLY deny that gives the next eligible date passes."""
    d = draft(
        "deny",
        "Laptops refresh every 48 months, so you will be eligible on 2029-02-01.",
        "R-21 DENY_TOO_EARLY",
    )
    assert check(d, [E1002_LAPTOP]) == []


def test_other_item_named_in_reply_is_caught():
    """R-52: the reply may only name the item that was evaluated (laptop, not monitor)."""
    d = draft("approve", "Approved! We'll send you a 32-inch 4K monitor delivered by Friday.")
    issues = check(d, [E1001_LAPTOP])
    assert any("R-52" in issue and "monitor" in issue for issue in issues)


def test_no_eligibility_observation_is_caught():
    """R-43: a draft with no check_request_eligibility observation is caught."""
    issues = check(draft("approve", "Approved."), [])
    assert any("R-43" in issue for issue in issues)


def test_latest_eligibility_result_is_used():
    """R-44: if eligibility was checked twice, the decision must match the latest result."""
    d = draft(
        "deny",
        "You will be eligible on 2029-02-01.",
        "R-21 DENY_TOO_EARLY",
    )
    assert check(d, [E1001_LAPTOP, E1002_LAPTOP]) == []


@pytest.mark.parametrize("decision", ["approve", "deny"])
def test_ticket_not_required_unless_escalating(decision):
    """R-51: approve and deny drafts need no ticket_id."""
    status_obs = E1001_LAPTOP if decision == "approve" else E1002_LAPTOP
    response = "Approved." if decision == "approve" else "Eligible again on 2029-02-01."
    assert check(draft(decision, response), [status_obs]) == []


# ---------- written-out dates ----------


@pytest.mark.parametrize(
    "written",
    ["February 1, 2029", "Feb 1, 2029", "1 February 2029", "February 1st, 2029"],
)
def test_too_early_deny_with_written_next_date_passes(written):
    """R-50: the next eligible date may be written out instead of YYYY-MM-DD."""
    d = draft("deny", f"Laptops refresh every 48 months; you are eligible on {written}.")
    assert check(d, [E1002_LAPTOP]) == []


def test_invented_written_date_is_caught():
    """R-52: an invented date written out ('December 1, 2026') is caught like an ISO one."""
    d = draft("approve", "Approved! Your new laptop will arrive on December 1, 2026.")
    issues = check(d, [E1001_LAPTOP])
    assert any("R-52" in issue and "2026-12-01" in issue for issue in issues)


def test_observed_written_date_passes():
    """R-52: a written-out date that a tool returned ('June 15, 2026') is allowed."""
    d = draft("approve", "Approved: you have been eligible for a new laptop since June 15, 2026.")
    assert check(d, [E1001_LAPTOP]) == []
