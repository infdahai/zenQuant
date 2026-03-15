"""Thin realtime-ingestion wrapper around the Binance futures client."""

from __future__ import annotations

from dataclasses import dataclass

from zenquant.infra import EventBus

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
    """Live market-data settings shared by the realtime ingestor.

    Attributes:
        symbols: Internal symbols to subscribe to.
        depth_levels: Number of order-book levels retained per depth snapshot.
        depth_update_ms: Binance depth stream cadence in milliseconds.
        mark_price_update_s: Binance mark-price update cadence in seconds.
        open_interest_poll_interval_s: REST polling interval for open interest.
        request_timeout_s: Timeout for bootstrap/polling REST requests.
        reconnect_backoff_s: Delay before reconnecting after stream failure.
        bootstrap_enabled: Whether startup should warm indicator context first.
        bootstrap_4h_bars: Closed 4h bars fetched for live warmup.
        bootstrap_1d_bars: Closed 1d bars fetched for live warmup.
        bootstrap_funding_points: Historical funding samples used for warmup.
        bootstrap_open_interest_points: Historical OI samples used for warmup.
        bootstrap_required: If true, bootstrap failure aborts startup.
    """

    symbols: tuple[str, ...] = ("BTCUSDT_PERP",)
    depth_levels: int = 10
    depth_update_ms: int = 1000
    mark_price_update_s: int = 1
    open_interest_poll_interval_s: float = 30.0
    request_timeout_s: float = 6.0
    reconnect_backoff_s: float = 3.0
    bootstrap_enabled: bool = True
    bootstrap_4h_bars: int = 90
    bootstrap_1d_bars: int = 90
    bootstrap_funding_points: int = 500
    bootstrap_open_interest_points: int = 500
    bootstrap_required: bool = False


class RealTimeIngestor:
    """Start and stop the live Binance futures client as one component."""

    def __init__(self, bus: EventBus, config: RealTimeIngestorConfig | None = None) -> None:
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
                bootstrap_enabled=runtime_config.bootstrap_enabled,
                bootstrap_4h_bars=runtime_config.bootstrap_4h_bars,
                bootstrap_1d_bars=runtime_config.bootstrap_1d_bars,
                bootstrap_funding_points=runtime_config.bootstrap_funding_points,
                bootstrap_open_interest_points=runtime_config.bootstrap_open_interest_points,
                bootstrap_required=runtime_config.bootstrap_required,
            ),
        )

    async def bootstrap_history(self) -> None:
        """Warm recent indicator context before the live stream starts."""
        await self._client.bootstrap_history()

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
