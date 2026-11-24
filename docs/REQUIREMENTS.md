# IT Equipment Request Handler: Requirements

## 1. What the system does

An employee asks for IT equipment in plain English. An AI agent looks up the employee and the
policy, then gives one of three decisions:

- **Approve**: the policy clearly allows it.
- **Deny**: the policy clearly forbids it. The agent cites the rule.
- **Escalate**: anything uncertain. The agent opens a human-review ticket instead of guessing.

The agent gets its information from an MCP server with four tools (section 5). The yes/no
logic lives in code, not in the AI.

## 2. What a request needs

| Field | Example | Notes |
|---|---|---|
| `employee_id` | `E1001` | Trimmed and uppercased |
| `item` | `"screen"` | One item per request. Normalized to a known name (see below). |
| `reason` | `"Battery dies by lunch"` | Free text. Checked for accommodation needs (R-40). |
| `submitted_date` | `2026-11-24` | The evaluation date: `as_of` in tests, otherwise `REFERENCE_DATE` from env, otherwise today |

**Item names** are lowercased, trimmed and mapped through synonyms:

| You type | It means |
|---|---|
| laptops, notebook | laptop |
| monitors, screen(s), display(s) | monitor |
| keyboards | keyboard |
| mice | mouse |
| headsets, headphone(s) | headset |

## 3. Policy rules

**Refresh cycle** is the minimum number of months between two issues of the same item.

| Item | standard | engineer | manager | contractor |
|---|---|---|---|---|
| laptop | 48 | 36 | 24 | **not allowed** (laptops come from their agency) |
| monitor | 36 | 36 | 36 | 36 |
| keyboard | 24 | 24 | 24 | 36 |
| mouse | 24 | 24 | 24 | 36 |
| headset | 24 | 24 | 18 | 24 |

**How dates work**
- **Last issue date** is the most recent `issued_date` for that item. Each physical unit is a
  separate record.
- **Next eligible date** is the last issue date plus the refresh cycle. The day is clamped to
  the end of the month, so `2024-01-31` plus 1 month is `2024-02-29`.
- **Days until eligible** is the next eligible date minus the evaluation date.

**Decision rules**, checked top to bottom. The first match wins.

| # | ID | If… | Then | `reason_code` |
|---|---|---|---|---|
| 1 | R-10 | Employee ID or item is empty, or the date is invalid | escalate | `ESCALATE_INVALID_INPUT` |
| 2 | R-11 | Employee not found | escalate | `ESCALATE_EMPLOYEE_NOT_FOUND` |
| 3 | R-12 | Role has no policy | escalate | `ESCALATE_NO_POLICY_FOR_ROLE` |
| 4 | R-13 | Item is not in the policy table | escalate | `ESCALATE_UNKNOWN_ITEM` |
| 5 | R-20 | Item is not allowed for the role | **deny** | `DENY_NOT_ALLOWED_FOR_ROLE` |
| 6 | R-30 | Never had this item | **approve** | `APPROVE_NEVER_ISSUED` |
| 7 | R-21 | More than 60 days until eligible | **deny** | `DENY_TOO_EARLY` |
| 8 | R-15 | 1 to 60 days until eligible | escalate | `ESCALATE_BORDERLINE` |
| 9 | R-31 | 0 or fewer days until eligible | **approve** | `APPROVE_REFRESH_DUE` |

**The principle:** explicit prohibitions are checked first and denied; uncertainty is
escalated. Rows 1 to 4 come first because a rule can't be applied to an unknown employee,
role or item. Rows 7 to 9 only run when there is an issue date.

## 4. Edge cases and ambiguity

**Deny vs. escalate:** clear rule violations are denied; uncertainty is escalated.

