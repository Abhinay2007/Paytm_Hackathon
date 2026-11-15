"""SQLite persistence for cases and Brain events."""

import json
import os
import sqlite3
import threading
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import unquote


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    """Small SQLite store using a connection per operation."""

    def __init__(self, path: str | Path | None = None) -> None:
        configured_path = str(path) if path is not None else os.getenv("BRAIN_DB_PATH", "brain.db")
        if configured_path.startswith("sqlite:///"):
            configured_path = unquote(configured_path[len("sqlite:///") :])

        self._lock = threading.RLock()
        self._memory = configured_path == ":memory:"
        if self._memory:
            self.path = f"file:brain-{id(self)}?mode=memory&cache=shared"
            self._anchor = sqlite3.connect(self.path, uri=True, check_same_thread=False)
        else:
            file_path = Path(configured_path).expanduser()
            file_path.parent.mkdir(parents=True, exist_ok=True)
            self.path = str(file_path)
            self._anchor = None
        self.initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.path,
            timeout=5.0,
            check_same_thread=False,
            uri=self._memory,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def initialize(self) -> None:
        with self._lock, self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS cases (
                    id TEXT PRIMARY KEY,
                    customer_id TEXT NOT NULL,
                    chat_id TEXT,
                    txn_id TEXT,
                    language TEXT NOT NULL DEFAULT 'en',
                    status TEXT NOT NULL DEFAULT 'open',
                    data_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    type TEXT NOT NULL,
                    case_id TEXT NOT NULL REFERENCES cases(id),
                    agent TEXT NOT NULL DEFAULT '',
                    kind TEXT NOT NULL DEFAULT '',
                    summary TEXT NOT NULL DEFAULT '',
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS events_case_order
                    ON events(case_id, sequence);
                """
            )

    def create_case(
        self,
        case_id: str,
        customer_id: str,
        chat_id: str | int | None = None,
        txn_id: str | None = None,
        language: str = "en",
        status: str = "open",
        data: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        now = _utc_now()
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO cases
                    (id, customer_id, chat_id, txn_id, language, status, data_json,
                     created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    case_id,
                    customer_id,
                    str(chat_id) if chat_id is not None else None,
                    txn_id,
                    language,
                    status,
                    json.dumps(dict(data or {}), ensure_ascii=False),
                    now,
                    now,
                ),
            )
        result = self.get_case(case_id)
        if result is None:
            raise RuntimeError(f"Case {case_id!r} was not persisted")
        return result

    def get_case(self, case_id: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM cases WHERE id = ?", (case_id,)
            ).fetchone()
        if row is None:
            return None
        case = dict(row)
        case["case_id"] = case["id"]
        case["data"] = json.loads(case.pop("data_json"))
        return case

    def update_case(self, case_id: str, updates: Mapping[str, Any]) -> dict[str, Any]:
        columns = {"customer_id", "chat_id", "txn_id", "language", "status"}
        column_updates = {key: value for key, value in updates.items() if key in columns}
        data_updates = {
            key: value for key, value in updates.items() if key not in columns
        }
        now = _utc_now()
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT data_json FROM cases WHERE id = ?", (case_id,)
            ).fetchone()
            if row is None:
                raise LookupError(f"Case {case_id!r} does not exist")
            data = json.loads(row["data_json"])
            data.update(data_updates)
            assignments = ["data_json = ?", "updated_at = ?"]
            values: list[Any] = [json.dumps(data, ensure_ascii=False), now]
            for key, value in column_updates.items():
                assignments.append(f"{key} = ?")
                values.append(str(value) if key == "chat_id" and value is not None else value)
            values.append(case_id)
            connection.execute(
                f"UPDATE cases SET {', '.join(assignments)} WHERE id = ?",
                values,
            )
        result = self.get_case(case_id)
        if result is None:
            raise RuntimeError(f"Case {case_id!r} disappeared during update")
        return result

    def list_cases(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT id FROM cases ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        cases = [self.get_case(row["id"]) for row in rows]
        return [case for case in cases if case is not None]

    def add_event(self, event: Mapping[str, Any]) -> dict[str, Any]:
        event_type = event.get("type")
        case_id = event.get("case_id")
        if not isinstance(event_type, str) or not isinstance(case_id, str):
            raise ValueError("Events require string type and case_id fields")

        created_at = event.get("created_at") or _utc_now()
        payload = {
            key: value
            for key, value in event.items()
            if key not in {"type", "case_id", "agent", "kind", "summary", "seq", "created_at"}
        }
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO events
                    (type, case_id, agent, kind, summary, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_type,
                    case_id,
                    event.get("agent", ""),
                    event.get("kind", ""),
                    event.get("summary", ""),
                    json.dumps(payload, ensure_ascii=False),
                    created_at,
                ),
            )
            sequence = int(cursor.lastrowid)
        return self._event_from_row(
            {
                "sequence": sequence,
                "type": event_type,
                "case_id": case_id,
                "agent": event.get("agent", ""),
                "kind": event.get("kind", ""),
                "summary": event.get("summary", ""),
                "payload_json": json.dumps(payload, ensure_ascii=False),
                "created_at": created_at,
            }
        )

    def list_events(
        self,
        limit: int = 100,
        case_id: str | None = None,
    ) -> list[dict[str, Any]]:
        if limit <= 0:
            return []
        with self._lock, self._connect() as connection:
            if case_id is None:
                rows = connection.execute(
                    "SELECT * FROM events ORDER BY sequence DESC LIMIT ?", (limit,)
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM events WHERE case_id = ? "
                    "ORDER BY sequence DESC LIMIT ?",
                    (case_id, limit),
                ).fetchall()
        return [self._event_from_row(row) for row in reversed(rows)]

    @staticmethod
    def _event_from_row(row: Mapping[str, Any]) -> dict[str, Any]:
        event = {
            "type": row["type"],
            "case_id": row["case_id"],
            **json.loads(row["payload_json"]),
        }
        if event["type"] == "step":
            event.update(
                {
                    "seq": row["sequence"],
                    "agent": row["agent"],
                    "kind": row["kind"],
                    "summary": row["summary"],
                    "created_at": row["created_at"],
                }
            )
        elif event["type"] == "message":
            event["created_at"] = row["created_at"]
        return event
