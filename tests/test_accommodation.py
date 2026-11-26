"""Tests for agent/accommodation.py: detect_accommodation_need (R-41)."""

import pytest

from agent import accommodation

# Demo reason texts from docs/REQUIREMENTS.md section 7.
DEMO_REASONS_NO_TRIGGER = [
    "My laptop is over three years old and the battery dies by lunch. Can I get a new one?",
    "I'd like a new laptop, mine feels a bit slow.",
    "Can I get a new laptop? Mine is almost two years old.",
    "Could I get a standing desk for my workstation?",
]
BONUS_REASON = "My doctor diagnosed RSI and recommended an ergonomic keyboard. Can I get one?"


def detect(reason):
    """Call through the module so a missing function fails each test separately."""
    return accommodation.detect_accommodation_need(reason)


# ---------- triggers ----------


def test_medical_mention_triggers():
    """R-41: a medical mention triggers and reports the matched terms in text order."""
    assert detect("my doctor diagnosed RSI") == {
        "needs_accommodation": True,
        "matched_terms": ["doctor", "diagnosed", "rsi"],
    }


def test_accessibility_mention_triggers():
    """R-41: an accessibility mention triggers."""
    result = detect("I need an accessible keyboard because of my disability.")
    assert result["needs_accommodation"] is True
    assert result["matched_terms"] == ["accessible", "disability"]


def test_safety_mention_triggers():
    """R-41: a safety mention triggers."""
    result = detect("the cable is a safety hazard")
    assert result["needs_accommodation"] is True
    assert result["matched_terms"] == ["safety", "hazard"]


def test_matching_ignores_case():
    """R-41: matching ignores case; matched terms are reported lowercase."""
    assert detect("MY DOCTOR SAYS It Is UNSAFE")["matched_terms"] == ["doctor", "unsafe"]


def test_repeated_term_reported_once():
    """R-41: a term that appears twice is listed once."""
    assert detect("Pain in my wrist, real pain.")["matched_terms"] == ["pain"]


# ---------- must not trigger ----------


@pytest.mark.parametrize("text", ["I enjoy painting my desk", "The display is blinding", "rsid"])
def test_whole_words_only(text):
    """R-41: whole words only; 'painting' does not match 'pain', 'blinding' not 'blind'."""
    assert detect(text) == {"needs_accommodation": False, "matched_terms": []}


def test_normal_request_does_not_trigger():
    """R-41: an ordinary refresh request does not trigger."""
    assert detect("My mouse scroll wheel is broken, can I get a new one?") == {
        "needs_accommodation": False,
        "matched_terms": [],
    }


@pytest.mark.parametrize("text", ["", "   ", None])
def test_empty_or_missing_reason(text):
    """R-41: an empty or missing reason returns no match and never raises."""
    assert detect(text) == {"needs_accommodation": False, "matched_terms": []}


# ---------- demo cases (Spec 7) ----------


@pytest.mark.parametrize("reason", DEMO_REASONS_NO_TRIGGER)
def test_demo_cases_1_to_4_do_not_trigger(reason):
    """R-41 / Spec 7: demo cases 1 to 4 contain no accommodation keyword."""
    assert detect(reason)["needs_accommodation"] is False


def test_bonus_demo_case_triggers():
    """R-40 / R-41 / Spec 7: the bonus RSI case triggers on doctor, diagnosed, rsi, ergonomic."""
    assert detect(BONUS_REASON) == {
        "needs_accommodation": True,
        "matched_terms": ["doctor", "diagnosed", "rsi", "ergonomic"],
    }
