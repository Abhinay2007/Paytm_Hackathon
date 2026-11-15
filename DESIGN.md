# Paytm Hackathon — System Design

## 1. Project Architecture

The project uses a two-person Brain/Body architecture.

- Person A (Brain): thinking, agents, orchestration, policy, persistence and event streaming.
- Person B (Body): external integrations, Paytm simulator, payment page, Telegram and dashboard.

The two halves communicate through the shared integration contract in `CONTRACT.md`.

## 2. Folder Structure

```text
Paytm_Hackathon/
├── platform/                 # Person A, except tools.py and channels/
│   ├── __init__.py
│   ├── llm.py
│   ├── db.py
│   ├── events.py
│   ├── policy.py
│   ├── supervisor.py
│   ├── workers.py
│   ├── metrics.py
│   ├── demo_cli.py
│   ├── tools_fake.py
│   ├── agents/
│   │   ├── __init__.py
│   │   ├── base.py
│   │   ├── concierge.py
│   │   ├── investigator.py
│   │   ├── resolver.py
│   │   └── comms.py
│   ├── channels/             # Person B
│   └── tests/
├── core/                     # Person B
├── dashboard/                # Person B
├── AGENTS.md
├── CONTRACT.md
├── DESIGN.md
├── README.md
├── requirements.txt
├── .env.example
└── .gitignore
```

Person A must not create or modify `platform/tools.py` or `platform/channels/`. These belong to Person B.

## 3. Ownership

### Person A — Brain

Owns:
- Database and persistence.
- SSE events and streaming.
- Sarvam LLM wrapper.
- Agent framework.
- Concierge, Investigator, Resolver and Comms agents.
- Guardian deterministic policy.
- Supervisor and orchestration.
- Watcher and timers.
- Metrics.
- P1–P8 and P10 endpoints.
- Fake tools and demo CLI.

### Person B — Body

Owns:
- Core payment simulator.
- Admin scenario seeding.
- Payment page and webhook.
- Refund operations.
- `platform/tools.py`.
- `platform/channels/`.
- Telegram customer and human desk bot.
- React dashboard.
- C1–C10 endpoints.
- `check.py` health and integration smoke test.

P9 is replaced by the small `check.py` script.

## 4. Shared Integration Contract

`CONTRACT.md` is the source of truth for the shared JSON structures.

It defines:
- Transaction.
- Bank status.
- Payment link.
- Refund.
- Payment webhook.
- SSE event.

Do not independently change shared data structures or function signatures.

### Brain interfaces

```python
handle_inbound(chat_id, text) -> case_id
decide_case(case_id, approved, by)
```

Brain also exposes a payment webhook resume function.

### Body interfaces

```python
send_message(chat_id, text)
send_desk_brief(case_id, text)
mark_desk_decision(case_id, text)
```

Any change to these interfaces requires agreement from both teammates.

## 5. Brain Components

### 5.1 LLM wrapper

File: `platform/llm.py`

- Sarvam is accessed only through this module.
- Expose `chat(system, user) -> str`.
- Read credentials and model configuration from environment variables.
- Handle HTTP errors, authentication errors and timeouts.
- Keep external API calls out of agent modules.

### 5.2 Database

File: `platform/db.py`

- SQLite persistence.
- Store cases and events.
- Provide case creation, update, retrieval and listing operations.
- Support case history.

Exact database schema and additional fields must be agreed and documented before implementation.

### 5.3 Events and SSE

File: `platform/events.py`

- Persist events.
- Publish events to SSE subscribers.
- Support multiple subscribers.
- Provide case event history.
- Handle disconnected subscribers.

Use the SSE event structure defined in `CONTRACT.md`.

### 5.4 Agent framework

File: `platform/agents/base.py`

- Run agents using structured Pydantic output.
- Validate agent responses.
- Retry invalid output with validation feedback.
- Record agent events.
- Handle LLM errors and timeouts.

### 5.5 Agents

**Concierge**
- Understand incoming customer messages.
- Identify the issue and relevant transaction.
- Support English and Hindi.

**Investigator**
- Inspect transaction and bank information.
- Identify root cause.
- Determine retryability and possible duplicate charges.
- Never assume that a failed transaction means funds were not deducted.

**Resolver**
- Propose the appropriate resolution.
- Possible actions include sending a payment link, refund, waiting or escalation.
- Must not independently execute financial operations.

**Comms**
- Generate short, empathetic customer messages.
- Support English and Hindi.
- Never claim an unconfirmed refund or payment success.

**Guardian**
- Apply deterministic Python policy rules.
- Refunds and suspected duplicate charges require human approval.
- Refunds above INR 5000 cannot be auto-approved.
- Verified retryable failures can qualify for payment-link resolution.
- Pending transactions wait.
- Uncertain or conflicting information escalates.

## 6. Supervisor and Workflow

File: `platform/supervisor.py`

Responsibilities:
- `handle_inbound`.
- `run_case`.
- Payment webhook resume.
- `decide_case`.
- Coordinate agents and Guardian.
- Persist case state.
- Publish events.

Use normal Python control flow for orchestration.

Do not change the agreed Brain/Body interfaces.

### Main workflows

**Failed payment**
1. Watcher detects a failed transaction.
2. Investigator checks transaction and bank status.
3. Guardian evaluates the proposed action.
4. Resolver proposes sending a payment link.
5. Comms prepares the customer message.
6. Body sends the Telegram message.
7. Customer pays through the core payment page.
8. Core sends the payment webhook.
9. Brain resumes the case.
10. Case is resolved and events are published.

