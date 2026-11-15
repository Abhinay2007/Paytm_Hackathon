"""Persisted case events and non-blocking per-case async subscriptions."""

import asyncio
import json
import threading
from collections.abc import AsyncIterator
from pydantic import BaseModel, ConfigDict

from platform.db import CaseNotFoundError, Database


class CaseEvent(BaseModel):
    """The exact public event payload defined in CONTRACT.md."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    type: str
    case_id: str
    agent: str
    kind: str
    summary: str


_Subscriber = tuple[asyncio.AbstractEventLoop, asyncio.Queue[CaseEvent]]


class EventHub:
    """Store events and fan them out to any number of case subscribers."""

    def __init__(self, database: Database) -> None:
        self.database = database
        self._lock = threading.RLock()
        self._subscribers: dict[str, set[_Subscriber]] = {}

    def publish(self, event: CaseEvent | dict[str, str]) -> CaseEvent:
        """Validate, persist, and enqueue an event without waiting for clients."""
        validated = event if isinstance(event, CaseEvent) else CaseEvent.model_validate(event)
        with self._lock:
            if self.database.get_case(validated.case_id) is None:
                raise CaseNotFoundError(validated.case_id)
            self.database.add_event(validated.model_dump())
            for loop, queue in tuple(self._subscribers.get(validated.case_id, ())):
                if not loop.is_closed():
                    loop.call_soon_threadsafe(queue.put_nowait, validated)
        return validated

    def history(self, case_id: str) -> list[CaseEvent]:
        """Return persisted case events in insertion order."""
        rows = self.database.list_events(case_id)
        return [
            CaseEvent.model_validate(
                {key: row[key] for key in ("type", "case_id", "agent", "kind", "summary")}
            )
            for row in rows
        ]

    async def subscribe(self, case_id: str) -> AsyncIterator[CaseEvent]:
        """Yield history followed by live events until the consumer disconnects."""
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[CaseEvent] = asyncio.Queue()
        subscriber: _Subscriber = (loop, queue)
        with self._lock:
            history = self.history(case_id)  # Also rejects unknown case IDs.
            self._subscribers.setdefault(case_id, set()).add(subscriber)
        try:
            for event in history:
                yield event
            while True:
                yield await queue.get()
        finally:
            with self._lock:
                subscribers = self._subscribers.get(case_id)
                if subscribers is not None:
                    subscribers.discard(subscriber)
                    if not subscribers:
                        self._subscribers.pop(case_id, None)


def encode_sse(event: CaseEvent) -> str:
    """Encode an event as SSE data without adding non-contract payload fields."""
    payload = json.dumps(event.model_dump(), ensure_ascii=False, separators=(",", ":"))
    return f"data: {payload}\n\n"
