import asyncio
import json
import threading

from brain.db import Database
from brain.events import EventHub, encode_sse


def test_database_persists_case_and_latest_events_in_order(tmp_path):
    database = Database(tmp_path / "brain.sqlite3")
    database.create_case(
        "c_1",
        customer_id="C1",
        chat_id="C1",
        txn_id="TXN1001",
        language="en",
        data={"text": "Payment failed"},
    )
    hub = EventHub(database)
    first = hub.publish({"type": "case_status", "case_id": "c_1", "status": "open"})
    second = hub.publish(
        {
            "type": "step",
            "case_id": "c_1",
            "agent": "resolver",
            "kind": "llm",
            "summary": "Checking facts",
            "ok": True,
            "payload": {"status": "FAILED"},
            "duration_ms": 12,
        }
    )

    assert database.get_case("c_1")["data"]["text"] == "Payment failed"
    assert [event["type"] for event in hub.replay(limit=1)] == ["step"]
    assert hub.replay(limit=2) == [first, second]
    assert second["seq"] > first.get("seq", 0)
    assert second["payload"] == {"status": "FAILED"}


def test_event_hub_fans_out_from_worker_thread_and_encodes_named_sse(tmp_path):
    hub = EventHub(Database(tmp_path / "brain.sqlite3"))
    hub.database.create_case("c_2", customer_id="C2")

    async def receive_event():
        queue = hub.subscribe()
        event = {
            "type": "message",
            "case_id": "c_2",
            "text": "Payment received",
            "direction": "assistant",
            "chat_id": "C2",
        }
        worker = threading.Thread(target=hub.publish, args=(event,))
        worker.start()
        try:
            received = await asyncio.wait_for(queue.get(), timeout=1)
        finally:
            worker.join()
            hub.unsubscribe(queue)
        return received

    received = asyncio.run(receive_event())
    encoded = encode_sse(received)
    assert encoded.startswith("event: message\ndata: ")
    assert json.loads(encoded.split("data: ", 1)[1]) == received
    assert received["direction"] == "assistant"
