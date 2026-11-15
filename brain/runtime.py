"""Synchronous adapter used by Core and Mitra."""

from typing import Any

from brain.db import Database
from brain.events import EventHub
from brain.scenarios import build_scenario, default_scenario_amounts
from brain import supervisor


_database = Database()
_events = EventHub(_database)


def create_case(
    customer_id: str,
    txn_id: str,
    text: str,
    language: str | None,
    chat_id: str | int | None = None,
) -> str:
    return supervisor.create_case(
        customer_id,
        txn_id,
        text,
        language,
        chat_id if chat_id is not None else customer_id,
        _database,
    )


def run_case(case_id: str) -> dict[str, Any]:
    return supervisor.run_case(case_id, _database, _event_hub())


def decide_case(case_id: str, approved: bool, by: str) -> dict[str, Any]:
    return supervisor.decide_case(case_id, approved, by, _database, _event_hub())


def start_scenario(
    customer_id: str,
    scenario: str,
    created_txn_ids: list[str],
    language: str | None,
) -> dict[str, Any]:
    amounts = default_scenario_amounts(scenario, len(created_txn_ids))
    built = build_scenario(scenario, language or "en", created_txn_ids, amounts)
    case_id = supervisor.create_case(
        customer_id,
        built["txn_id"],
        built["text"],
        built["language"],
        customer_id,
        _database,
        data={
            "scenario": built["scenario"],
            "precomputed_concierge": built["precomputed_concierge"],
        },
    )
    return run_case(case_id)


def _event_hub() -> EventHub:
    global _events
    if _events.database is not _database:
        _events = EventHub(_database)
    return _events
