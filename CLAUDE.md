# Project briefing: IT Equipment Request Handler (MCP server + ReAct agent)

Read this whole file before doing anything. It is the source of truth for this repo.

## What we are building
An internal IT equipment request handler:
1. An MCP server (Python, `mcp` SDK v2) exposing four tools over mock data.
2. A ReAct agent (OpenAI API) that connects to the server as an MCP client, investigates a
   natural-language request, and approves, denies, or escalates it.
3. A reflection step that checks every draft before it is finalized.
4. Unit tests for every tool's plain function, run in GitHub Actions CI.

## Grading rubric (100 points). Every change must serve one of these.
| Criteria | Points |
|---|---|
| Requirements doc and policy rules are clear and specific enough to be testable | 8 |
| Minimal one-tool server/client connection confirmed working before full build | 7 |
| MCP server correctly implements all four tools | 12 |
| Unit tests cover normal and edge cases for each tool, and pass | 13 |
| Agent correctly connects to the MCP server and follows ReAct pattern | 15 |
| Agent correctly approves/denies clear-cut requests | 10 |
| Agent correctly escalates ambiguous requests instead of guessing, with clear reasons | 15 |
| Reflection step demonstrably improves or confirms at least one draft | 10 |
| Submitted and passing through the pipeline | 10 |

## Required tools (exact names and parameters)
- `get_employee_info(employee_id)` returns role, tenure, and current equipment on file
- `get_policy_limits(role)` returns what that role is eligible for and how often
- `check_request_eligibility(employee_id, item)` returns whether the request falls within policy
- `flag_for_human_review(employee_id, request, reason)` side-effecting; escalates a request

## CRITICAL: MCP Python SDK v2 gotchas (we use mcp>=2.3,<3)
Most tutorials online are for v1 and WILL BREAK. Use these v2 names:
- Server: `from mcp.server.mcpserver import MCPServer` then `mcp = MCPServer("name")`.
  NOT `from mcp.server.fastmcp import FastMCP` (raises ModuleNotFoundError in v2).
- Register tools with `@mcp.tool()`. The decorator returns the original function.
- Run with `mcp.run()` (stdio transport by default).
- Client: `from mcp import ClientSession, StdioServerParameters` and
  `from mcp.client.stdio import stdio_client`. Always call `await session.initialize()` first.
- Launch the server with `StdioServerParameters(command=sys.executable, args=[...])` so it
  uses the same virtualenv Python.
- Field names are snake_case in v2: `tool.input_schema` (NOT inputSchema),
  `result.structured_content`, `result.is_error`. Text output is `result.content[0].text`.
- NEVER `print()` to stdout inside the server process. stdout is the protocol channel.
  Log to stderr only (`print(..., file=sys.stderr)` or the logging module).

## Architecture rules
- Business logic lives in plain functions in `server/tools.py`. `server/server.py` only
  wraps them with `@mcp.tool()`. Unit tests import `server/tools.py` directly, never MCP.
- Decision logic is deterministic and lives in `check_request_eligibility`, NOT in the LLM.
  The LLM reads a clear `status` field and follows the rules in the system prompt.
- Tools never raise on bad input. They return `{"found": False, "error": "..."}` style dicts.
- Mock data lives in `server/data/employees.json` and `server/data/policies.json`.
- `check_request_eligibility` accepts an optional `as_of` date (ISO string) in the plain
  function for deterministic tests; the MCP wrapper does NOT expose `as_of`.
  `get_employee_info` takes the same optional `as_of` (for `tenure_months`).
- Evaluation date: `as_of` if given, else env var `REFERENCE_DATE`, else today (UTC). The MCP
  wrappers always pass `REFERENCE_DATE` for both tenure and eligibility.
- `flag_for_human_review` appends one JSON line to the file at env var `ESCALATIONS_FILE`
  (default `escalations.jsonl`) and returns a ticket id like `ESC-0001`. Tests point
  `ESCALATIONS_FILE` at pytest's `tmp_path`.
