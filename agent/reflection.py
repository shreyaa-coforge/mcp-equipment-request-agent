"""Reflection (R-44): checks every final-answer draft before it is finalized.

Layer 1, check_draft: deterministic rules over the draft and the tool observations.
Layer 2, critique_draft: an LLM critique given the draft and ALL observations, returning
{"verdict": "confirmed" | "revised", "issues": [...], "final": {...}}.

reflect() runs both layers and guards the result: a revision is only kept if it passes
Layer 1, and an R-40 accommodation escalation is never turned back into approve or deny.

An observation is one tool call the agent made: {"tool": name, "input": args, "output": result}.
"""

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

ELIGIBILITY_TOOL = "check_request_eligibility"
FLAG_TOOL = "flag_for_human_review"
DECISIONS = ("approve", "deny", "escalate")
FINAL_KEYS = ("decision", "response_to_employee", "policy_basis", "escalation_reason", "ticket_id")

_ISO_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")

# Words that name a covered item in a reply, mapped to the item they mean.
ITEM_WORDS = {
    "laptop": "laptop",
    "laptops": "laptop",
    "notebook": "laptop",
    "monitor": "monitor",
    "monitors": "monitor",
    "screen": "monitor",
    "screens": "monitor",
    "display": "monitor",
    "displays": "monitor",
    "keyboard": "keyboard",
    "keyboards": "keyboard",
    "mouse": "mouse",
    "mice": "mouse",
    "headset": "headset",
    "headsets": "headset",
    "headphone": "headset",
    "headphones": "headset",
}
_WORD = re.compile(r"[a-z]+")

# Takes chat messages, returns the model's raw reply text (a JSON object).
LLM = Callable[[list[dict[str, str]]], str]


# ---------- shared helpers ----------


def normalize_final(answer: dict[str, Any]) -> dict[str, Any]:
    """Return the answer with exactly FINAL_KEYS; escalation fields are null unless escalating."""
    final = {key: answer.get(key) for key in FINAL_KEYS}
    if final["decision"] != "escalate":
        final["escalation_reason"] = None
        final["ticket_id"] = None
    return final


