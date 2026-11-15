import asyncio
import json

import pytest
from pydantic import ValidationError

from platform.db import CaseNotFoundError, Database
from platform.events import CaseEvent, EventHub, encode_sse


def make_hub(tmp_path):
    database = Database(f"sqlite:///{tmp_path / 'events.sqlite3'}")
    database.create_case("c_17", {"status": "open"})
    return EventHub(database)


def event(summary):
    return {
        "type": "step",
        "case_id": "c_17",
        "agent": "investigator",
        "kind": "llm",
        "summary": summary,
    }


def test_event_history_preserves_order_and_contract_shape(tmp_path):
    hub = make_hub(tmp_path)
    hub.publish(event("first"))
    hub.publish(event("second"))

    history = hub.history("c_17")

    assert [entry.summary for entry in history] == ["first", "second"]
    assert set(history[0].model_dump()) == {"type", "case_id", "agent", "kind", "summary"}
    encoded = encode_sse(history[0])
    assert json.loads(encoded.removeprefix("data: ").strip()) == history[0].model_dump()


def test_publish_and_subscribe_support_multiple_live_consumers(tmp_path):
    hub = make_hub(tmp_path)

    async def exercise():
        subscriber_one = hub.subscribe("c_17")
        subscriber_two = hub.subscribe("c_17")
        waiting_one = asyncio.create_task(anext(subscriber_one))
        waiting_two = asyncio.create_task(anext(subscriber_two))
        await asyncio.sleep(0)

        published = hub.publish(event("live"))
        received_one, received_two = await asyncio.wait_for(
            asyncio.gather(waiting_one, waiting_two), timeout=1
        )

        assert received_one == published
        assert received_two == published
        await subscriber_one.aclose()
        await subscriber_two.aclose()

    asyncio.run(exercise())


def test_subscribe_replays_history_then_receives_new_events(tmp_path):
    hub = make_hub(tmp_path)
    hub.publish(event("stored"))

    async def exercise():
        subscription = hub.subscribe("c_17")
        stored = await anext(subscription)
        waiting = asyncio.create_task(anext(subscription))
        await asyncio.sleep(0)
        hub.publish(event("later"))
        later = await asyncio.wait_for(waiting, timeout=1)
        await subscription.aclose()
        return stored, later

    stored, later = asyncio.run(exercise())
    assert [stored.summary, later.summary] == ["stored", "later"]


def test_unknown_case_is_reported_for_history_publish_and_subscription(tmp_path):
    hub = make_hub(tmp_path)

    with pytest.raises(CaseNotFoundError):
        hub.history("unknown")
    with pytest.raises(CaseNotFoundError):
        hub.publish({**event("bad"), "case_id": "unknown"})

    async def read_unknown():
        subscription = hub.subscribe("unknown")
        with pytest.raises(CaseNotFoundError):
            await anext(subscription)

    asyncio.run(read_unknown())


def test_event_rejects_fields_outside_shared_contract():
    with pytest.raises(ValidationError):
        CaseEvent.model_validate({**event("extra"), "timestamp": "now"})