- Item names are normalized (lowercase, trimmed, simple synonyms like "screen" to "monitor").

## Agent rules
- OpenAI Chat Completions API, model from env var `OPENAI_MODEL`, key from `OPENAI_API_KEY`
  loaded via python-dotenv from `.env`. Never hardcode or print the key.
- Explicit ReAct loop: each model turn returns JSON (response_format json_object):
  `{"thought": str, "action": <tool name or "final_answer">, "action_input": {...}}`.
  The agent prints `Thought:`, `Action:`, `Observation:` for every step.
- Tool descriptions given to the model come from `session.list_tools()` at runtime.
- Print the evaluation date (`REFERENCE_DATE` or today) as the first line of every trace.
- Max 8 steps. Validate the action name against the tool list; feed errors back as observations.
- Final answer JSON: `decision` (approve | deny | escalate), `response_to_employee`,
  `policy_basis`, `escalation_reason` (required when escalating), `ticket_id` (when escalating).
- The agent must call `check_request_eligibility` before any final answer.
- If the decision is escalate, the agent must have called `flag_for_human_review`. If not,
  the loop feeds that back as an observation and continues.

## Confirmed design decisions (see docs/REQUIREMENTS.md for full rules)
- `docs/REQUIREMENTS.md` is the spec for policy values, rule order, reason codes and tool
  output schemas. Code and tests must match it.
- No quantity handling: `check_request_eligibility(employee_id, item)` has exactly those two
  MCP parameters. There is no max-quantity rule and no `max_quantity` field anywhere.
- Rule order principle: explicit prohibitions are checked first and denied; uncertainty is
  escalated. Unknown employee, role or item is escalated before prohibitions, because policy
  can't be applied without them.
- Deny vs. escalate line: more than 60 days until eligible is a deny, 1 to 60 days is an
  escalate, and 0 or fewer days is an approve.
- `REFERENCE_DATE=2026-11-24` (in `.env.example`) pins the demo date. Demo expectations in
  the requirements doc assume it.
- Accommodation (medical, accessibility or safety need) is detected by the plain function
  `detect_accommodation_need(reason)` in `agent/accommodation.py`, with unit tests in
  `tests/test_accommodation.py`. If it fires, the final decision is escalate, overriding
  the tool's approve or deny. Reflection (both layers) must never revise an R-40 escalation
  back to approve or deny.
- Equipment records: one record per physical unit (`item`, `issued_date`), no quantity field.
  Eligibility uses the most recent `issued_date` for the item.
- Evaluation order (first match wins): invalid input, employee not found, no role policy,
  unknown item (all escalate); not allowed for role (deny); never issued (approve); then date
  rules only: more than 60 days (deny), 1 to 60 days (escalate), 0 or fewer (approve).

## Reflection rules
- Runs on every draft before it is finalized. Two layers:
  1. Deterministic checks: decision matches eligibility status, unless R-40 (accommodation)
     applies, in which case escalate is the only valid decision; escalation has a ticket id;
     the draft does not name dates, items, or quantities absent from tool observations.
  2. LLM critique: given the draft and ALL observations, return JSON
     `{"verdict": "confirmed" | "revised", "issues": [...], "final": {...}}`.
- Print a clear `=== REFLECTION ===` block showing verdict and issues.
- `python -m agent.agent --demo-reflection` runs reflection on a deliberately flawed,
  clearly labeled demo draft to prove it catches problems.

## Commands
- Tests: `pytest -v`
- Minimal connectivity: `python scripts/minimal_client.py`
- Full tool check through MCP: `python scripts/client_check.py`
- Agent: `python -m agent.agent "<request text>"` and `python -m agent.run_demo` for all cases

## Working style
- Build in small steps. After each step, run the relevant command and show the output.
- Do not move on while anything is failing.
- Do not commit `.env`, `.venv/`, or `escalations.jsonl`.
- Keep code readable: type hints, docstrings, small functions.