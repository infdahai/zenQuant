"""Deterministic stream replay utilities for parity checks."""

from __future__ import annotations

import time
from dataclasses import dataclass

from zenquant.infra.event_bus import InMemoryJetStream, MessageEnvelope


@dataclass(slots=True)
class ReplayConfig:
    """Replay timing options used by ``DeterministicReplayer``."""

    preserve_timing: bool = False
    max_sleep_seconds: float = 0.5


class DeterministicReplayer:
    """Re-publish retained bus messages in timestamp order.

    Example:
        replayer = DeterministicReplayer.from_stream(bus, "STREAM_MARKET_DATA")
        replayer.replay(other_bus)
    """

    def __init__(self, messages: list[MessageEnvelope]) -> None:
        self._messages = sorted(messages, key=lambda m: m.timestamp_ns)

    @classmethod
    def from_stream(cls, bus: InMemoryJetStream, stream_name: str) -> "DeterministicReplayer":
        """Build a replayer from one retained stream."""
        return cls(messages=bus.get_stream_messages(stream_name))

    def replay(self, bus: InMemoryJetStream, config: ReplayConfig | None = None) -> None:
        """Replay messages onto another bus, optionally preserving message gaps."""
        replay_cfg = config or ReplayConfig()
        previous_ts: int | None = None
        for message in self._messages:
            if replay_cfg.preserve_timing and previous_ts is not None:
                delta_ns = max(0, message.timestamp_ns - previous_ts)
                delta_seconds = min(delta_ns / 1_000_000_000, replay_cfg.max_sleep_seconds)
                if delta_seconds > 0:
                    time.sleep(delta_seconds)
            bus.publish(message.subject, message.payload)
            previous_ts = message.timestamp_ns