**Duplicate charge**
1. Investigator identifies a possible duplicate.
2. Guardian requires human approval.
3. Brain creates a desk brief.
4. Body sends the brief to the human desk.
5. Human approves or rejects.
6. Brain processes the decision through `decide_case`.
7. Case state and SSE events are updated.

**Pending payment**
1. Investigator checks bank status.
2. Resolver proposes waiting.
3. Watcher/timer checks again.
4. The case resumes when updated status is available.

## 7. Watcher

File: `platform/workers.py`

- Periodically inspect failed transactions through the agreed tools interface.
- Avoid creating duplicate cases for the same transaction.
- Start proactive case handling.
- Support a configurable interval.
- Stop cleanly.

The pending-payment timer flow may be cut if the team is behind schedule, as specified in the original plan.

## 8. Fake Tools

File: `platform/tools_fake.py`

Person A uses fake tools while Person B develops the real core simulator and wrappers.

The fake tools must return the contract-compatible structures.

Required demonstration scenarios:
- Failed bank timeout.
- Duplicate charge.
- Pending transaction that later settles.

At integration time, replace fake tools with Person B's `platform/tools.py` through the agreed integration approach.

## 9. Body Components

### Core simulator

Person B owns `core/`.

Responsibilities:
- Payment simulation.
- Transaction and bank status.
- Admin scenario seeding.
- Payment page.
- Webhook.
- Refund handling.

### Telegram

Person B owns `platform/channels/`.

Responsibilities:
- Customer messages.
- Human desk messages.
- Telegram polling and commands.
- Approval and rejection interaction.
- Calls into the agreed Brain interfaces.

### Dashboard

Person B owns `dashboard/`.

Responsibilities:
- Live SSE agent feed.
- Case detail and history.
- Human approval queue.
- KPI cards.
- Phone simulator.
- Reset control.

Use fixture events initially and switch to live SSE when P7 is ready.

## 10. API Endpoint Ownership

### Person A

Owns P1–P8 and P10.

### Person B

Owns C1–C10.

P9 is replaced by `check.py`.

**OPEN QUESTION:** Exact endpoint paths, HTTP methods, request schemas, response schemas and status codes must be agreed by the team. Do not invent these details. Record the final agreed specifications here before implementing endpoints.

## 11. Security and Reliability

- Store secrets in environment variables.
- Do not commit `.env`.
- Verify payment webhooks using the agreed `X-Core-Secret` header.
- Validate structured agent outputs.
- Handle HTTP errors and timeouts.
- Keep financial operations behind the agreed tools boundary.
- Require human approval where Guardian rules demand it.
- Never silently swallow exceptions.
- Use fake tools for offline tests.

## 12. Technology

- Python 3.12.
- FastAPI.
- SQLite.
- Pydantic v2.
- httpx.
- pytest.
- python-dotenv.
- Sarvam API.
- Telegram Bot API.
- React dashboard.

Avoid unnecessary heavy frameworks.

## 13. Timeline and Sync Checkpoints

### 0:00–0:15 — Together
- Create repository and agree on layout.
- Person A tests Sarvam.
- Person B tests Telegram echo.
- Agree on `CONTRACT.md` and shared interfaces.

### 0:15–0:40
Person A:
- Database.
- Events and SSE.
- LLM wrapper.
- Agent base.
- Read-only P3 and P4.

Person B:
- Core simulator.
- C1–C5.
- Payment page.
- Webhook and refunds.

### 0:40–1:05
Person A:
- Concierge.
- Investigator.
- Resolver.
- Comms.
- Guardian policy.
- Fake tools.
- Demo CLI.

Person B:
- Tool wrappers.
- Telegram customer and desk bot.

### 1:05–1:30
Person A:
- Supervisor.
- Webhook and approval resume paths.
- Watcher.
- P1, P2, P5, P6, P8, P10.
- Metrics.

Person B:
- Dashboard and live event feed.

### 1:30–1:45
- Integrate real tools.
- Fix integration bugs.
- Tune English and Hindi prompts.

### 1:40–1:50
Person B:
- `check.py` integration smoke test.

### 1:50
- Code freeze.
- Record backup video.
- Rehearse once.

## 14. Sync Checkpoints

**0:45**
- Brain demo CLI prints a full agent trace.
- Core endpoints work through curl.
- Payment page opens on a phone.

**1:15**
- Failed-payment flow works end to end.
- Watcher detects failure.
- Telegram sends the payment link.
- Customer payment resolves the case.

**1:35**
- Duplicate-charge flow works.
- Human approval works.
- Dashboard shows live events.

**1:50**
- Code freeze and backup video.

## 15. Blocker Rule

If blocked on the other person's component for more than 10 minutes:
- Person A uses fake tools.
- Person B uses fixture events.
- Both continue independently.

Commit small and often. Modify only owned files.

## 16. Feature Cut Order

If behind schedule, cut in this order:

1. Telegram voice notes.
2. Pending-payment and timer flow.
3. Dashboard charts, retaining KPI numbers.
4. Guardian LLM review; retain deterministic rules.
5. P8 watcher scan endpoint; retain the Watcher loop.
6. Telegram approval buttons; retain dashboard approval.

Never cut:
- Watcher end-to-end flow.
- Human approval.
- Live event feed.

If the 1:15 checkpoint is not green, stop polish and fix the failed-payment flow first.
