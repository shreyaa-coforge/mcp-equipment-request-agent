"""Agent loop regression tests with a scripted fake model.

No API key is needed: the model is a scripted FakeLLM, while the tools run on the real MCP
server over stdio. REFERENCE_DATE and ESCALATIONS_FILE are set with monkeypatch, so the
evaluation date is fixed and tickets go to pytest's tmp_path.
"""

import asyncio
import json

import pytest

from agent import agent as agent_module
from agent import reflection
from agent.mcp_client import MCPClient


@pytest.fixture(autouse=True)
def fixed_env(tmp_path, monkeypatch):
    """Pin the evaluation date and keep tickets out of the repo."""
    monkeypatch.setenv("REFERENCE_DATE", "2026-11-24")
    monkeypatch.setenv("ESCALATIONS_FILE", str(tmp_path / "escalations.jsonl"))


def act(action: str, **args) -> str:
    """One scripted model turn."""
    return json.dumps({"thought": f"scripted: {action}", "action": action, "action_input": args})


def final(**fields) -> str:
    """A scripted final-answer turn."""
    return act("final_answer", **fields)


class FakeLLM:
    """Replays scripted agent turns; answers reflection critiques with 'confirmed'.

    After the script runs out it returns invalid JSON, which drives the loop to MAX_STEPS.
    """

    def __init__(self, turns: list[str]) -> None:
        self.turns = list(turns)
        self.agent_messages: list[dict[str, str]] = []

    def __call__(self, messages: list[dict[str, str]]) -> str:
        if messages[0]["content"] == reflection.CRITIQUE_PROMPT:
            draft = json.loads(messages[1]["content"])["draft"]
            return json.dumps({"verdict": "confirmed", "issues": [], "final": draft})
        self.agent_messages = messages
        return self.turns.pop(0) if self.turns else "not json"

    def observations(self) -> list[str]:
        """Every observation the loop fed back to the model."""
        return [
            m["content"]
            for m in self.agent_messages
            if m["role"] == "user" and m["content"].startswith("Observation:")
        ]


def run(request: str, llm: FakeLLM) -> agent_module.AgentRun:
    """Run the agent on one request against the real MCP server."""

    async def go() -> agent_module.AgentRun:
        async with MCPClient() as client:
            return await agent_module.run_agent_detailed(request, client, llm)

    return asyncio.run(go())


# ---------- fix 1: R-42 escalations survive reflection ----------


def test_r42_escalation_with_real_ticket_is_accepted(capsys):
    """R-42 / R-44: a model-applied R-40 escalation with a real ticket is not rejected."""
    llm = FakeLLM(
        [
            act("check_request_eligibility", employee_id="E1001", item="monitor"),
            act(
                "flag_for_human_review",
                employee_id="E1001",
                request="lower monitor mount",
                reason="Employee says: 'I use a wheelchair'.",
            ),
            final(
                decision="escalate",
                response_to_employee="A person on the IT team will review your request.",
                policy_basis="R-40 ESCALATE_ACCOMMODATION: 'I use a wheelchair' (R-42).",
                escalation_reason="Accessibility need: 'I use a wheelchair'.",
                ticket_id="ESC-0001",
            ),
        ]
    )
    result = run("Employee E1001: I use a wheelchair and need a lower monitor mount.", llm)
    assert (result.final["decision"], result.final["ticket_id"]) == ("escalate", "ESC-0001")
    assert not any("Reflection rejected" in o for o in llm.observations())
    assert "Verdict: CONFIRMED" in capsys.readouterr().out


# ---------- fix 2: eligibility must be for the requesting employee and item ----------


def test_eligibility_for_another_employee_does_not_satisfy_r43():
    """R-43: eligibility checked for E1001 on an E1002 request is fed back, not accepted."""
    llm = FakeLLM(
        [
            act("check_request_eligibility", employee_id="E1001", item="laptop"),
            final(decision="approve", response_to_employee="Approved.", policy_basis="R-31"),
            act("check_request_eligibility", employee_id="E1002", item="laptop"),
            final(
                decision="deny",
                response_to_employee="Laptops refresh every 48 months; eligible on 2029-02-01.",
                policy_basis="R-21 DENY_TOO_EARLY: Standard employees may refresh a laptop "
                "every 48 months.",
            ),
        ]
    )
    result = run("Employee E1002: I'd like a new laptop, mine feels a bit slow.", llm)
    observations = llm.observations()
    assert "E1001" in observations[0] and "E1002" in observations[0]
    assert "R-43" in observations[1]
    assert result.final["decision"] == "deny"


def test_eligibility_for_another_item_is_rejected_by_reflection():
    """R-44 / R-45: checking 'mouse' on a laptop request is a reflection issue."""
    llm = FakeLLM(
        [
            act("check_request_eligibility", employee_id="E1002", item="mouse"),
            final(decision="approve", response_to_employee="Approved.", policy_basis="R-30"),
            act("check_request_eligibility", employee_id="E1002", item="laptop"),
            final(
                decision="deny",
                response_to_employee="Laptops refresh every 48 months; eligible on 2029-02-01.",
                policy_basis="R-21 DENY_TOO_EARLY: Standard employees may refresh a laptop "
                "every 48 months.",
            ),
        ]
    )
    result = run("Employee E1002: I'd like a new laptop, mine feels a bit slow.", llm)
    rejected = [o for o in llm.observations() if "Reflection rejected" in o]
    assert rejected and "mouse" in rejected[0]
    assert result.final["decision"] == "deny"


# ---------- fix 4: the 8-step fallback ----------


def test_fallback_reports_missing_ticket_when_flag_fails(tmp_path, monkeypatch, capsys):
    """R-51: if the fallback flag fails, the answer says no ticket could be created."""
    monkeypatch.setenv("ESCALATIONS_FILE", str(tmp_path))  # a directory: the write fails
    result = run("Employee E1003: Can I get a new laptop?", FakeLLM([]))
    assert result.final["decision"] == "escalate"
    assert result.final["ticket_id"] is None
    assert "no ticket could be created" in result.final["response_to_employee"].lower()
    out = capsys.readouterr().out
    assert out.index("=== REFLECTION ===") > out.index("No valid final answer")


def test_fallback_with_ticket_runs_through_reflection(capsys):
    """R-44: the fallback escalation is a draft like any other and is reflected on."""
    result = run("Employee E1003: Can I get a new laptop?", FakeLLM([]))
    assert (result.final["decision"], result.final["ticket_id"]) == ("escalate", "ESC-0001")
    out = capsys.readouterr().out
    assert out.index("=== REFLECTION ===") > out.index("No valid final answer")