| Situation | Decision | Why |
|---|---|---|
| Exactly 61 days until eligible | deny | Clearly too early |
| Exactly 60 days until eligible | escalate | Close enough that a person might make an exception |
| Exactly 0 days until eligible | approve | Eligible today |
| Contractor asks for a laptop they've never had | deny | The prohibition is checked before "never had it" |
| Several units of one item (e.g. two monitors) | uses the newest date | Each unit is a record. The most recent `issued_date` wins. |
| Unknown employee, role or item | escalate | Policy can't be applied |
| Reason mentions a medical, accessibility or safety need | **always escalate** | See R-40 below |

**R-40 Accommodation override.** If the reason cites a medical, accessibility or safety need,
the decision is **escalate** with `reason_code` `ESCALATE_ACCOMMODATION`. This applies whatever
the tool said, even approve. A person decides accommodations.

- **R-41** Detection is a plain function, `detect_accommodation_need(reason)`, in
  `agent/accommodation.py`. It returns `{"needs_accommodation": bool, "matched_terms": [...]}`.
  It matches whole words, ignoring case, from this list:
  `medical, doctor, physician, prescription, diagnosis, diagnosed, disability, disabled,
  accessibility, accessible, accommodation, ergonomic, injury, injured, pain, rsi, carpal,
  tendonitis, vision, hearing, deaf, blind, safety, hazard, unsafe`.
  A false match only causes an extra human review, which is the safe direction.
- **R-42** The AI may also apply R-40 when the reason clearly states a need that the keyword
  list misses. It must quote that text. The item name alone ("standing desk") doesn't count.

## 5. Tool contract

All tools return JSON and **never raise errors**. Bad input returns `found: false` (or
`flagged: false`) with an `error` message. A value that doesn't apply is `null`.

**`get_employee_info(employee_id)`**
```json
{"found": true, "employee_id": "E1001", "name": "Alex Rivera", "role": "engineer",
 "start_date": "2021-03-01", "tenure_months": 68,
 "equipment": [{"item": "laptop", "issued_date": "2023-06-15"},
               {"item": "monitor", "issued_date": "2024-01-10"},
               {"item": "monitor", "issued_date": "2024-05-20"}]}
```
Not found: `{"found": false, "employee_id": "E9999", "error": "Employee E9999 not found"}`

**`get_policy_limits(role)`**
```json
{"found": true, "role": "contractor",
 "items": {"laptop":  {"allowed": false, "refresh_months": null,
                       "note": "Contractors receive laptops from their agency only."},
           "monitor": {"allowed": true, "refresh_months": 36, "note": null}}}
```
The example is shortened. All five items are always listed. Not found:
`{"found": false, "role": "intern", "error": "No policy on file for role 'intern'"}`

**`check_request_eligibility(employee_id, item)`** (every key is always present)
```json
{"status": "deny", "reason_code": "DENY_TOO_EARLY",
 "message": "Standard employees may refresh a laptop every 48 months. Last issued 2025-02-01; next eligible 2029-02-01.",
 "employee_id": "E1002", "role": "standard", "requested_item": "Laptop ", "item": "laptop",
 "allowed": true, "refresh_months": 48, "last_issued_date": "2025-02-01",
 "next_eligible_date": "2029-02-01", "days_until_eligible": 800, "as_of": "2026-11-24"}
```

**`flag_for_human_review(employee_id, request, reason)`** adds one JSON line to
`ESCALATIONS_FILE` (default `escalations.jsonl`).
```json
{"flagged": true, "ticket_id": "ESC-0001", "employee_id": "E1003", "request": "New laptop",
 "reason": "Borderline: next eligible 2026-12-20, 26 days away.",
 "status": "pending_review", "created_at": "2026-11-24T10:15:00Z"}
```
- Ticket numbers count up: `ESC-0001`, `ESC-0002`, and so on.
- Unknown employee IDs are still accepted, so they can be escalated.
- An empty `request` or `reason` returns `{"flagged": false, "error": "reason is required"}`,
  and nothing is written.

**Dates in tests vs. live:** the plain functions `get_employee_info` and
`check_request_eligibility` accept an optional `as_of` for tests. The MCP tools don't expose
`as_of`. They use `REFERENCE_DATE` for both tenure and eligibility.

