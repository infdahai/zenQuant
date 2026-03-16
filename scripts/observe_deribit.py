"""Run the Deribit observation layer continuously and persist published packs.

Usage:
    ZENQUANT_PG_DSN="postgresql://..." uv run python scripts/observe_deribit.py
"""

from __future__ import annotations

import asyncio
import os
import signal
import sys
from datetime import datetime

from zenquant.domain import DeribitRiskPack
from zenquant.ingestor import DeribitOptionsIngestor, DeribitOptionsIngestorConfig
from zenquant.infra import InMemoryJetStream, OnlineStateStore, OnlineStateStoreConfig


def _format_pack(pack: DeribitRiskPack) -> str:
    term_label = "contango" if pack.term_structure_slope >= 0 else "backwardation"
    return "\n".join(
        [
            f"[{datetime.now().isoformat(timespec='seconds')}] Deribit BTC",
            f"  ATM IV 30d: {pack.atm_iv_30d:.2f}",
            f"  Term structure: {pack.term_structure_slope:+.2%} ({term_label})",
            f"  Put/Call skew: {pack.put_call_skew_25d:+.4f}",
            f"  OI concentration: {pack.oi_concentration:.2%}",
            f"  Flow imbalance proxy: {pack.block_flow_imbalance:+.2%}",
            f"  Quality flags: {', '.join(pack.quality_flags)}",
        ]
    )


async def _run() -> int:
    dsn = os.getenv("ZENQUANT_PG_DSN")
    if not dsn:
        print("ZENQUANT_PG_DSN is required")
        return 1

    bus = InMemoryJetStream()
    store = OnlineStateStore(bus=bus, config=OnlineStateStoreConfig(dsn=dsn))
    ingestor = DeribitOptionsIngestor(
        bus=bus,
        config=DeribitOptionsIngestorConfig(currency="BTC", poll_interval_s=900.0),
    )

    stop_event = asyncio.Event()

    def _handle_signal(_signum, _frame) -> None:
        stop_event.set()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    bus.subscribe(
        "risk.deribit.>",
        lambda _subject, payload: print(_format_pack(payload)) if isinstance(payload, DeribitRiskPack) else None,
    )

    store.start()
    try:
        await ingestor.start()
        print("Deribit observation started")
        print("Polling every 900 seconds")
        print("Press Ctrl+C to stop")
        await stop_event.wait()
    finally:
        await ingestor.stop()
        store.stop()
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(_run()))


if __name__ == "__main__":
    main()
