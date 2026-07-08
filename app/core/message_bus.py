from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from itertools import count
from typing import Any, Callable, DefaultDict


Subscriber = Callable[["Envelope"], None]


@dataclass(frozen=True)
class Envelope:
    sequence: int
    topic: str
    payload: dict[str, Any]
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    source: str | None = None
    tick: int | None = None


class MessageBus:
    """Small in-process event bus for Phase 0 module integration."""

    def __init__(self) -> None:
        self._sequence = count(1)
        self._history: list[Envelope] = []
        self._latest: dict[str, Envelope] = {}
        self._subscribers: DefaultDict[str, list[Subscriber]] = defaultdict(list)

    def publish(
        self,
        topic: str,
        payload: dict[str, Any],
        *,
        source: str | None = None,
        tick: int | None = None,
    ) -> Envelope:
        envelope = Envelope(
            sequence=next(self._sequence),
            topic=topic,
            payload=payload,
            source=source,
            tick=tick,
        )
        self._history.append(envelope)
        self._latest[topic] = envelope
        for subscriber in [*self._subscribers.get(topic, []), *self._subscribers.get("*", [])]:
            subscriber(envelope)
        return envelope

    def subscribe(self, topic: str, subscriber: Subscriber) -> None:
        self._subscribers[topic].append(subscriber)

    def latest(self, topic: str) -> Envelope | None:
        return self._latest.get(topic)

    def history(self, topic: str | None = None) -> list[Envelope]:
        if topic is None:
            return list(self._history)
        return [envelope for envelope in self._history if envelope.topic == topic]