## 6. Agent behaviour

- Uses a ReAct loop: every step prints `Thought:`, `Action:` and `Observation:`, for at most
  8 steps. The first line of every trace prints the evaluation date.
- **R-43** Must call `check_request_eligibility` before giving a final answer.
- **R-45** Passes the core item noun to tools: `"keyboard"`, not `"ergonomic keyboard"`. If
  unsure, it passes the item as written, since an unknown item escalates anyway.
  Normalization matches whole names only, never substrings, so `"laptop stand"` is not a
  laptop. It is an unknown item.
- Follows the tool's `status`, except when R-40 applies.
- To escalate, it must call `flag_for_human_review` and include the `ticket_id`.
- Final answer fields: `decision`, `response_to_employee`, `policy_basis`, and when
  escalating, `escalation_reason` and `ticket_id`.
- **R-50** A deny cites the rule. A too-early deny also gives the next eligible date.
- **R-51** An escalation has a ticket ID and a plain-language reason.
- **R-52** The reply only states dates and items that came from tool results.
- **R-44 Reflection** checks every draft before it is final. The decision must match the
  tool's `status`, unless R-40 applies, in which case escalate is the only valid decision.
  Reflection must **never** turn an R-40 escalation back into approve or deny.

## 7. Acceptance and tests

### Demo requests (`REFERENCE_DATE=2026-11-24`)

Each demo request is sent to the agent as `"Employee <ID>: <request text>"`, for example
`"Employee E1002: I'd like a new laptop, mine feels a bit slow."`

| # | Employee | Request | Key facts | Expected |
|---|---|---|---|---|
| 1 | E1001 engineer | "My laptop is over three years old and the battery dies by lunch. Can I get a new one?" | Laptop issued `2023-06-15`, eligible since `2026-06-15` | **approve** `APPROVE_REFRESH_DUE` |
| 2 | E1002 standard | "I'd like a new laptop, mine feels a bit slow." | Laptop issued `2025-02-01`, eligible `2029-02-01` (800 days away) | **deny** `DENY_TOO_EARLY` |
| 3 | E1003 manager | "Can I get a new laptop? Mine is almost two years old." | Laptop issued `2024-12-20`, eligible `2026-12-20` (26 days away) | **escalate** `ESCALATE_BORDERLINE` |
| 4 | E1004 contractor | "Could I get a standing desk for my workstation?" | Not a covered item | **escalate** `ESCALATE_UNKNOWN_ITEM` |
| Bonus | E1002 standard | "My doctor diagnosed RSI and recommended an ergonomic keyboard. Can I get one?" | Keyboard refresh is due, so the tool says approve | **escalate** `ESCALATE_ACCOMMODATION` (R-40 overrides the tool) |

None of the reasons in cases 1 to 4 contains an R-41 keyword.

### Mock employees

Each equipment record is one physical unit: `{item, issued_date}`.

| ID | Role | Start | Equipment records, in file order |
|---|---|---|---|
| E1001 | engineer | 2021-03-01 | laptop `2023-06-15`, monitor `2024-01-10`, monitor `2024-05-20` |
| E1002 | standard | 2024-11-04 | laptop `2025-02-01`, keyboard `2023-03-01` |
| E1003 | manager | 2019-08-12 | laptop `2024-12-20`, headset `2025-09-01`, laptop `2022-11-15` |
| E1004 | contractor | 2025-06-02 | monitor `2025-06-02` |
| E1005 | intern (no policy) | 2026-09-01 | none |
| E9999 | does not exist | | |

E1003's old laptop is deliberately listed **last**. If the code used it, the result would be
approve. The correct, newest date gives borderline.

### Unit tests

Every rule (R-10 to R-31, R-41) has at least one unit test in tests/. Each test's docstring names the rule ID it covers. Tests run with pytest -v in CI.
