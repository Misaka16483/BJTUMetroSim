from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class RunRecorder:
    """SQLite-backed recording framework for events, telemetry and metrics."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.db_path)
        self._init_schema()

    def close(self) -> None:
        self.connection.close()

    def _init_schema(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                started_at TEXT NOT NULL,
                metadata_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL,
                tick INTEGER,
                topic TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(run_id) REFERENCES runs(id)
            );
            CREATE TABLE IF NOT EXISTS metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL,
                tick INTEGER,
                name TEXT NOT NULL,
                value REAL NOT NULL,
                unit TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY(run_id) REFERENCES runs(id)
            );
            """
        )
        self.connection.commit()

    def start_run(self, name: str, metadata: dict[str, Any] | None = None) -> int:
        cursor = self.connection.execute(
            "INSERT INTO runs(name, started_at, metadata_json) VALUES (?, ?, ?)",
            (
                name,
                datetime.now(timezone.utc).isoformat(),
                json.dumps(metadata or {}, ensure_ascii=False),
            ),
        )
        self.connection.commit()
        return int(cursor.lastrowid)

    def record_event(
        self,
        run_id: int,
        topic: str,
        payload: dict[str, Any],
        *,
        tick: int | None = None,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO events(run_id, tick, topic, payload_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                run_id,
                tick,
                topic,
                json.dumps(payload, ensure_ascii=False),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        self.connection.commit()

    def record_metric(
        self,
        run_id: int,
        name: str,
        value: float,
        *,
        unit: str | None = None,
        tick: int | None = None,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO metrics(run_id, tick, name, value, unit, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                tick,
                name,
                value,
                unit,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        self.connection.commit()

