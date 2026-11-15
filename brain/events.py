"""Persisted dashboard events and thread-safe async fan-out."""

import asyncio
import json
import threading
from typing import Any

from brain.db import Database


Subscriber = tuple[asyncio.AbstractEventLoop, asyncio.Queue[dict[str, Any]]]


class EventHub:
    def __init__(self, database: Database) -> None:
        self.database = database
        self._lock = threading.RLock()
        self._subscribers: set[Subscriber] = set()

    def publish(self, event: dict[str, Any]) -> dict[str, Any]:
        """Persist the event before scheduling it on subscriber event loops."""
        with self._lock:
            normalized = dict(event)
            if normalized.get("type") == "step":
                normalized.setdefault("output", normalized.get("payload", {}))
                normalized.setdefault("latency_ms", normalized.get("duration_ms", 0))
            stored = self.database.add_event(normalized)
            for loop, queue in tuple(self._subscribers):
                if not loop.is_closed():
                    loop.call_soon_threadsafe(queue.put_nowait, dict(stored))
        return stored

    def replay(self, limit: int = 100) -> list[dict[str, Any]]:
        """Return the latest events in original sequence order."""
        return self.database.list_events(limit=limit)

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        with self._lock:
            self._subscribers.add((loop, queue))
        return queue

    def subscribe_with_replay(
        self,
        limit: int = 100,
    ) -> tuple[asyncio.Queue[dict[str, Any]], list[dict[str, Any]]]:
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        with self._lock:
            replay = self.database.list_events(limit=limit)
            self._subscribers.add((loop, queue))
        return queue, replay

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        with self._lock:
            self._subscribers = {
                subscriber for subscriber in self._subscribers if subscriber[1] is not queue
            }


def encode_sse(event: dict[str, Any]) -> str:
    event_type = event.get("type")
    if not isinstance(event_type, str) or not event_type:
        raise ValueError("SSE events require a non-empty type")
    payload = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event_type}\ndata: {payload}\n\n"
