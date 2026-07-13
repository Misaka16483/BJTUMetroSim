from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
import threading
from typing import Any


class ConnectionEventLog:
    """Small thread-safe ring buffer for hardware connection observability."""

    def __init__(self, max_entries: int = 160) -> None:
        if max_entries <= 0:
            raise ValueError("max_entries must be positive")
        self._entries: deque[dict[str, Any]] = deque(maxlen=max_entries)
        self._sequence = 0
        self._lock = threading.RLock()

    def append(
        self,
        endpoint: str,
        event: str,
        message: str,
        *,
        level: str = "INFO",
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            self._sequence += 1
            entry = {
                "sequence": self._sequence,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "endpoint": endpoint,
                "level": level.upper(),
                "event": event,
                "message": message,
                "details": dict(details or {}),
            }
            self._entries.append(entry)
            return dict(entry)

    def snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            return [{**entry, "details": dict(entry["details"])} for entry in self._entries]

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
