import asyncio
import threading

import pytest
from sqlmodel import Session, create_engine

from brain.db import Database
from brain.events import EventHub
from core import main


@pytest.mark.parametrize("language", ["en", "hi"])
@pytest.mark.parametrize(
    ("scenario", "amount"),
    [
        ("failed_payment", 1250),
        ("pending_payment", 2000),
        ("duplicate_small", 300),
        ("duplicate_large", 1500),
        ("suspicious_refund", 5000),
    ],
)
def test_scenario_handoff_sends_real_customer_message_to_concierge(
    monkeypatch, tmp_path, scenario, amount, language
):
    core_engine = create_engine(
        f"sqlite:///{tmp_path / 'core.sqlite3'}",
        connect_args={"check_same_thread": False},
    )
    monkeypatch.setattr(main, "engine", core_engine)
    main.SQLModel.metadata.create_all(core_engine)
    with Session(core_engine) as session:
        main.seed_customers(session)

    database = Database(f"sqlite:///{tmp_path / 'brain.sqlite3'}")
    monkeypatch.setattr(main.brain_runtime, "_database", database)
    monkeypatch.setattr(main.brain_runtime, "_events", EventHub(database))
    completed = threading.Event()
    observed: dict[str, object] = {}

    def mocked_run_case(case_id):
        try:
            case = database.get_case(case_id)
            assert case is not None
            observed["case"] = case["data"]
            observed["concierge"] = case["data"]["precomputed_concierge"]
        except Exception as exc:
            observed["error"] = exc
        finally:
            completed.set()

    monkeypatch.setattr(main.brain_runtime, "run_case", mocked_run_case)

    async def create_scenario():
        response = await main.set_scenario(
            main.ScenarioRequest(
                scenario=scenario,
                customer_ref="C1",
                language=language,
            )
        )
        assert await asyncio.to_thread(completed.wait, 5)
        return response

    response = asyncio.run(create_scenario())
    assert "error" not in observed
    case = observed["case"]
    text = case["text"]
    txn_id = response["created"][-1]
    assert case["txn_id"] == txn_id
    assert case["customer_id"] == "C1"
    assert case["language"] == language
    assert "Scenario:" not in text
    assert txn_id in text
    assert str(amount) in text
    concierge = observed["concierge"]
    assert concierge["transaction_id"] == txn_id
    assert concierge["summary"] == text
    assert concierge["language"] == language
    assert concierge["intent"] == {
        "failed_payment": "failed_payment",
        "pending_payment": "pending_payment",
        "duplicate_small": "duplicate_charge",
        "duplicate_large": "duplicate_charge",
        "suspicious_refund": "refund_request",
    }[scenario]
    if scenario in {"duplicate_small", "duplicate_large"}:
        assert response["created"][0] in text
    if language == "hi":
        assert any("\u0900" <= character <= "\u097f" for character in text)
