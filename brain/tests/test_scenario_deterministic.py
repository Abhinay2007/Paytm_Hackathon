import pytest

from brain import llm, supervisor
from brain.db import Database
from brain.events import EventHub
from brain.scenarios import build_scenario
from mitra import tools


@pytest.mark.parametrize(
    ("scenario", "facts", "expected"),
    [
        (
            "failed_payment",
            {"status": "FAILED", "retryable": True, "links_issued": 0},
            "create_link",
        ),
        (
            "failed_payment",
            {"status": "FAILED", "retryable": False, "links_issued": 0},
            "escalate",
        ),
        (
            "failed_payment",
            {"status": "FAILED", "retryable": True, "links_issued": 1},
            "escalate",
        ),
        ("pending_settlement", {"bank_state": "SETTLING"}, "wait"),
        (
            "small_duplicate",
            {
                "txn_id": "TXN2",
                "duplicate_pairs": [{"txn_id": "TXN2", "duplicate_of": "TXN1"}],
                "refund_count": 0,
            },
            "create_refund",
        ),
        (
            "large_duplicate",
            {
                "txn_id": "TXN2",
                "duplicate_pairs": [{"txn_id": "TXN2", "duplicate_of": "TXN1"}],
                "refund_count": 1,
            },
            "escalate",
        ),
    ],
)
def test_scenario_action_is_selected_from_verified_facts(scenario, facts, expected):
    action, _ = supervisor._scenario_action(scenario, facts)

    assert action == expected


def test_suspicious_scenario_is_blocked_without_any_sarvam_call(
    monkeypatch, tmp_path
):
    database = Database(tmp_path / "brain.sqlite3")
    events = EventHub(database)
    built = build_scenario("suspicious_refund", "hi", ["TXN7"], [5000])
    case_id = supervisor.create_case(
        "C1",
        built["txn_id"],
        built["text"],
        built["language"],
        "web-case",
        database,
        data={
            "scenario": built["scenario"],
            "precomputed_concierge": built["precomputed_concierge"],
        },
    )

    async def mocked_tool(agent, name, verdict=None, **kwargs):
        if name == "history":
            return {"transactions": [], "duplicate_pairs": [], "refund_count": 0}
        if name == "get_txn":
            return {
                "id": "TXN7",
                "customer_id": "C1",
                "status": "SUCCESS",
                "amount": 5000,
                "duplicate_of": None,
                "links_issued": 0,
                "recovered_by": None,
            }
        if name == "bank_status":
            return {"state": "SUCCESS", "code": "OK", "retryable": False, "eta_s": 0}
        raise AssertionError(f"Unexpected tool call: {name}")

    def sarvam_must_not_be_called(*args, **kwargs):
        raise AssertionError("Suspicious scenario must not call Sarvam")

    monkeypatch.setattr(tools, "call_tool", mocked_tool)
    monkeypatch.setattr(llm, "chat", sarvam_must_not_be_called)

    result = supervisor.run_case(case_id, database, events)
    case_events = database.list_events(case_id=case_id, limit=100)
    guardian_step = next(
        event
        for event in case_events
        if event["type"] == "step" and event["agent"] == "guardian"
    )
    assistant_message = next(
        event
        for event in case_events
        if event["type"] == "message" and event["direction"] == "assistant"
    )

    assert result["status"] == "escalated"
    assert guardian_step["summary"].startswith("Guardian blocked: ")
    assert "एक व्यक्ति आपसे आगे संपर्क करेगा" in assistant_message["text"]


def _create_scenario_case(database, scenario, txn_ids, amounts):
    built = build_scenario(scenario, "en", txn_ids, amounts)
    case_id = supervisor.create_case(
        "C1",
        built["txn_id"],
        built["text"],
        built["language"],
        "web-case",
        database,
        data={
            "scenario": built["scenario"],
            "precomputed_concierge": built["precomputed_concierge"],
        },
    )
    return case_id, built


def _mock_scenario_tools(monkeypatch, scenario, txn_id, duplicate_of=None):
    state = {"links_issued": 0, "refund_created": False}
    amount = {
        "failed_payment": 1250,
        "pending_settlement": 2000,
        "small_duplicate": 300,
        "large_duplicate": 1500,
    }[scenario]

    async def mocked_tool(agent, name, verdict=None, **kwargs):
        if name == "history":
            pairs = (
                [{"txn_id": txn_id, "duplicate_of": duplicate_of}]
                if duplicate_of
                else []
            )
            return {
                "transactions": [],
                "duplicate_pairs": pairs,
                "refund_count": 0,
            }
        if name == "get_txn":
            return {
                "id": txn_id,
                "customer_id": "C1",
                "status": (
                    "FAILED"
                    if scenario == "failed_payment"
                    else "PENDING"
                    if scenario == "pending_settlement"
                    else "SUCCESS"
                ),
                "amount": amount,
                "duplicate_of": duplicate_of,
                "links_issued": state["links_issued"],
                "recovered_by": None,
            }
        if name == "bank_status":
            if scenario == "failed_payment":
                return {
                    "state": "FAILED",
                    "code": "BANK_TIMEOUT",
                    "retryable": True,
                    "eta_s": 0,
                }
            if scenario == "pending_settlement":
                return {
                    "state": "SETTLING",
                    "code": "SETTLEMENT_IN_PROGRESS",
                    "retryable": False,
                    "eta_s": 30,
                }
            return {
                "state": "SUCCESS",
                "code": "OK",
                "retryable": False,
                "eta_s": 0,
            }
        if name == "create_link":
            state["links_issued"] = 1
            return {
                "link_id": "L1",
                "url": "http://localhost:8001/pay/L1",
                "expires_at": "2030-01-01T00:00:00+00:00",
                "links_issued": 1,
            }
        if name == "create_refund":
            state["refund_created"] = True
            return {
                "refund_id": "R1",
                "status": "PROCESSED",
                "amount": amount,
            }
        if name == "get_refund":
            return {
                "refund_id": "R1",
                "txn_id": txn_id,
                "status": "PROCESSED",
                "amount": amount,
            }
        raise AssertionError(f"Unexpected tool call: {name}")

    monkeypatch.setattr(tools, "call_tool", mocked_tool)
    return state


