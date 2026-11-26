"""ReAct agent for IT equipment requests (CLAUDE.md "Agent rules", REQUIREMENTS.md section 6).

Each model turn returns JSON {"thought", "action", "action_input"}. The agent runs the
action as an MCP tool call, prints the step, and feeds the observation back, until the
model gives a valid final answer or MAX_STEPS is reached. Every draft final answer goes
through reflection (agent/reflection.py, both layers) before it is finalized.

Usage:
    python -m agent.agent "Employee E1001: <request text>"
    python -m agent.agent --demo-reflection
"""

import argparse
import asyncio
import json
import os
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from agent.accommodation import detect_accommodation_need
from agent.mcp_client import MCPClient
from agent.reflection import normalize_final, print_reflection, reflect

MAX_STEPS = 8
FINAL_ACTION = "final_answer"
ELIGIBILITY_TOOL = "check_request_eligibility"
FLAG_TOOL = "flag_for_human_review"
DECISIONS = ("approve", "deny", "escalate")
NO_DECISION_REASON = "agent could not reach a decision"

# reason_code -> rule ID (docs/REQUIREMENTS.md section 3 and R-40), cited in policy_basis.
RULE_IDS = {
    "ESCALATE_INVALID_INPUT": "R-10",
    "ESCALATE_EMPLOYEE_NOT_FOUND": "R-11",
    "ESCALATE_NO_POLICY_FOR_ROLE": "R-12",
    "ESCALATE_UNKNOWN_ITEM": "R-13",
    "ESCALATE_BORDERLINE": "R-15",
    "DENY_NOT_ALLOWED_FOR_ROLE": "R-20",
    "DENY_TOO_EARLY": "R-21",
    "APPROVE_NEVER_ISSUED": "R-30",
    "APPROVE_REFRESH_DUE": "R-31",
    "ESCALATE_ACCOMMODATION": "R-40",
}

_EMPLOYEE_PREFIX = re.compile(r"^\s*Employee\s+([A-Za-z0-9]+)\s*:\s*(.*)$", re.DOTALL)

# Takes the chat messages, returns the model's raw reply text (expected to be a JSON object).
LLM = Callable[[list[dict[str, str]]], str]


# ---------- request parsing and prompts ----------


def parse_request(request: str) -> tuple[str | None, str]:
    """Split "Employee E1001: <text>" into ("E1001", "<text>"). No prefix gives (None, request)."""
    match = _EMPLOYEE_PREFIX.match(request)
    if match:
        return match.group(1).upper(), match.group(2).strip()
    return None, request.strip()


def evaluation_date_label() -> str:
    """Return the evaluation date the server will use: REFERENCE_DATE, else today (UTC)."""
    reference = os.environ.get("REFERENCE_DATE", "").strip()
    if reference:
        return reference
    return f"{datetime.now(timezone.utc).date().isoformat()} (today, UTC)"


def build_system_prompt(tools: list[dict[str, Any]]) -> str:
    """Build the system prompt: tool list (from list_tools), JSON format, and decision rules."""
    tool_lines = "\n\n".join(
        f"- {tool['name']}\n  description: {tool['description'].strip()}\n"
        f"  input_schema: {json.dumps(tool['input_schema'])}"
        for tool in tools
    )
    rule_ids = ", ".join(f"{code} = {rule}" for code, rule in RULE_IDS.items())
    return f"""You are an IT equipment request agent. You investigate one employee request
using tools, then approve, deny or escalate it. You work in a ReAct loop.

TOOLS (call them by exact name; action_input must match input_schema):

{tool_lines}

RESPONSE FORMAT. On EVERY turn reply with ONE JSON object and nothing else:
{{"thought": "<your reasoning for this step>",
  "action": "<one tool name above, or \\"{FINAL_ACTION}\\">",
  "action_input": {{<tool arguments, or the final answer object>}}}}
After each tool call you receive "Observation: <JSON result>".

FINAL ANSWER. When action is "{FINAL_ACTION}", action_input must be:
{{"decision": "approve" | "deny" | "escalate",
  "response_to_employee": "<short, polite reply to the employee>",
  "policy_basis": "<rule ID and reason_code, then the rule, e.g. 'R-31 APPROVE_REFRESH_DUE: ...'>",
  "escalation_reason": "<plain-language reason a human must decide; null unless escalating>",
  "ticket_id": "<ticket_id returned by {FLAG_TOOL}; null unless escalating>"}}

RULES:
1. (R-43) Call {ELIGIBILITY_TOOL} before giving any final answer. It is the authoritative
   policy decision.
2. Follow its "status" exactly: approve -> approve, deny -> deny, escalate -> escalate.
   The only exception is an accommodation need (R-40), which is always escalate.
   Do NOT compare dates or do date arithmetic yourself: the tool has already done it at
   the evaluation date (its "as_of"). A next_eligible_date in the past means the refresh
   is due. Your own sense of "today" is wrong; trust status and reason_code.
3. To escalate, first call {FLAG_TOOL} (employee_id, a short request phrase, and the
   plain-language reason), then put the returned ticket_id in the final answer (R-51).
   Never call {FLAG_TOOL} for an approve or a deny.
4. (R-45) Pass the core item noun to tools: "keyboard", not "ergonomic keyboard". If unsure,
   pass the item as written; an unknown item escalates anyway.
5. (R-52) Only state facts (dates, items, numbers, rules) that appear in observations.
   Never invent or estimate a date.
6. (R-50) A deny cites the rule from the tool message. A DENY_TOO_EARLY deny also gives the
   next_eligible_date.
7. policy_basis starts with "<rule ID> <reason_code>:" for the eligibility result's
   reason_code, then the rule from its message. Rule IDs: {rule_ids}.
   When R-40 applies, start with "R-40 ESCALATE_ACCOMMODATION:".
"""


