from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable


SubjectHandler = Callable[[str, Any], None]


@dataclass(slots=True)
class MessageEnvelope:
    subject: str
    payload: Any
    timestamp_ns: int


@dataclass(slots=True)
class StreamConfig:
    name: str
    subjects: list[str]
    retention: str = "limits"
    max_messages: int = 10_000


class InMemoryJetStream:
    def __init__(self) -> None:
        self._subscriptions: list[tuple[str, SubjectHandler]] = []
        self._streams: dict[str, tuple[StreamConfig, deque[MessageEnvelope]]] = {}

    def add_stream(self, config: StreamConfig) -> None:
        if config.name in self._streams:
            raise ValueError(f"stream already exists: {config.name}")
        self._streams[config.name] = (config, deque(maxlen=config.max_messages))

    def subscribe(self, subject_pattern: str, handler: SubjectHandler) -> None:
        self._subscriptions.append((subject_pattern, handler))

    def publish(self, subject: str, payload: Any) -> None:
        envelope = MessageEnvelope(subject=subject, payload=payload, timestamp_ns=time.time_ns())
        for config, buffer in self._streams.values():
            if any(_subject_matches(pattern, subject) for pattern in config.subjects):
                buffer.append(envelope)
        for subject_pattern, handler in tuple(self._subscriptions):
            if _subject_matches(subject_pattern, subject):
                handler(subject, payload)

    def get_stream_messages(self, stream_name: str) -> list[MessageEnvelope]:
        if stream_name not in self._streams:
            raise KeyError(f"stream not found: {stream_name}")
        _, buffer = self._streams[stream_name]
        return list(buffer)


def _subject_matches(pattern: str, subject: str) -> bool:
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

