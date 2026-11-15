"""Small SQLite persistence layer for cases and their event history."""

import json
import os
import sqlite3
from collections.abc import Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import unquote

DEFAULT_DATABASE_URL = "sqlite:///./brain.db"


class CaseNotFoundError(LookupError):
    """Raised when an operation refers to a case that does not exist."""


def _sqlite_path(database_url: str) -> str:
    prefix = "sqlite:///"
    if not database_url.startswith(prefix):
        raise ValueError("DATABASE_URL must use the sqlite:/// scheme")
    path = unquote(database_url[len(prefix):])
    if not path:
        raise ValueError("DATABASE_URL must include a SQLite database path")
    return path


class Database:
    """SQLite storage with per-operation connections and a minimal schema."""

    def __init__(self, database_url: str | None = None) -> None:
        configured_url = database_url or os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL)
        self.path = _sqlite_path(configured_url)
        if self.path != ":memory:":
            self.path = str(Path(self.path).expanduser())
            Path(self.path).resolve().parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        """Create the minimum case and event tables if they do not exist."""
        with self._connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS cases (
                    case_id TEXT PRIMARY KEY,
                    data_json TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                );
                CREATE TABLE IF NOT EXISTS events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    type TEXT NOT NULL,
                    case_id TEXT NOT NULL REFERENCES cases(case_id),
                    agent TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    summary TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS events_case_order
                    ON events(case_id, sequence);
                """
            )

    def create_case(self, case_id: str, data: Mapping[str, Any]) -> dict[str, Any]:
        """Create a case with arbitrary JSON-compatible case data."""
        serialized = json.dumps(dict(data), ensure_ascii=False)
        with self._connection() as connection:
            connection.execute(
                "INSERT INTO cases (case_id, data_json) VALUES (?, ?)",
                (case_id, serialized),
            )
        result = self.get_case(case_id)
        assert result is not None
        return result

    def get_case(self, case_id: str) -> dict[str, Any] | None:
        """Return the case record, or None when it does not exist."""
        with self._connection() as connection:
            row = connection.execute(
                "SELECT case_id, data_json, created_at, updated_at FROM cases WHERE case_id = ?",
                (case_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "case_id": row["case_id"],
            "data": json.loads(row["data_json"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def update_case(self, case_id: str, updates: Mapping[str, Any]) -> dict[str, Any]:
        """Merge JSON-compatible fields into an existing case record."""
        with self._connection() as connection:
            row = connection.execute(
                "SELECT data_json FROM cases WHERE case_id = ?", (case_id,)
            ).fetchone()
            if row is None:
                raise CaseNotFoundError(case_id)
            data = json.loads(row["data_json"])
            data.update(dict(updates))
            connection.execute(
                "UPDATE cases SET data_json = ?, updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE case_id = ?",
                (json.dumps(data, ensure_ascii=False), case_id),
            )
        result = self.get_case(case_id)
        assert result is not None
        return result

    def compare_and_set_case(
        self,
        case_id: str,
        expected: Mapping[str, Any],
        updates: Mapping[str, Any],
    ) -> bool:
        """Atomically merge updates only if the case data matches expected fields."""
        serialized_updates = dict(updates)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT data_json FROM cases WHERE case_id = ?", (case_id,)
            ).fetchone()
            if row is None:
                return False
            data = json.loads(row["data_json"])
            if any(data.get(key) != value for key, value in expected.items()):
                return False
            data.update(serialized_updates)
            cursor = connection.execute(
                "UPDATE cases SET data_json = ?, updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE case_id = ?",
                (json.dumps(data, ensure_ascii=False), case_id),
            )
            return cursor.rowcount == 1

    def add_event(self, event: Mapping[str, str]) -> int:
        """Persist one contract-shaped event and return its order sequence."""
        with self._connection() as connection:
            cursor = connection.execute(
                "INSERT INTO events (type, case_id, agent, kind, summary) VALUES (?, ?, ?, ?, ?)",
                (event["type"], event["case_id"], event["agent"], event["kind"], event["summary"]),
            )
            return int(cursor.lastrowid)

    def list_events(self, case_id: str, *, after_sequence: int = 0) -> list[dict[str, Any]]:
        """Return a case's persisted events in insertion order."""
        with self._connect() as connection:
            exists = connection.execute(
                "SELECT 1 FROM cases WHERE case_id = ?", (case_id,)
            ).fetchone()
            if exists is None:
                raise CaseNotFoundError(case_id)
            rows = connection.execute(
                "SELECT sequence, type, case_id, agent, kind, summary FROM events "
                "WHERE case_id = ? AND sequence > ? ORDER BY sequence ASC",
                (case_id, after_sequence),
            ).fetchall()
        return [dict(row) for row in rows]