def build_first_message(request: str, accommodation: dict[str, Any], evaluation_date: str) -> str:
    """Build the first user message: evaluation date, request, and the R-40 notice if any."""
    message = (
        f"Evaluation date (today, for all policy purposes): {evaluation_date}\nRequest: {request}"
    )
    if accommodation["needs_accommodation"]:
        terms = ", ".join(accommodation["matched_terms"])
        message += (
            "\n\nR-40 APPLIES: the request mentions an accommodation need (matched terms: "
            f"{terms}). Still call {ELIGIBILITY_TOOL} first, but the final decision MUST be "
            "escalate, whatever its status says. Call "
            f"{FLAG_TOOL} with a reason that quotes the need, and start policy_basis with "
            '"R-40 ESCALATE_ACCOMMODATION".'
        )
    return message


# ---------- validation ----------


def parse_model_turn(raw: str, tool_names: set[str]) -> tuple[dict[str, Any] | None, str | None]:
    """Parse and validate one model turn. Returns (turn, None) or (None, error message)."""
    try:
        turn = json.loads(raw)
    except json.JSONDecodeError as exc:
        return None, f"Your reply was not valid JSON ({exc.msg}). Reply with one JSON object."
    if not isinstance(turn, dict):
        return None, "Your reply must be a JSON object with thought, action, action_input."

    thought = turn.get("thought")
    action = turn.get("action")
    action_input = turn.get("action_input")
    if not isinstance(thought, str) or not thought.strip():
        return None, "Missing 'thought' (a non-empty string)."
    if not isinstance(action, str) or not action.strip():
        return None, "Missing 'action' (a tool name or 'final_answer')."
    if not isinstance(action_input, dict):
        return None, "'action_input' must be a JSON object."
    action = action.strip()
    if action != FINAL_ACTION and action not in tool_names:
        valid = ", ".join(sorted(tool_names | {FINAL_ACTION}))
        return None, f"Unknown action {action!r}. Valid actions: {valid}."
    return {"thought": thought.strip(), "action": action, "action_input": action_input}, None


def check_tool_args(tool: dict[str, Any], args: dict[str, Any]) -> str | None:
    """Return an error if required arguments are missing or unknown ones are given."""
    schema = tool["input_schema"]
    properties = set(schema.get("properties", {}))
    missing = [name for name in schema.get("required", []) if name not in args]
    unknown = [name for name in args if name not in properties]
    if missing:
        return f"{tool['name']} is missing required argument(s): {', '.join(missing)}."
    if unknown:
        return f"{tool['name']} does not accept argument(s): {', '.join(unknown)}."
    return None


@dataclass
class RunState:
    """What the agent has done so far, used to enforce the loop rules."""

    eligibility_results: list[dict[str, Any]] = field(default_factory=list)
    ticket_ids: list[str] = field(default_factory=list)
    # Every MCP tool call, as {"tool", "input", "output"}; reflection reads these.
    observations: list[dict[str, Any]] = field(default_factory=list)


