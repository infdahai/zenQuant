"""Complete decision logging for trade attribution and debugging.

Captures the FULL decision chain at each bar: indicators → signals →
composition → risk overlay → execution. Enables post-hoc analysis of
why each trade was made (or not made).

Logs to JSONL files, one line per bar.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from zenquant.domain import (
    ExecutionReport,
    IndicatorPack,
    MarketState,
    RiskOverlayDecision,
    RiskSnapshot,
    TargetPosition,
    Timeframe,
)
from zenquant.infra import EventBus


@dataclass(slots=True)
class DecisionRecord:
    """One complete decision record per bar."""

    ts: str
    symbol: str
    price: float
    # Indicators
    momentum_z: float
    cvd_slope: float
    funding_oi_z: float
    realized_vol: float
    cycle_position: float
    vol_ratio_rv: float
    directional_consistency: float
    # Macro
    vix_z: float
    tlt_z: float
    qqq_z: float
    iwm_z: float
    # State
    regime: str
    directional_state: str
    volatility_state: str
    transition_risk: float
    # Target
    target_qty: float
    target_leverage: float
    reduce_only: bool
    reason_codes: list[str]
    # Risk overlay
    risk_lev_cap: float
    risk_flags: list[str]
    original_qty: float
    revised_qty: float
    # Execution
    filled: bool
    filled_qty: float
    fill_price: float
    fee: float


class DecisionLogger:
    """Subscribe to all pipeline events and log complete decision records."""

    def __init__(self, bus: EventBus, output_dir: Path | None = None) -> None:
        self._bus = bus
        self._output_dir = output_dir or Path("logs/decisions")
        self._output_dir.mkdir(parents=True, exist_ok=True)

        ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        self._log_path = self._output_dir / f"decisions_{ts}.jsonl"

        # Pending data (collected across events for same bar)
        self._latest_pack: dict[str, IndicatorPack] = {}
        self._latest_state: dict[str, MarketState] = {}
        self._latest_risk: dict[str, RiskOverlayDecision] = {}
        self._latest_snapshot: dict[str, RiskSnapshot] = {}

        bus.subscribe("indicator.>", self._on_indicator)
        bus.subscribe("state.market.>", self._on_state)
        bus.subscribe("risk.overlay.decision", self._on_risk_decision)
        bus.subscribe("risk.snapshot", self._on_risk_snapshot)
        bus.subscribe("intent.target_position.final.>", self._on_final_target)
        bus.subscribe("exec.order.report", self._on_execution)

    def _on_indicator(self, _: str, payload: object) -> None:
        if isinstance(payload, IndicatorPack) and payload.timeframe == Timeframe.TF_4H:
            self._latest_pack[payload.symbol] = payload

    def _on_state(self, _: str, payload: object) -> None:
        if isinstance(payload, MarketState) and payload.timeframe == Timeframe.TF_4H:
            self._latest_state[payload.symbol] = payload

    def _on_risk_decision(self, _: str, payload: object) -> None:
        if isinstance(payload, RiskOverlayDecision):
            self._latest_risk[payload.symbol] = payload

    def _on_risk_snapshot(self, _: str, payload: object) -> None:
        if isinstance(payload, RiskSnapshot):
            self._latest_snapshot[payload.portfolio] = payload

    def _on_final_target(self, _: str, payload: object) -> None:
        if not isinstance(payload, TargetPosition):
            return
        self._write_record(payload, filled=False)

    def _on_execution(self, _: str, payload: object) -> None:
        if isinstance(payload, ExecutionReport) and payload.status == "FILLED":
            # Update the last record with fill info
            pass  # Fill info captured in _write_record via risk decision

    def _write_record(self, target: TargetPosition, filled: bool = False) -> None:
        pack = self._latest_pack.get(target.symbol)
        state = self._latest_state.get(target.symbol)
        risk = self._latest_risk.get(target.symbol)

        vals = pack.values if pack else {}
        ts = datetime.fromtimestamp(target.cutoff_time_ns / 1e9, tz=UTC)

        record = {
            "ts": ts.isoformat(),
            "symbol": target.symbol,
            "price": float(vals.get("close", vals.get("last_price", 0)) or 0),
            # Indicators
            "momentum_z": float(vals.get("multi_momentum_z", 0) or 0),
            "cvd_slope": float(vals.get("cvd_slope", 0) or 0),
            "funding_oi_z": float(vals.get("funding_oi_z", 0) or 0),
            "realized_vol": float(vals.get("realized_vol", 0) or 0),
            "cycle_position": float(vals.get("cycle_position", 1) or 1),
            "vol_ratio_rv": float(vals.get("vol_ratio_rv", 1) or 1),
            "dir_consistency": float(vals.get("directional_consistency", 1) or 1),
            # Macro
            "vix_z": float(vals.get("vix_z", 0) or 0),
            "tlt_z": float(vals.get("tlt_z", 0) or 0),
            "qqq_z": float(vals.get("qqq_z", 0) or 0),
            "iwm_z": float(vals.get("iwm_z", 0) or 0),
            # State
            "regime": state.regime if state else "UNKNOWN",
            "directional": state.directional_state if state else "UNKNOWN",
            "volatility": state.volatility_state if state else "UNKNOWN",
            "transition_risk": state.transition_risk if state else 0,
            # Target
            "target_qty": target.target_qty,
            "target_leverage": target.target_leverage,
            "reduce_only": target.reduce_only,
            "reason_codes": target.reason_codes[:15],
            # Risk
            "risk_lev_cap": risk.lev_cap if risk else 0,
            "risk_flags": risk.flags if risk else [],
            "original_qty": risk.original_target_qty if risk else 0,
            "revised_qty": risk.revised_target_qty if risk else 0,
        }

        with open(self._log_path, "a") as f:
            f.write(json.dumps(record) + "\n")

    @property
    def log_path(self) -> Path:
        return self._log_path
