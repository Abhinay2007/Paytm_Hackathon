"""Telegram-to-Brain adapter; all case decisions use the Brain runtime."""

import asyncio
from brain import runtime
from mitra import tools


async def handle_inbound(chat_id: int | str, text: str, language_hint: str | None = None) -> str:
    # Telegram customers are created by the existing /start Core scenario.
    customer_id = str(chat_id)
    history = await tools.history(customer_id)
    transactions = history.get("transactions", [])
    txn_id = str(transactions[0]["id"]) if transactions else ""
    language = language_hint or str(history.get("language") or "en")
    case_id = runtime.create_case(customer_id, txn_id, text, language, chat_id=customer_id)
    await asyncio.to_thread(runtime.run_case, case_id)
    return case_id


async def decide_case(case_id: str, approved: bool, by: str) -> None:
    await asyncio.to_thread(runtime.decide_case, case_id, approved, by)