def check_final_answer(answer: dict[str, Any], state: RunState) -> str | None:
    """Return the rule a final answer breaks, or None if the loop may accept it."""
    if not state.eligibility_results:
        return f"R-43: call {ELIGIBILITY_TOOL} before giving a final answer."
    decision = answer.get("decision")
    if decision not in DECISIONS:
        return f"decision must be one of {', '.join(DECISIONS)}; got {decision!r}."
    for key in ("response_to_employee", "policy_basis"):
        if not isinstance(answer.get(key), str) or not answer[key].strip():
            return f"Final answer is missing '{key}'."
    if decision == "escalate":
        if not state.ticket_ids:
            return (
                f"The decision is escalate but {FLAG_TOOL} has not been called. Call it "
                "first, then give the final answer with its ticket_id."
            )
        if not isinstance(answer.get("escalation_reason"), str) or not answer["escalation_reason"]:
            return "An escalation needs a plain-language 'escalation_reason' (R-51)."
        if answer.get("ticket_id") not in state.ticket_ids:
            return f"ticket_id must be one returned by {FLAG_TOOL}: {', '.join(state.ticket_ids)}."
    return None


# ---------- printing ----------


def print_step(number: int, thought: str | None, action: str, args: Any, observation: Any) -> None:
    """Print one ReAct step: Step N, Thought, Action with args, and Observation as JSON."""
    print(f"\n----- Step {number} -----")
    print(f"Thought: {thought if thought is not None else '(unparseable reply)'}")
    print(f"Action: {action} {json.dumps(args)}")
    print("Observation:")
    print(json.dumps(observation, indent=2))


# ---------- the loop ----------


async def run_agent(request: str, client: MCPClient, llm: LLM) -> dict[str, Any]:
    """Run the ReAct loop for one request and return the final answer dict.

    Args:
        request: "Employee <ID>: <request text>".
        client: An open MCPClient.
        llm: Function from chat messages to the model's raw JSON reply.
    """
    employee_id, reason_text = parse_request(request)
    accommodation = detect_accommodation_need(reason_text)
    print(f"Request: {request}")
    print(f"Accommodation check (R-41): {json.dumps(accommodation)}")

    tools = await client.list_tools()
    tools_by_name = {tool["name"]: tool for tool in tools}
    messages = [
        {"role": "system", "content": build_system_prompt(tools)},
        {
            "role": "user",
            "content": build_first_message(request, accommodation, evaluation_date_label()),
        },
    ]
    state = RunState()

    for step in range(1, MAX_STEPS + 1):
        raw = llm(messages)
        messages.append({"role": "assistant", "content": raw})
        turn, error = parse_model_turn(raw, set(tools_by_name))

        if turn is None:
            observation = {"error": error}
            print_step(step, None, "(invalid)", raw, observation)
        elif turn["action"] == FINAL_ACTION:
            problem = check_final_answer(turn["action_input"], state)
            if problem is None:
                draft = normalize_final(turn["action_input"])
                print(f"\n----- Step {step} -----")
                print(f"Thought: {turn['thought']}")
                print(f"Action: {FINAL_ACTION} (draft)")
                print(json.dumps(draft, indent=2))
                result = reflect(
                    llm, request, draft, state.observations, accommodation["needs_accommodation"]
                )
                print_reflection(result)
                if result.accepted:
                    print_final(result.final)
                    return result.final
                observation = {
                    "error": "Reflection rejected the draft. Fix these and try again: "
                    + " ".join(result.remaining_issues)
                }
                print("Observation:")
                print(json.dumps(observation, indent=2))
            else:
                observation = {"error": problem}
                print_step(step, turn["thought"], FINAL_ACTION, turn["action_input"], observation)
        else:
            observation = await run_tool(turn, tools_by_name, client, state)
            print_step(step, turn["thought"], turn["action"], turn["action_input"], observation)

        messages.append({"role": "user", "content": f"Observation: {json.dumps(observation)}"})

    return await give_up(request, employee_id, client)


async def run_tool(
    turn: dict[str, Any], tools_by_name: dict[str, dict], client: MCPClient, state: RunState
) -> dict[str, Any]:
    """Validate arguments, call the tool through MCP, and record what the loop needs."""
    name, args = turn["action"], turn["action_input"]
    error = check_tool_args(tools_by_name[name], args)
    if error:
        return {"error": error}
    observation = await client.call_tool(name, args)
    state.observations.append({"tool": name, "input": args, "output": observation})
    if name == ELIGIBILITY_TOOL and "status" in observation:
        state.eligibility_results.append(observation)
    if name == FLAG_TOOL and observation.get("flagged"):
        state.ticket_ids.append(observation["ticket_id"])
    return observation


async def give_up(request: str, employee_id: str | None, client: MCPClient) -> dict[str, Any]:
    """After MAX_STEPS, escalate to a human with a real ticket (R-51)."""
    print(f"\nNo valid final answer after {MAX_STEPS} steps; escalating.")
    flagged = await client.call_tool(
        FLAG_TOOL,
        {"employee_id": employee_id or "UNKNOWN", "request": request, "reason": NO_DECISION_REASON},
    )
    final = {
        "decision": "escalate",
        "response_to_employee": (
            "We couldn't complete an automatic decision on your request, so it has been "
            "passed to the IT team for review."
        ),
        "policy_basis": f"Agent stopped after {MAX_STEPS} steps without a valid decision.",
        "escalation_reason": NO_DECISION_REASON,
        "ticket_id": flagged.get("ticket_id"),
    }
    print_final(final)
    return final