def latest_eligibility(observations: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Return the output of the most recent check_request_eligibility call with a status."""
    for obs in reversed(observations):
        if obs.get("tool") == ELIGIBILITY_TOOL and "status" in obs.get("output", {}):
            return obs["output"]
    return None


def flagged_tickets(observations: list[dict[str, Any]]) -> list[str]:
    """Return every ticket_id that a successful flag_for_human_review call returned."""
    return [
        obs["output"]["ticket_id"]
        for obs in observations
        if obs.get("tool") == FLAG_TOOL and obs.get("output", {}).get("flagged")
    ]


def observed_dates(observations: list[dict[str, Any]]) -> set[str]:
    """Return every YYYY-MM-DD date that appears anywhere in the tool outputs."""
    return set(_ISO_DATE.findall(json.dumps([obs.get("output") for obs in observations])))


def required_decision(observations: list[dict[str, Any]], accommodation_flag: bool) -> str | None:
    """Return the only valid decision: escalate under R-40, else the latest status (R-44)."""
    if accommodation_flag:
        return "escalate"
    eligibility = latest_eligibility(observations)
    return eligibility["status"] if eligibility else None


# ---------- Layer 1: deterministic checks ----------


def check_draft(
    draft: dict[str, Any], observations: list[dict[str, Any]], accommodation_flag: bool
) -> list[str]:
    """Check a final-answer draft against the observations. Returns issues (empty = passes).

    Rules:
        R-43  a check_request_eligibility result exists.
        R-40  if accommodation_flag, the decision must be escalate (the tool's status is
              overridden, so an escalate here is NOT an issue even if the tool said approve).
        R-44  otherwise, the decision must match the latest eligibility status.
        R-51  an escalation has a ticket_id returned by flag_for_human_review.
        R-50  a DENY_TOO_EARLY deny gives the next_eligible_date in the reply.
        R-52  every YYYY-MM-DD date in the reply appears in an observation, and the reply
              names no covered item other than the one that was evaluated.
    """
    issues: list[str] = []
    decision = draft.get("decision")
    response = draft.get("response_to_employee") or ""
    eligibility = latest_eligibility(observations)

    if eligibility is None:
        issues.append(f"R-43: no {ELIGIBILITY_TOOL} result; the decision has no policy basis.")
    elif accommodation_flag:
        if decision != "escalate":
            issues.append(
                f"R-40: an accommodation need was detected, so the decision must be escalate, "
                f"not {decision!r}."
            )
    elif decision != eligibility["status"]:
        issues.append(
            f"R-44: decision {decision!r} does not match {ELIGIBILITY_TOOL} status "
            f"{eligibility['status']!r} ({eligibility['reason_code']})."
        )

    if decision == "escalate":
        tickets = flagged_tickets(observations)
        ticket = draft.get("ticket_id")
        if not ticket:
            issues.append(f"R-51: escalation has no ticket_id; call {FLAG_TOOL} first.")
        elif ticket not in tickets:
            returned = ", ".join(tickets) or "none"
            issues.append(
                f"R-51: ticket_id {ticket!r} was not returned by {FLAG_TOOL} "
                f"(returned: {returned})."
            )

    if (
        decision == "deny"
        and eligibility is not None
        and eligibility.get("reason_code") == "DENY_TOO_EARLY"
        and eligibility["next_eligible_date"] not in response
    ):
        issues.append(
            f"R-50: a too-early deny must give the next eligible date "
            f"{eligibility['next_eligible_date']} in the reply."
        )

    known_dates = observed_dates(observations)
    for date in sorted(set(_ISO_DATE.findall(response)) - known_dates):
        issues.append(f"R-52: the reply states date {date}, which no tool returned.")

    if eligibility is not None:
        evaluated = eligibility.get("item")
        named = {ITEM_WORDS[w] for w in _WORD.findall(response.lower()) if w in ITEM_WORDS}
        for item in sorted(named - {evaluated}):
            issues.append(
                f"R-52: the reply names {item!r}, but the request was evaluated for {evaluated!r}."
            )

    return issues


# ---------- Layer 2: LLM critique ----------

CRITIQUE_PROMPT = """You are the reflection step for an IT equipment request agent. You get the
employee's request, every tool observation, a DRAFT final answer, and the issues a
deterministic checker found. Decide whether the draft is correct and safe to send.

The input gives "required_decision", computed by code from the latest
check_request_eligibility "status" (or escalate when an accommodation need, R-40, was
detected). It is authoritative: final.decision MUST equal required_decision. NEVER change an
R-40 escalation to approve or deny. Do not compare dates yourself: the tool already did
that at "evaluation_date". A next_eligible_date before evaluation_date means the refresh is
due, not that the employee must wait.

Check that:
- An escalation keeps its ticket_id (which must come from flag_for_human_review) and has a
  plain-language escalation_reason. For approve and deny, ticket_id and escalation_reason
  are null by design; that is correct, never an issue.
- The reply states only facts found in the observations (R-52): no invented dates, items,
  models, sizes, specs, quantities, delivery times or promises. Remove anything else.
- A DENY_TOO_EARLY deny cites the rule and gives next_eligible_date (R-50).
- Every deterministic issue listed is fixed.

Reply with ONE JSON object:
{"verdict": "confirmed" | "revised",
 "issues": ["<each problem you found; empty if none>"],
 "final": {"decision", "response_to_employee", "policy_basis", "escalation_reason",
           "ticket_id"}}
Be conservative. Revise ONLY for a real violation of the rules above. Generic next steps
("we will process this shortly", "the IT team will follow up") are fine. Specific claims
that no observation supports (a date, a day of the week, an item other than the one
evaluated, a size, model or spec, a quantity, a delivery time) are violations. Do not
revise for tone or style, and do not add details that rules do not require.

Most drafts are correct and should be confirmed. Each issue you report must name the rule it
breaks (R-40, R-44, R-50, R-51 or R-52) and quote the exact words from the draft at fault.
If you cannot do both, it is not an issue.

Examples (approve, APPROVE_REFRESH_DUE for a laptop):
- "Your laptop request is approved as your current one is due for refresh. We will process
  this shortly." -> confirmed, issues []. "Due for refresh" restates the tool message;
  "we will process this shortly" is a generic next step.
- "Approved! We'll ship your new laptop on Monday." -> revised, issues ["R-52: 'on Monday'
  is a delivery day no observation gives"], and final removes only that claim.

Use "confirmed" with the draft unchanged as "final" if nothing needs fixing. Use "revised"
with a corrected "final" otherwise, changing only what is needed and keeping everything
else exactly as it was."""


@dataclass
class Critique:
    """Parsed Layer 2 output."""

    verdict: str
    issues: list[str]
    final: dict[str, Any] | None


def critique_draft(
    llm: LLM,
    request: str,
    draft: dict[str, Any],
    observations: list[dict[str, Any]],
    accommodation_flag: bool,
    layer1_issues: list[str],
) -> Critique:
    """Run the LLM critique. An unusable reply becomes a 'confirmed' with an issue noted."""
    eligibility = latest_eligibility(observations)
    payload = {
        "request": request,
        "evaluation_date": eligibility.get("as_of") if eligibility else None,
        "required_decision": required_decision(observations, accommodation_flag),
        "accommodation_need_detected_R40": accommodation_flag,
        "observations": observations,
        "draft": draft,
        "deterministic_issues": layer1_issues,
    }
    messages = [
        {"role": "system", "content": CRITIQUE_PROMPT},
        {"role": "user", "content": json.dumps(payload, indent=2)},
    ]
    try:
        reply = json.loads(llm(messages))
    except (json.JSONDecodeError, TypeError):
        return Critique("confirmed", ["Layer 2 reply was not valid JSON; draft kept."], None)
    if not isinstance(reply, dict):
        return Critique("confirmed", ["Layer 2 reply was not a JSON object; draft kept."], None)

    verdict = reply.get("verdict") if reply.get("verdict") in ("confirmed", "revised") else None
    issues = [str(i) for i in reply.get("issues", []) if str(i).strip()]
    final = reply.get("final") if isinstance(reply.get("final"), dict) else None
    if verdict is None:
        return Critique("confirmed", [*issues, "Layer 2 gave no valid verdict; draft kept."], None)
    return Critique(verdict, issues, final)


# ---------- both layers ----------


@dataclass
class ReflectionResult:
    """Outcome of reflecting on one draft."""

    verdict: str  # "confirmed", "revised", or "rejected" (issues remain; back to the agent)
    layer1_issues: list[str]
    layer2_verdict: str
    layer2_issues: list[str]
    draft: dict[str, Any]
    final: dict[str, Any]
    remaining_issues: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def accepted(self) -> bool:
        """True if the final answer passes every Layer 1 check."""
        return not self.remaining_issues


def reflect(
    llm: LLM,
    request: str,
    draft: dict[str, Any],
    observations: list[dict[str, Any]],
    accommodation_flag: bool,
) -> ReflectionResult:
    """Run Layer 1 and Layer 2 on a draft and decide what the final answer is."""
    draft = normalize_final(draft)
    layer1 = check_draft(draft, observations, accommodation_flag)
    critique = critique_draft(llm, request, draft, observations, accommodation_flag, layer1)
    notes: list[str] = []

    candidate = draft
    if critique.verdict == "revised" and critique.final is not None:
        candidate = normalize_final(critique.final)
        if candidate["decision"] not in DECISIONS:
            notes.append("Revision discarded: its decision is not approve, deny or escalate.")
            candidate = draft
        elif not all(
            isinstance(candidate[key], str) and candidate[key].strip()
            for key in ("response_to_employee", "policy_basis")
        ):
            notes.append("Revision discarded: it left response_to_employee or policy_basis empty.")
            candidate = draft

    # R-40 guard: never turn an accommodation escalation back into approve or deny.
    r40_escalation = accommodation_flag and draft["decision"] == "escalate"
    if r40_escalation and candidate["decision"] != "escalate":
        notes.append("Revision discarded: an R-40 escalation can never become approve or deny.")
        candidate = draft

    remaining = check_draft(candidate, observations, accommodation_flag)
    if remaining and candidate is not draft and not layer1:
        notes.append("Revision discarded: it broke Layer 1 rules the draft passed.")
        candidate, remaining = draft, []

    if candidate != draft:
        verdict = "revised"
    else:
        verdict = "confirmed" if not remaining else "rejected"
    return ReflectionResult(
        verdict=verdict,
        layer1_issues=layer1,
        layer2_verdict=critique.verdict,
        layer2_issues=critique.issues,
        draft=draft,
        final=candidate,
        remaining_issues=remaining,
        notes=notes,
    )


def print_reflection(result: ReflectionResult) -> None:
    """Print the === REFLECTION === block: verdict, every issue, before/after if revised."""
    print("\n=== REFLECTION ===")
    print(f"Layer 1 (deterministic checks): {len(result.layer1_issues)} issue(s)")
    for issue in result.layer1_issues:
        print(f"  - {issue}")
    print(f"Layer 2 (LLM critique): {result.layer2_verdict}, {len(result.layer2_issues)} issue(s)")
    for issue in result.layer2_issues:
        print(f"  - {issue}")
    for note in result.notes:
        print(f"  ! {note}")
    print(f"Verdict: {result.verdict.upper()}")
    if result.verdict == "revised":
        before, after = result.draft, result.final
        if before["decision"] != after["decision"]:
            print(f"Decision before: {before['decision']}")
            print(f"Decision after:  {after['decision']}")
        print(f"Response before: {before['response_to_employee']}")
        print(f"Response after:  {after['response_to_employee']}")
    if result.remaining_issues:
        print("Still failing after reflection (sent back to the agent):")
        for issue in result.remaining_issues:
            print(f"  - {issue}")
    print("==================")
