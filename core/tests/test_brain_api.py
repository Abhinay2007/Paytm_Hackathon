import asyncio
import threading

from brain import runtime, supervisor
from brain.db import Database
from brain.events import EventHub
from core import main


def _isolate_brain(monkeypatch, tmp_path):
    database = Database(tmp_path / "brain.sqlite3")
    events = EventHub(database)
    monkeypatch.setattr(main.brain_runtime, "_database", database)
    monkeypatch.setattr(main.brain_runtime, "_events", events)
    return database, events


def test_case_api_reads_dashboard_shape_and_event_details(monkeypatch, tmp_path):
    database, events = _isolate_brain(monkeypatch, tmp_path)
    database.create_case(
        "c_test",
        "C1",
        chat_id="web-1",
        txn_id="TXN1001",
        language="en",
        status="resolved",
        data={
            "intent": "failed_payment",
            "resolved_at": "2026-01-01T00:00:10+00:00",
            "outcome": {"type": "create_link"},
        },
    )
    events.publish(
        {
            "type": "step",
            "case_id": "c_test",
            "agent": "executor",
            "kind": "tool",
            "summary": "Link verified.",
            "ok": True,
            "payload": {"link_id": "L1"},
            "duration_ms": 4,
        }
    )
    events.publish(
        {
            "type": "message",
            "case_id": "c_test",
            "text": "Your payment link is ready.",
            "direction": "assistant",
            "chat_id": "web-1",
        }
    )

    cases = main.list_brain_cases()
    detail = main.get_brain_case("c_test")

    assert cases[0]["id"] == "c_test"
    assert cases[0]["intent"] == "failed payment"
    assert detail["steps"][0]["output"] == {"link_id": "L1"}
    assert detail["messages"][0]["direction"] == "assistant"
    assert main.list_brain_cases("open") == []


def test_metrics_and_named_sse_replay(monkeypatch, tmp_path):
    database, events = _isolate_brain(monkeypatch, tmp_path)
    database.create_case(
        "c_metrics",
        "C1",
        status="resolved",
        data={
            "human_touched": False,
            "resolved_at": "2026-01-01T00:00:10+00:00",
            "outcome": {
                "type": "create_refund",
                "verified": True,
                "refund_amount": 300,
            },
        },
    )
    events.publish(
        {
            "type": "case_status",
            "case_id": "c_metrics",
            "status": "resolved",
            "resolved_at": "2026-01-01T00:00:10+00:00",
        }
    )
    metrics = main.get_brain_metrics()
    assert metrics["total_cases"] == 1
    assert metrics["auto_resolution_rate"] == 1
    assert metrics["rupees_recovered"] == 300

    async def first_event():
        response = await main.stream_brain_events()
        try:
            return await anext(response.body_iterator)
        finally:
            await response.body_iterator.aclose()

    replayed = asyncio.run(first_event())
    assert "event: case_status\n" in replayed
    assert '"status":"resolved"' in replayed


def test_llm_debug_endpoint_returns_recent_safe_metadata(monkeypatch):
    observed = {}
    rows = [{"attempt": 2, "request_max_tokens": 6000, "usage": {"total_tokens": 30}}]

    def recent_call_metadata(limit):
        observed["limit"] = limit
        return rows

    monkeypatch.setattr(main.brain_llm, "recent_call_metadata", recent_call_metadata)

    assert main.get_llm_debug_metadata() == {"calls": rows}
    assert observed["limit"] == 20


def test_decision_route_runs_runtime_in_worker_thread(monkeypatch):
    caller_thread = threading.get_ident()
    observed = {}

    def decide(case_id, approved, by):
        observed["thread"] = threading.get_ident()
        observed["args"] = (case_id, approved, by)
        return {"id": case_id, "status": "escalated"}

    monkeypatch.setattr(main.brain_runtime, "decide_case", decide)
    response = asyncio.run(
        main.decide_brain_case(
            "c_decision",
            main.DecisionRequest(approved=False, by="dashboard"),
        )
    )
    assert response == {
        "ok": True,
        "case_id": "c_decision",
        "approved": False,
        "status": "escalated",
    }
    assert observed["thread"] != caller_thread
    assert observed["args"] == ("c_decision", False, "dashboard")


def test_rejection_escalates_and_notifies_customer(monkeypatch, tmp_path):
    database, events = _isolate_brain(monkeypatch, tmp_path)
    case_id = runtime.create_case(
        "C1",
        "TXN1001",
        "Please refund this payment.",
        "en",
        chat_id="web-1",
    )
    database.update_case(
        case_id,
        {
            "status": "awaiting_approval",
            "pending_action": {
                "action": "create_refund",
                "case_type": "refund_request",
                "facts": {"txn_id": "TXN1001", "status": "SUCCESS", "amount": 300},
            },
            "approval": {"status": "pending", "action": "Refund ₹300"},
        },
    )
    monkeypatch.setattr(
        supervisor.CommsAgent,
        "compose",
        lambda self, language, facts, **kwargs: (
            "The proposed action was rejected. Your case has been escalated."
        ),
    )

    result = asyncio.run(
        asyncio.to_thread(runtime.decide_case, case_id, False, "dashboard")
    )
    case_events = events.replay()

    assert result["status"] == "escalated"
    assert result["data"]["outcome"]["type"] == "rejected"
    assert any(
        event["type"] == "message"
        and event["case_id"] == case_id
        and event["direction"] == "assistant"
        for event in case_events
    )
    assert any(
        event["type"] == "approval_decided"
        and event["case_id"] == case_id
        and event["approved"] is False
        and event["status"] == "escalated"
        for event in case_events
    )