def print_final(final: dict[str, Any]) -> None:
    """Print the final answer block."""
    print("\n===== FINAL ANSWER =====")
    print(json.dumps(final, indent=2))


# ---------- OpenAI wiring and CLI ----------


def make_openai_llm(model: str) -> LLM:
    """Return an LLM function backed by OpenAI Chat Completions in JSON mode."""
    from openai import OpenAI

    client = OpenAI()  # reads OPENAI_API_KEY from the environment

    def complete(messages: list[dict[str, str]]) -> str:
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            response_format={"type": "json_object"},
            temperature=0,
        )
        return response.choices[0].message.content or ""

    return complete


# ---------- reflection demo ----------

DEMO_REQUEST = (
    "Employee E1001: My laptop is over three years old and the battery dies by lunch. "
    "Can I get a new one?"
)

# DELIBERATELY FLAWED, hand-written draft (not produced by the agent). The decision is right,
# but the reply promises an item, specs and a delivery time that no tool returned.
DEMO_FLAWED_DRAFT = {
    "decision": "approve",
    "response_to_employee": (
        "Great news! Your request is approved. We'll send you a 32-inch 4K monitor "
        "delivered by Friday."
    ),
    "policy_basis": (
        "R-31 APPROVE_REFRESH_DUE: Engineer employees may refresh a laptop every 36 months."
    ),
    "escalation_reason": None,
    "ticket_id": None,
}


async def demo_reflection(client: MCPClient, llm: LLM) -> dict[str, Any]:
    """Run reflection on DEMO_FLAWED_DRAFT, using real observations for demo case 1."""
    print("#" * 72)
    print("# DEMO: reflection on a DELIBERATELY FLAWED draft (hand-written, NOT agent output)")
    print("#" * 72)
    print(f"Request: {DEMO_REQUEST}")
    employee_id, reason_text = parse_request(DEMO_REQUEST)
    accommodation = detect_accommodation_need(reason_text)
    print(f"Accommodation check (R-41): {json.dumps(accommodation)}")

    # Real tool calls through MCP, so reflection checks the draft against true observations.
    observations = []
    for name, args in [
        ("get_employee_info", {"employee_id": employee_id}),
        (ELIGIBILITY_TOOL, {"employee_id": employee_id, "item": "laptop"}),
    ]:
        output = await client.call_tool(name, args)
        observations.append({"tool": name, "input": args, "output": output})
        print(f"\nObservation from {name} {json.dumps(args)}:")
        print(json.dumps(output, indent=2))

    print("\nFlawed draft (DEMO):")
    print(json.dumps(DEMO_FLAWED_DRAFT, indent=2))
    result = reflect(
        llm, DEMO_REQUEST, DEMO_FLAWED_DRAFT, observations, accommodation["needs_accommodation"]
    )
    print_reflection(result)
    print_final(result.final)
    return result.final


# ---------- entry points ----------


async def main(request: str | None, demo: bool = False) -> dict[str, Any]:
    """Load settings, print the evaluation date first, connect to MCP and run."""
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    print(f"Evaluation date: {evaluation_date_label()}")
    if not os.environ.get("OPENAI_API_KEY"):
        sys.exit("OPENAI_API_KEY is not set (add it to .env).")
    model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
    print(f"Model: {model}")
    async with MCPClient() as client:
        llm = make_openai_llm(model)
        if demo:
            return await demo_reflection(client, llm)
        return await run_agent(request, client, llm)


def cli() -> None:
    """Parse the command line and run one request, or the reflection demo."""
    parser = argparse.ArgumentParser(description="IT equipment request ReAct agent")
    parser.add_argument("request", nargs="?", help='e.g. "Employee E1001: My laptop is too old."')
    parser.add_argument(
        "--demo-reflection",
        action="store_true",
        help="run reflection on a deliberately flawed demo draft for demo case 1",
    )
    args = parser.parse_args()
    if not args.demo_reflection and not args.request:
        parser.error("give a request, or use --demo-reflection")
    # Line-buffer stdout so the trace interleaves correctly with the server's stderr log,
    # and the evaluation date is always the first line, even when output is piped.
    sys.stdout.reconfigure(line_buffering=True)
    asyncio.run(main(args.request, demo=args.demo_reflection))


if __name__ == "__main__":
    cli()
