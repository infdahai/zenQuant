"""Small in-memory event bus with JetStream-like subject semantics.

The project uses this bus as the shared contract between live, replay, and
tests. Keeping it simple makes ordering and parity bugs easier to reason
about than if each module talked to every other module directly.
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

SubjectHandler = Callable[[str, Any], None]


class EventBus(Protocol):
    """Minimal publish/subscribe contract shared by runtime services."""

    def subscribe(self, subject_pattern: str, handler: SubjectHandler) -> None:
        ...

    def publish(self, subject: str, payload: Any) -> None:
        ...


@dataclass(slots=True)
class MessageEnvelope:
    """Recorded message metadata stored inside an in-memory stream."""

    subject: str
    payload: Any
    timestamp_ns: int


@dataclass(slots=True)
class StreamConfig:
    """Retention settings for one logical message stream."""

    name: str
    subjects: list[str]
    retention: str = "limits"
    max_messages: int = 10_000


class InMemoryJetStream:
    """Tiny subject-based bus used across runtime, replay, and tests.

    Example:
        bus = InMemoryJetStream()
        bus.add_stream(StreamConfig(name="STREAM_MARKET_DATA", subjects=["md.>"]))
        bus.publish("md.trade.BINANCE.BTCUSDT_PERP", payload)
    """

    def __init__(self) -> None:
        self._subscriptions: list[tuple[str, SubjectHandler]] = []
        self._streams: dict[str, tuple[StreamConfig, deque[MessageEnvelope]]] = {}

    def add_stream(self, config: StreamConfig) -> None:
        """Register a retained subject group before publishing to it."""
        if config.name in self._streams:
            raise ValueError(f"stream already exists: {config.name}")
        self._streams[config.name] = (config, deque(maxlen=config.max_messages))

    def subscribe(self, subject_pattern: str, handler: SubjectHandler) -> None:
        """Attach a handler to a wildcard subject pattern."""
        self._subscriptions.append((subject_pattern, handler))

    def publish(self, subject: str, payload: Any) -> None:
        """Append to matching streams, then fan out to subscribers synchronously."""
        envelope = MessageEnvelope(subject=subject, payload=payload, timestamp_ns=time.time_ns())
        for config, buffer in self._streams.values():
            if any(_subject_matches(pattern, subject) for pattern in config.subjects):
                buffer.append(envelope)
        for subject_pattern, handler in tuple(self._subscriptions):
            if _subject_matches(subject_pattern, subject):
                handler(subject, payload)

    def get_stream_messages(self, stream_name: str) -> list[MessageEnvelope]:
        """Return retained messages for deterministic replay or assertions."""
        if stream_name not in self._streams:
            raise KeyError(f"stream not found: {stream_name}")
        _, buffer = self._streams[stream_name]
        return list(buffer)


def _subject_matches(pattern: str, subject: str) -> bool:
    """Match NATS-style ``*`` and ``>`` wildcards against a concrete subject."""
    pattern_tokens = pattern.split(".")
    subject_tokens = subject.split(".")
    index = 0
    while index < len(pattern_tokens):
        token = pattern_tokens[index]
        if token == ">":
            return True
        if index >= len(subject_tokens):
            return False
        if token != "*" and token != subject_tokens[index]:
            return False
        index += 1
    return index == len(subject_tokens)
