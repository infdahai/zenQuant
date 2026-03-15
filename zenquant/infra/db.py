from __future__ import annotations

import json
import logging
import queue
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from psycopg import connect

from zenquant.domain import (
    ExecutionReport,
    IndicatorPack,
    MarketState,
    OpenPositionState,
    RiskSnapshot,
    TargetPosition,
)
from zenquant.domain.serde import to_jsonable

from .event_bus import InMemoryJetStream


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class OnlineStateStoreConfig:
    """Operational settings for the live PostgreSQL audit writer.

    Attributes:
        dsn: PostgreSQL connection string. When empty, persistence is disabled.
        max_queue_size: Max buffered writes before new audit events are dropped.
        apply_migrations: Whether to run the local schema migration on connect.
        reconnect_backoff_s: Initial retry delay after a connection/write failure.
        max_reconnect_backoff_s: Upper bound for exponential reconnect backoff.
    """

    dsn: str | None = None
    max_queue_size: int = 10_000
    apply_migrations: bool = True
    reconnect_backoff_s: float = 5.0
    max_reconnect_backoff_s: float = 30.0


class OnlineStateStore:
    def __init__(
        self,
        bus: InMemoryJetStream,
        config: OnlineStateStoreConfig | None = None,
    ) -> None:
        self._bus = bus
        self._config = config or OnlineStateStoreConfig()
        self._queue: queue.Queue[tuple[str, tuple[Any, ...]]] = queue.Queue(
            maxsize=self._config.max_queue_size
        )
        self._worker: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._enabled = bool(self._config.dsn)
        self._connected = False
        self._last_error = ""
        self._writes = 0
        self._dropped = 0

        self._bus.subscribe("indicator.>", self._on_indicator)
        self._bus.subscribe("intent.target_position.final.>", self._on_target)
        self._bus.subscribe("risk.snapshot", self._on_risk_snapshot)
        self._bus.subscribe("exec.order.report", self._on_execution_report)
        self._bus.subscribe("strategy.position_state.>", self._on_position_state)
        self._bus.subscribe("state.market.>", self._on_market_state)

    def start(self) -> None:
        if not self._enabled or self._worker is not None:
            return
        self._stop_event.clear()
        self._worker = threading.Thread(target=self._run, name="zenquant-db-writer", daemon=True)
        self._worker.start()

    def stop(self) -> None:
        if self._worker is None:
            return
        self._stop_event.set()
        self._queue.put(("__STOP__", ()))
        self._worker.join(timeout=5.0)
        self._worker = None

    def snapshot(self) -> dict[str, Any]:
        """Return operator-facing health for the dashboard/runtime endpoints."""
        return {
            "enabled": self._enabled,
            "connected": self._connected,
            "writes": self._writes,
            "dropped": self._dropped,
            "queue_depth": self._queue.qsize(),
            "last_error": self._last_error,
        }

    def _run(self) -> None:
        assert self._config.dsn is not None
        # Keep retrying so a transient PostgreSQL outage does not permanently
        # disable audit persistence for a long-running paper/live process.
        backoff_s = max(1.0, float(self._config.reconnect_backoff_s))
        while not self._stop_event.is_set():
            try:
                with connect(self._config.dsn, autocommit=True) as conn:
                    self._connected = True
                    self._last_error = ""
                    backoff_s = max(1.0, float(self._config.reconnect_backoff_s))
                    if self._config.apply_migrations:
                        self._apply_migrations(conn)
                    with conn.cursor() as cur:
                        while not self._stop_event.is_set():
                            try:
                                sql, params = self._queue.get(timeout=1.0)
                            except queue.Empty:
                                continue
                            if sql == "__STOP__":
                                self._stop_event.set()
                                break
                            cur.execute(sql, params)
                            self._writes += 1
            except Exception as exc:
                if self._stop_event.is_set():
                    break
                self._connected = False
                self._last_error = str(exc)
                logger.exception("online state store error: %s", exc)
                time.sleep(backoff_s)
                backoff_s = min(backoff_s * 2.0, float(self._config.max_reconnect_backoff_s))
        self._connected = False

    def _apply_migrations(self, conn: Any) -> None:
        migration_path = Path(__file__).resolve().parents[2] / "migrations" / "001_initial_schema.sql"
        if not migration_path.exists():
            return
        sql = migration_path.read_text(encoding="utf-8")
        with conn.cursor() as cur:
            cur.execute(sql)

    def _enqueue(self, sql: str, params: tuple[Any, ...]) -> None:
        if not self._enabled:
            return
        try:
            self._queue.put_nowait((sql, params))
        except queue.Full:
            self._dropped += 1

    def _on_indicator(self, _: str, payload: object) -> None:
        if not isinstance(payload, IndicatorPack):
            return
        self._enqueue(
            """
            INSERT INTO indicator_packs (
                ts_ns, symbol, timeframe, cutoff_time_ns, indicator_version,
                formula_hash, quality_flags, values
            ) VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb)
            """,
            (
                payload.ts_ns,
                payload.symbol,
                payload.timeframe.value,
                payload.cutoff_time_ns,
                payload.indicator_version,
                payload.formula_hash,
                json.dumps(list(payload.quality_flags)),
                json.dumps(to_jsonable(payload.values)),
            ),
        )

    def _on_target(self, _: str, payload: object) -> None:
        if not isinstance(payload, TargetPosition):
            return
        self._enqueue(
            """
            INSERT INTO strategy_intents (
                intent_id, portfolio, symbol, timeframe, cutoff_time_ns, target_qty,
                target_leverage, reduce_only, regime, regime_confidence, reason_codes
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
            ON CONFLICT (intent_id) DO NOTHING
            """,
            (
                payload.intent_id,
                payload.portfolio,
                payload.symbol,
                payload.timeframe.value,
                payload.cutoff_time_ns,
                payload.target_qty,
                payload.target_leverage,
                payload.reduce_only,
                payload.regime,
                payload.regime_confidence,
                json.dumps(list(payload.reason_codes)),
            ),
        )

    def _on_risk_snapshot(self, _: str, payload: object) -> None:
        if not isinstance(payload, RiskSnapshot):
            return
        self._enqueue(
            """
            INSERT INTO risk_snapshots (
                portfolio, ts_ns, equity, high_water_mark, drawdown, lev_cap,
                reduce_only_mode, flags
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb)
            """,
            (
                payload.portfolio,
                payload.ts_ns,
                payload.equity,
                payload.high_water_mark,
                payload.drawdown,
                payload.lev_cap,
                payload.reduce_only_mode,
                json.dumps(list(payload.flags)),
            ),
        )

    def _on_execution_report(self, _: str, payload: object) -> None:
        if not isinstance(payload, ExecutionReport):
            return
        self._enqueue(
            """
            INSERT INTO execution_reports (
                client_order_id, symbol, side, status, filled_qty, avg_price,
                fee, transaction_time_ns, reject_reason
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (client_order_id) DO NOTHING
            """,
            (
                payload.client_order_id,
                payload.symbol,
                payload.side,
                payload.status,
                payload.filled_qty,
                payload.avg_price,
                payload.fee,
                payload.transaction_time_ns,
                payload.reject_reason,
            ),
        )

    def _on_position_state(self, subject: str, payload: object) -> None:
        if not isinstance(payload, OpenPositionState):
            return
        parts = subject.split(".")
        timeframe = parts[2] if len(parts) >= 4 else payload.timeframe.value
        self._enqueue(
            """
            INSERT INTO position_states (
                symbol, timeframe, snapshot_time_ns, direction, entry_price,
                current_qty, trailing_stop, bars_held
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                payload.symbol,
                timeframe,
                payload.entry_bar_ns,
                payload.direction,
                payload.entry_price,
                payload.current_qty,
                payload.trailing_stop,
                payload.bars_held,
            ),
        )

    def _on_market_state(self, _: str, payload: object) -> None:
        if not isinstance(payload, MarketState):
            return
        self._enqueue(
            """
            INSERT INTO market_states (
                symbol, timeframe, cutoff_time_ns,
                directional_state, directional_confidence,
                volatility_state, volatility_confidence,
                positioning_state, positioning_confidence,
                quality_state, quality_confidence,
                regime, regime_confidence, direction_signal, transition_risk,
                quality_flags, reason_codes
            ) VALUES (
                %s, %s, %s,
                %s, %s,
                %s, %s,
                %s, %s,
                %s, %s,
                %s, %s, %s, %s,
                %s::jsonb, %s::jsonb
            )
            """,
            (
                payload.symbol,
                payload.timeframe.value,
                payload.cutoff_time_ns,
                payload.directional_state,
                payload.directional_confidence,
                payload.volatility_state,
                payload.volatility_confidence,
                payload.positioning_state,
                payload.positioning_confidence,
                payload.quality_state,
                payload.quality_confidence,
                payload.regime,
                payload.regime_confidence,
                payload.direction_signal,
                payload.transition_risk,
                json.dumps(list(payload.quality_flags)),
                json.dumps(list(payload.reason_codes)),
            ),
        )
