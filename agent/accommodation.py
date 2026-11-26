"""Accommodation detection (R-41): does a request reason cite a medical, accessibility or
safety need? If so, R-40 makes the final decision escalate, whatever the tool said.

A false match only causes an extra human review, which is the safe direction.
"""

import re
from typing import Any

# R-41 keyword list, from docs/REQUIREMENTS.md section 4. Matched as whole words, any case.
ACCOMMODATION_TERMS: frozenset[str] = frozenset(
    {
        "medical",
        "doctor",
        "physician",
        "prescription",
        "diagnosis",
        "diagnosed",
        "disability",
        "disabled",
        "accessibility",
        "accessible",
        "accommodation",
        "ergonomic",
        "injury",
        "injured",
        "pain",
        "rsi",
        "carpal",
        "tendonitis",
        "vision",
        "hearing",
        "deaf",
        "blind",
        "safety",
        "hazard",
        "unsafe",
    }
)

_WORD = re.compile(r"[a-z]+")


def detect_accommodation_need(reason: str | None) -> dict[str, Any]:
    """Check a request reason for accommodation keywords (R-41).

    Args:
        reason: The employee's free-text reason. None or non-string counts as empty.

    Returns:
        {"needs_accommodation": bool, "matched_terms": [...]}, where matched_terms are the
        lowercase keywords found, in order of first appearance, each listed once.
    """
    if not isinstance(reason, str):
        return {"needs_accommodation": False, "matched_terms": []}

    matched: list[str] = []
    for word in _WORD.findall(reason.lower()):
        if word in ACCOMMODATION_TERMS and word not in matched:
            matched.append(word)
    return {"needs_accommodation": bool(matched), "matched_terms": matched}
