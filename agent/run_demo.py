"""Run the agent on every demo case from docs/REQUIREMENTS.md section 7 and summarize.

Deletes the escalations file first so tickets start at ESC-0001. Each case's full output
(header, trace, reflection block, final answer) is printed and saved to
demo_outputs/case_<N>.txt. Exits 1 if any case does not match its expected outcome.

Usage: python -m agent.run_demo
"""

import asyncio
import contextlib
import io
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from agent.agent import AgentRun, evaluation_date_label, make_openai_llm, run_agent_detailed
from agent.mcp_client import MCPClient
from agent.reflection import latest_eligibility

REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = REPO_ROOT / "demo_outputs"


@dataclass(frozen=True)
class DemoCase:
    """One demo request and its expected outcome (REQUIREMENTS.md section 7)."""

    label: str
    employee_id: str
    text: str
    expected_decision: str
    expected_reason_code: str

    @property
    def request(self) -> str:
        """The request exactly as the agent receives it."""
        return f"Employee {self.employee_id}: {self.text}"


DEMO_CASES = [
    DemoCase(
        "1",
        "E1001",
        "My laptop is over three years old and the battery dies by lunch. Can I get a new one?",
        "approve",
        "APPROVE_REFRESH_DUE",
    ),
    DemoCase(
        "2",
        "E1002",
        "I'd like a new laptop, mine feels a bit slow.",
        "deny",
        "DENY_TOO_EARLY",
    ),
    DemoCase(
        "3",
        "E1003",
        "Can I get a new laptop? Mine is almost two years old.",
        "escalate",
        "ESCALATE_BORDERLINE",
    ),
    DemoCase(
        "4",
        "E1004",
        "Could I get a standing desk for my workstation?",
        "escalate",
        "ESCALATE_UNKNOWN_ITEM",
    ),
    DemoCase(
        "bonus",
        "E1002",
        "My doctor diagnosed RSI and recommended an ergonomic keyboard. Can I get one?",
        "escalate",
        "ESCALATE_ACCOMMODATION",
    ),
]


class Tee(io.TextIOBase):
    """Write to the terminal and keep a copy, so each case is shown and saved."""

    def __init__(self, terminal: Any) -> None:
        self.terminal = terminal
        self.buffer = io.StringIO()

    def write(self, text: str) -> int:
        self.terminal.write(text)
        self.terminal.flush()
        return self.buffer.write(text)

    def flush(self) -> None:
        self.terminal.flush()


def escalations_path() -> Path:
    """The file flag_for_human_review writes to (the server runs from the repo root)."""
    path = Path(os.environ.get("ESCALATIONS_FILE", "").strip() or "escalations.jsonl")
    return path if path.is_absolute() else REPO_ROOT / path


def reason_code_of(run: AgentRun) -> str | None:
    """R-40 if it fired, else the reason_code of the latest eligibility result."""
    if run.accommodation["needs_accommodation"] and run.final["decision"] == "escalate":
        return "ESCALATE_ACCOMMODATION"
    eligibility = latest_eligibility(run.observations)
    return eligibility["reason_code"] if eligibility else None


def outcome(case: DemoCase, run: AgentRun) -> str:
    """PASS if decision and reason code match, and every escalation has a ticket."""
    ok = (
        run.final["decision"] == case.expected_decision
        and reason_code_of(run) == case.expected_reason_code
        and (case.expected_decision != "escalate" or bool(run.final["ticket_id"]))
    )
    return "PASS" if ok else "MISMATCH"


async def run_case(case: DemoCase, client: MCPClient, llm: Any) -> AgentRun:
    """Run one case with output teed to the terminal and to demo_outputs/case_<N>.txt."""
    tee = Tee(sys.stdout)
    with contextlib.redirect_stdout(tee):
        title = f"CASE {case.label.upper()}: Expected {case.expected_decision.upper()}"
        print("=" * 80)
        print(f"{title} ({case.expected_reason_code})")
        print("=" * 80)
        print(f"Evaluation date: {evaluation_date_label()}")
        run = await run_agent_detailed(case.request, client, llm)
        print()
    (OUTPUT_DIR / f"case_{case.label}.txt").write_text(tee.buffer.getvalue(), encoding="utf-8")
    return run


def print_summary(rows: list[tuple[DemoCase, AgentRun]]) -> None:
    """Print the summary table: one row per case."""
    headers = ["Case", "Expected", "Actual", "Reason code", "Ticket", "Escalation reason", "Result"]
    table = [
        [
            case.label,
            case.expected_decision,
            run.final["decision"],
            reason_code_of(run) or "-",
            run.final["ticket_id"] or "-",
            shorten(run.final["escalation_reason"] or "-", 50),
            outcome(case, run),
        ]
        for case, run in rows
    ]
    widths = [max(len(str(r[i])) for r in [headers, *table]) for i in range(len(headers))]
    line = "-+-".join("-" * w for w in widths)
    print("\n" + "=" * len(line))
    print("SUMMARY")
    print("=" * len(line))
    for row in [headers, *table]:
        print(" | ".join(str(cell).ljust(w) for cell, w in zip(row, widths, strict=True)))
        if row is headers:
            print(line)
    passed = sum(outcome(case, run) == "PASS" for case, run in rows)
    print(f"\n{passed}/{len(rows)} cases PASS")


def shorten(text: str, limit: int) -> str:
    """Trim text to limit characters for the table."""
    return text if len(text) <= limit else text[: limit - 3] + "..."


async def main() -> int:
    """Run every demo case and return the process exit code."""
    load_dotenv(REPO_ROOT / ".env")
    if not os.environ.get("OPENAI_API_KEY"):
        sys.exit("OPENAI_API_KEY is not set (add it to .env).")
    model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

    escalations = escalations_path()
    escalations.unlink(missing_ok=True)
    OUTPUT_DIR.mkdir(exist_ok=True)
    print(f"Evaluation date: {evaluation_date_label()}")
    print(f"Model: {model}")
    print(f"Cleared {escalations.name}; outputs go to {OUTPUT_DIR.name}/\n")

    llm = make_openai_llm(model)
    rows = []
    async with MCPClient() as client:
        for case in DEMO_CASES:
            rows.append((case, await run_case(case, client, llm)))

    print_summary(rows)
    print(f"\nEscalations recorded in {escalations.name}:")
    print(escalations.read_text(encoding="utf-8") if escalations.exists() else "(none)")
    return 0 if all(outcome(c, r) == "PASS" for c, r in rows) else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(line_buffering=True)
    sys.exit(asyncio.run(main()))
