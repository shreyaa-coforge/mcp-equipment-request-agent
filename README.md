# IT Equipment Request Agent (MCP + ReAct)

An internal IT equipment request handler. An employee asks for equipment in plain language
("Employee E1002: I'd like a new laptop"). A ReAct agent built on the OpenAI API investigates
the request through an MCP server, then approves, denies or escalates it to a person. Policy
decisions are deterministic code in the server, not LLM guesses. Every draft answer goes
through a reflection step before it is final. Rules and tool contracts are specified in
[docs/REQUIREMENTS.md](docs/REQUIREMENTS.md).

## Setup

Requires Python 3.11 or later.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then set OPENAI_API_KEY in .env
```

`.env` also sets `OPENAI_MODEL` (default `gpt-4o-mini`) and `REFERENCE_DATE=2026-11-24`,
which pins the evaluation date so the demo outcomes below are reproducible.

## Commands

| Command | What it does | Needs API key |
|---|---|---|
| `pytest -v` | Unit and regression tests (the agent is tested with a scripted fake model) | No |
| `python scripts/minimal_client.py` | One-tool MCP connectivity check (`ping`) | No |
| `python scripts/client_check.py` | Calls all 4 tools through MCP | No |
| `python -m agent.agent "Employee E1001: <request>"` | Runs the agent on one request and prints the full trace | Yes |
| `python -m agent.run_demo` | Runs all demo cases, saves traces to `demo_outputs/` and prints a summary | Yes |
| `python -m agent.agent --demo-reflection` | Shows reflection catching a deliberately flawed draft | Yes |

CI (`.github/workflows/ci.yml`) runs ruff, pytest and both MCP client scripts on
Python 3.11 and 3.12. No step needs an API key.

## Architecture

- **MCP server** (`server/`). Business logic lives in plain functions in `server/tools.py`;
  `server/server.py` only wraps them with `@mcp.tool()`. Tools never raise: bad input
  returns an error dict. The four tools:
  - `get_employee_info(employee_id)`: role, tenure and equipment on file.
  - `get_policy_limits(role)`: what a role may receive and how often.
  - `check_request_eligibility(employee_id, item)`: the authoritative decision
    (`approve` / `deny` / `escalate` plus a reason code).
  - `flag_for_human_review(employee_id, request, reason)`: records a ticket (`ESC-0001`, ...)
    in `escalations.jsonl`.
- **ReAct agent** (`agent/agent.py`). Each model turn returns JSON
  `{thought, action, action_input}`, and every step is printed as Thought / Action /
  Observation (at most 8 steps). Code enforces the rules: the agent must check eligibility
  for the requesting employee before answering, and an escalation needs a real ticket. A
  keyword check (`agent/accommodation.py`) forces escalation for medical, accessibility or
  safety needs.
- **Reflection** (`agent/reflection.py`). Layer 1 is deterministic. It checks that the
  decision matches the tool's status (unless an accommodation applies), that escalations have
  a real ticket, and that the reply states no dates or items absent from tool results.
  Layer 2 is an LLM critique that can revise the reply. Its revisions are kept only if they
  pass Layer 1, and it can never turn an accommodation escalation into approve or deny.
- **Why stdio.** The agent launches the server as a child process and talks MCP over its
  stdin and stdout. That needs no network port, no extra service and no auth. It also uses
  the same virtualenv, so CI can run the server and client in one job. Because stdout is the
  protocol channel, the server logs only to stderr.

## Demo cases (`REFERENCE_DATE=2026-11-24`)

| # | Request | Outcome |
|---|---|---|
| 1 | E1001 (engineer): "My laptop is over three years old and the battery dies by lunch." | **approve**: 36-month refresh is due (`APPROVE_REFRESH_DUE`) |
| 2 | E1002 (standard): "I'd like a new laptop, mine feels a bit slow." | **deny**: next eligible 2029-02-01 (`DENY_TOO_EARLY`) |
| 3 | E1003 (manager): "Can I get a new laptop? Mine is almost two years old." | **escalate**: eligible in 26 days, inside the 60-day window (`ESCALATE_BORDERLINE`) |
| 4 | E1004 (contractor): "Could I get a standing desk for my workstation?" | **escalate**: not a covered item (`ESCALATE_UNKNOWN_ITEM`) |
| Bonus | E1002: "My doctor diagnosed RSI and recommended an ergonomic keyboard." | **escalate**: the tool says approve, but the accommodation rule overrides it (`ESCALATE_ACCOMMODATION`) |
