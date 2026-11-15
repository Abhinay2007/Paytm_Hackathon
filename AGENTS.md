# Paytm Hackathon - Coding Instructions

## My role
I am Person A (Brain). I own the `platform/` directory except `platform/tools.py` and `platform/channels/`, which belong to Person B.

## Ownership restrictions
- Person A owns `platform/` except `platform/tools.py` and `platform/channels/`.
- Person B owns `core/`, `platform/tools.py`, `platform/channels/`, and `dashboard/`.
- Person A may modify root-level documentation.
- Never modify Person B's files without informing them and obtaining agreement.
- Do not change shared function signatures without team agreement.

## Technology stack
- Python 3.12
- FastAPI
- SQLite
- Pydantic v2
- httpx
- pytest
- python-dotenv

Avoid unnecessary heavy frameworks.

## Architecture
- Sarvam is accessed only through `platform/llm.py`.
- `chat(system, user) -> str` is the LLM interface.
- Agents use Pydantic models for structured output.
- Supervisor uses normal Python if/else logic.
- Guardian uses deterministic Python rules.
- Events are stored in SQLite and published to SSE queues.
- External payment operations go through `tools_fake.py` initially.
- Follow the agreed Brain/Body architecture and integration contract.

## Shared interfaces

Person A exposes:
- `handle_inbound(chat_id, text) -> case_id`
- `decide_case(case_id, approved, by)`
- A payment webhook resume function.

Person B exposes:
- `send_message(chat_id, text)`
- `send_desk_brief(case_id, text)`
- `mark_desk_decision(case_id, text)`

Do not change these signatures without agreement from both people.

## Coding rules
1. Read DESIGN.md and CONTRACT.md before implementation.
2. Keep modules small and maintainable.
3. Use type hints.
4. Handle API errors and timeouts.
5. Never hardcode API keys or secrets.
6. Load configuration from environment variables.
7. Do not invent API endpoints or contract fields.
8. Keep the Brain/Body interface stable.
9. Add tests for every major feature.
10. Never silently swallow exceptions.
11. Do not modify another person's owned files.
12. Use fake tools when the other person's integration is unavailable.

## Testing
- Use pytest.
- Test offline functionality with fake tools.
- Do not require real payment transactions for tests.
- Give a test command after every implementation task.

## Git
- Work on `feat/brain-system`.
- Commit after each working phase.
- Never push directly to main.
- Commit small and often.
