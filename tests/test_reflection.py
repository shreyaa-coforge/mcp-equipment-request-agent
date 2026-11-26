"""Tests for reflection Layer 1: the deterministic check_draft (R-44). No API calls.

Observations are records {"tool", "input", "output"}, one per tool call the agent made.
"""

import json

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


# ---------- R-42: model-applied accommodation escalations ----------

R42_DRAFT = draft(
    "escalate",
    "A person on the IT team will review your request.",
    "R-40 ESCALATE_ACCOMMODATION: 'I use a wheelchair' (R-42).",
    escalation_reason="Accessibility need: 'I use a wheelchair'.",
    ticket_id="ESC-0001",
)


def test_r42_escalation_with_real_ticket_passes():
    """R-42: an escalation citing R-40 with a real ticket passes, even with no keyword match."""
    assert check(R42_DRAFT, [E1001_LAPTOP, flag_obs("ESC-0001", "E1001")]) == []


def test_r42_escalation_with_invented_ticket_is_caught():
    """R-42 / R-51: citing R-40 does not excuse a ticket no flag call returned."""
    issues = check(R42_DRAFT, [E1001_LAPTOP, flag_obs("ESC-0007", "E1001")])
    assert any("R-51" in issue for issue in issues)


def test_r42_escalation_without_ticket_is_caught():
    """R-42 / R-51: an R-40 escalation with no flag call at all is caught."""
    issues = check(R42_DRAFT, [E1001_LAPTOP])
    assert issues


def test_reflection_never_reverts_r42_escalation():
    """R-44: a critique that turns an R-42 escalation into approve is discarded."""

    def approving_critique(messages):
        revised = dict(R42_DRAFT, decision="approve", ticket_id=None, escalation_reason=None)
        return json.dumps({"verdict": "revised", "issues": ["x"], "final": revised})

    result = reflection.reflect(
        approving_critique,
        "Employee E1001: I use a wheelchair.",
        R42_DRAFT,
        [E1001_LAPTOP, flag_obs("ESC-0001", "E1001")],
        False,
    )
    assert result.final["decision"] == "escalate"
    assert result.final["ticket_id"] == "ESC-0001"


# ---------- requesting employee and item ----------


def test_eligibility_for_requesting_employee_is_used():
    """R-43 / R-44: with employee_id given, only that employee's eligibility result counts."""
    d = draft("deny", "You will be eligible on 2029-02-01.", "R-21 DENY_TOO_EARLY")
    issues = reflection.check_draft(d, [E1002_LAPTOP, E1001_LAPTOP], False, employee_id="E1002")
    assert issues == []


def test_no_eligibility_for_requesting_employee_is_caught():
    """R-43: an eligibility result for a different employee does not count."""
    issues = reflection.check_draft(
        draft("approve", "Approved."), [E1001_LAPTOP], False, employee_id="E1002"
    )
    assert any("R-43" in issue for issue in issues)


def test_eligibility_item_differs_from_request_is_caught():
    """R-45: eligibility checked for 'keyboard' on a laptop request is an issue."""
    issues = reflection.check_draft(
        draft("approve", "Approved."),
        [E1002_KEYBOARD],
        False,
        employee_id="E1002",
        request_text="I'd like a new laptop, mine feels a bit slow.",
    )
    assert any("keyboard" in issue and "laptop" in issue for issue in issues)


def test_uncovered_item_in_request_is_not_an_item_mismatch():
    """R-45: 'laptop stand' checked as 'laptop stand' (unknown item) is not a mismatch."""
    unknown = {
        "tool": "check_request_eligibility",
        "input": {"employee_id": "E1001", "item": "laptop stand"},
        "output": dict(
            E1001_LAPTOP["output"],
            status="escalate",
            reason_code="ESCALATE_UNKNOWN_ITEM",
            item="laptop stand",
        ),
    }
    d = draft(
        "escalate",
        "Sent to IT.",
        "R-13 ESCALATE_UNKNOWN_ITEM",
        escalation_reason="Not a covered item.",
        ticket_id="ESC-0001",
    )
    issues = reflection.check_draft(
        d,
        [unknown, flag_obs("ESC-0001", "E1001")],
        False,
        employee_id="E1001",
        request_text="Can I get a laptop stand?",
    )
    assert issues == []


# ---------- fallback drafts ----------


def test_fallback_escalation_with_ticket_passes():
    """R-51: the 8-step fallback escalation with a real ticket passes, with no eligibility."""
    d = draft(
        "escalate",
        "Passed to the IT team.",
        "Agent stopped after 8 steps.",
        escalation_reason="agent could not reach a decision",
        ticket_id="ESC-0001",
    )
    assert reflection.check_draft(d, [flag_obs("ESC-0001")], False, fallback=True) == []


def test_fallback_escalation_without_ticket_is_reported():
    """R-51: a fallback whose flag call failed is reported as having no ticket."""
    d = draft(
        "escalate",
        "No ticket could be created.",
        "Agent stopped after 8 steps.",
        escalation_reason="agent could not reach a decision",
    )
    issues = reflection.check_draft(d, [], False, fallback=True)
    assert len(issues) == 1 and "R-51" in issues[0]
