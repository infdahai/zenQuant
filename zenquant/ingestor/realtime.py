"""Thin realtime-ingestion wrapper around the Binance futures client."""

from __future__ import annotations

from dataclasses import dataclass

from zenquant.infra import InMemoryJetStream

from .binance_ws import BinancePerpStreamClient, BinancePerpStreamConfig, _parse_depth_side, _to_binance_symbol


def _to_okx_inst_id(symbol: str) -> str | None:
    """Legacy symbol mapper kept for future multi-venue work."""
    mapping = {
        "BTCUSDT_PERP": "BTC-USDT-SWAP",
        "ETHUSDT_PERP": "ETH-USDT-SWAP",
    }
    return mapping.get(symbol)


@dataclass(slots=True)
class RealTimeIngestorConfig:
    """Live market-data settings shared by the realtime ingestor."""

    symbols: tuple[str, ...] = ("BTCUSDT_PERP",)
    depth_levels: int = 10
    depth_update_ms: int = 1000
    mark_price_update_s: int = 1
    open_interest_poll_interval_s: float = 30.0
    request_timeout_s: float = 6.0
    reconnect_backoff_s: float = 3.0


class RealTimeIngestor:
    """Start and stop the live Binance futures client as one component."""

    def __init__(self, bus: InMemoryJetStream, config: RealTimeIngestorConfig | None = None) -> None:
        runtime_config = config or RealTimeIngestorConfig()
        self._client = BinancePerpStreamClient(
            bus=bus,
            config=BinancePerpStreamConfig(
                symbols=runtime_config.symbols,
                depth_levels=runtime_config.depth_levels,
                depth_update_ms=runtime_config.depth_update_ms,
                mark_price_update_s=runtime_config.mark_price_update_s,
                open_interest_poll_interval_s=runtime_config.open_interest_poll_interval_s,
                request_timeout_s=runtime_config.request_timeout_s,
                reconnect_backoff_s=runtime_config.reconnect_backoff_s,
            ),
        )

    async def start(self) -> None:
        """Start websocket and polling tasks."""
        await self._client.start()

    async def stop(self) -> None:
        """Stop websocket and polling tasks."""
        await self._client.stop()


__all__ = [
    "RealTimeIngestor",
    "RealTimeIngestorConfig",
    "_parse_depth_side",
    "_to_binance_symbol",
    "_to_okx_inst_id",
]
