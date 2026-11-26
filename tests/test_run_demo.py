"""run_demo regression tests: a case that raises is marked ERROR and the next case still runs.

No API key: a scripted fake model; tools run on the real MCP server over stdio.
"""

import asyncio
import json

from agent import reflection, run_demo
from agent.mcp_client import MCPClient

E1001_TEXT = "My laptop is over three years old and the battery dies by lunch."
FAILING = run_demo.DemoCase("fail", "E1002", "I'd like a new laptop.", "deny", "DENY_TOO_EARLY")
WORKING = run_demo.DemoCase("ok", "E1001", E1001_TEXT, "approve", "APPROVE_REFRESH_DUE")


class OutageThenScriptLLM:
    """Raises for E1002 requests (a simulated API outage); scripts an approve for E1001."""

    def __init__(self) -> None:
        self.turns = [
            json.dumps(
                {
                    "thought": "check",
                    "action": "check_request_eligibility",
                    "action_input": {"employee_id": "E1001", "item": "laptop"},
                }
            ),
            json.dumps(
                {
                    "thought": "done",
                    "action": "final_answer",
                    "action_input": {
                        "decision": "approve",
                        "response_to_employee": "Your laptop refresh is approved.",
                        "policy_basis": "R-31 APPROVE_REFRESH_DUE: refresh is due.",
                    },
                }
            ),
        ]

    def __call__(self, messages):
        if messages[0]["content"] == reflection.CRITIQUE_PROMPT:
            draft = json.loads(messages[1]["content"])["draft"]
            return json.dumps({"verdict": "confirmed", "issues": [], "final": draft})
        if "Employee E1002" in messages[1]["content"]:
            raise RuntimeError("simulated API outage")
        return self.turns.pop(0)


def test_case_error_is_reported_and_next_case_runs(tmp_path, monkeypatch, capsys):
    """Spec 7: an exception in one case marks it ERROR; the following case still runs."""
    monkeypatch.setenv("REFERENCE_DATE", "2026-11-24")
    monkeypatch.setenv("ESCALATIONS_FILE", str(tmp_path / "escalations.jsonl"))

    async def go():
        async with MCPClient() as client:
            return await run_demo.run_all(
                [FAILING, WORKING], client, OutageThenScriptLLM(), tmp_path
            )

    failed, passed = asyncio.run(go())
    assert run_demo.outcome(failed) == "ERROR"
    assert "simulated API outage" in failed.error
    assert run_demo.outcome(passed) == "PASS"
    assert "simulated API outage" in (tmp_path / "case_fail.txt").read_text()

    run_demo.print_summary([failed, passed])
    summary = capsys.readouterr().out.split("SUMMARY", 1)[1]
    assert "ERROR" in summary and "PASS" in summary
    assert "1/2 cases PASS" in summary