@pytest.mark.parametrize(
    ("scenario", "txn_ids", "amounts", "duplicate_of", "action"),
    [
        ("pending_settlement", ["TXN20"], [2000], None, "wait"),
        ("small_duplicate", ["TXN30", "TXN31"], [300, 300], "TXN30", "create_refund"),
    ],
)
def test_pending_and_small_duplicate_scenarios_use_deterministic_actions(
    monkeypatch, tmp_path, scenario, txn_ids, amounts, duplicate_of, action
):
    database = Database(tmp_path / "brain.sqlite3")
    events = EventHub(database)
    case_id, _ = _create_scenario_case(database, scenario, txn_ids, amounts)
    _mock_scenario_tools(
        monkeypatch, scenario, txn_ids[-1], duplicate_of=duplicate_of
    )
    monkeypatch.setattr(
        llm,
        "chat",
        lambda system, user, **kwargs: (
            "I checked the verified transaction details. I will share an update soon.",
            "stop",
        ),
    )

    result = supervisor.run_case(case_id, database, events)
    case_events = database.list_events(case_id=case_id, limit=100)
    resolver_step = next(
        event
        for event in case_events
        if event["type"] == "step" and event["agent"] == "resolver"
    )
    executor_step = next(
        event
        for event in case_events
        if event["type"] == "step" and event["agent"] == "executor"
    )

    assert result["status"] == "resolved"
    assert resolver_step["payload"]["action"] == action
    assert executor_step["payload"]["verified"] is True


def test_failed_payment_resolves_after_verified_link_with_budget_retry(
    monkeypatch, tmp_path
):
    database = Database(tmp_path / "brain.sqlite3")
    events = EventHub(database)
    case_id, _ = _create_scenario_case(database, "failed_payment", ["TXN1"], [1250])
    _mock_scenario_tools(monkeypatch, "failed_payment", "TXN1")
    calls = []

    def mocked_chat(system, user, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return "", "length"
        return "I checked the verified payment details. I will share the next step.", "stop"

    monkeypatch.setattr(llm, "chat", mocked_chat)
    result = supervisor.run_case(case_id, database, events)
    case_events = database.list_events(case_id=case_id, limit=100)
    link_step = next(
        event
        for event in case_events
        if event["type"] == "step"
        and event["agent"] == "executor"
        and event["payload"].get("action") == "create_link"
    )

    assert result["status"] == "resolved"
    assert link_step["payload"]["verified"] is True
    assert [call["max_tokens"] for call in calls] == [3000, 6000]
    assert any(event["type"] == "message" and event["direction"] == "assistant" for event in case_events)


def test_two_empty_length_replies_use_template_and_resolve_case(monkeypatch, tmp_path):
    database = Database(tmp_path / "brain.sqlite3")
    events = EventHub(database)
    case_id, _ = _create_scenario_case(database, "failed_payment", ["TXN1"], [1250])
    _mock_scenario_tools(monkeypatch, "failed_payment", "TXN1")
    calls = []

    def empty_length(system, user, **kwargs):
        calls.append(kwargs)
        return "", "length"

    monkeypatch.setattr(llm, "chat", empty_length)
    result = supervisor.run_case(case_id, database, events)
    case_events = database.list_events(case_id=case_id, limit=100)
    comms_step = next(
        event
        for event in case_events
        if event["type"] == "step" and event["agent"] == "comms"
    )

    assert result["status"] == "resolved"
    assert len(calls) == 2
    assert [call["max_tokens"] for call in calls] == [3000, 6000]
    assert comms_step["payload"]["fallback"] is True
    assert comms_step["payload"]["reason"] == "CommsOutputError"
    assert comms_step["summary"].endswith("(template)")
    assert "http://localhost:8001/pay/L1" in comms_step["payload"]["message"]


def test_large_duplicate_approval_executes_and_verifies_refund(monkeypatch, tmp_path):
    database = Database(tmp_path / "brain.sqlite3")
    events = EventHub(database)
    case_id, _ = _create_scenario_case(
        database, "large_duplicate", ["TXN10", "TXN11"], [1500, 1500]
    )
    tool_state = _mock_scenario_tools(
        monkeypatch, "large_duplicate", "TXN11", duplicate_of="TXN10"
    )
    monkeypatch.setattr(
        llm,
        "chat",
        lambda system, user, **kwargs: (
            "I checked the verified payment details. I will share an update soon.",
            "stop",
        ),
    )

    pending = supervisor.run_case(case_id, database, events)
    assert pending["status"] == "awaiting_approval"
    assert tool_state["refund_created"] is False

    resolved = supervisor.decide_case(case_id, True, "dashboard", database, events)
    assert resolved["status"] == "resolved"
    assert resolved["data"]["outcome"]["verified"] is True
    assert tool_state["refund_created"] is True
    assert any(
        event["type"] == "approval_decided"
        and event["approved"] is True
        and event["status"] == "resolved"
        for event in events.replay()
    )
