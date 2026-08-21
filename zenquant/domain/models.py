from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class Timeframe(StrEnum):
    TF_5M = "5m"
    TF_1H = "1h"
    TF_4H = "4h"
    TF_1D = "1d"

    @property
    def seconds(self) -> int:
        return {"5m": 300, "1h": 3600, "4h": 14400, "1d": 86400}[self.value]


@dataclass(slots=True, frozen=True)
class EventMeta:
    event_time_ns: int
    ingest_time_ns: int
    venue: str
    symbol: str
    stream_seq: int
    schema_version: str = "v1"


@dataclass(slots=True, frozen=True)
class OrderBookLevel:
    price: float
    quantity: float


@dataclass(slots=True)
class TradeTick:
    meta: EventMeta
    price: float
    size: float
    is_buyer_aggressor: bool | None


@dataclass(slots=True)
class OrderBookSnapshot:
    meta: EventMeta
    bids: list[OrderBookLevel]
    asks: list[OrderBookLevel]
    is_snapshot: bool = True


@dataclass(slots=True)
class FundingUpdate:
    meta: EventMeta
    funding_rate: float


@dataclass(slots=True)
class MarkPriceUpdate:
    meta: EventMeta
    mark_price: float
    index_price: float | None = None
    next_funding_time_ns: int | None = None


@dataclass(slots=True)
class OpenInterestUpdate:
    meta: EventMeta
    open_interest: float


@dataclass(slots=True)
class BarCloseEvent:
    timeframe: Timeframe
    symbol: str
    bar_start_ns: int
    bar_end_ns: int


@dataclass(slots=True)
class Candle:
    symbol: str
    timeframe: Timeframe
    bar_start_ns: int
    bar_end_ns: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    trade_count: int


@dataclass(slots=True)
class MarketSnapshot:
    symbol: str
    timeframe: Timeframe
    cutoff_time_ns: int
    last_price: float
    bid_depth: float
    ask_depth: float
    book_age_ms: int
    venue_count: int
    cvd_delta: float
    funding_oi_weighted: float | None
    oi_coverage: float
    staleness_ms: int
    quality_flags: list[str]
    vpin: float = 0.0  # Volume-synchronized probability of informed trading
    large_trade_count: int = 0  # Trades > $100k in this bar


@dataclass(slots=True)
class IndicatorCoverage:
    venue_count: int
    oi_coverage: float
    staleness_ms: int


@dataclass(slots=True)
class IndicatorPack:
    ts_ns: int
    symbol: str
    timeframe: Timeframe
    cutoff_time_ns: int
    indicator_version: str
    formula_hash: str
    quality_flags: list[str]
    coverage: IndicatorCoverage
    values: dict[str, Any]
    components: dict[str, float] = field(default_factory=dict)


@dataclass(slots=True)
class TargetPosition:
    intent_id: str
    portfolio: str
    symbol: str
    timeframe: Timeframe
    cutoff_time_ns: int
    target_qty: float
    target_leverage: float
    reduce_only: bool
    regime: str
    regime_confidence: float
    reason_codes: list[str]
    realized_vol: float = 0.0  # execution layer uses for adaptive speed
    atr: float = 0.0  # DCA distance gating


@dataclass(slots=True, frozen=True)
class DecisionSnapshot:
    """Immutable V2 audit record of an approved four-hour target position."""

    decision_id: str
    portfolio: str
    symbol: str
    timeframe: Timeframe
    cutoff_time_ns: int
    formula_hash: str
    config_hash: str
    target_qty: float
    target_leverage: float
    reduce_only: bool
    regime: str
    regime_confidence: float
    reason_codes: tuple[str, ...]
    realized_vol: float
    atr: float
    indicator_version: str
    data_identity: str
    alpha_direction: float
    alpha_raw_score: float
    alpha_confidence: float
    directional_state: str
    volatility_state: str
    positioning_state: str
    quality_state: str
    regime_risk_multiplier: float
    annualized_volatility: float
    volatility_target: float
    exposure_cap: float
    quality_flags: tuple[str, ...]
    options_state_id: str | None


@dataclass(slots=True, frozen=True)
class ArchitectureAlphaSnapshot:
    """Immutable pre-position four-hour alpha used by causal attribution."""

    alpha_id: str
    symbol: str
    timeframe: Timeframe
    cutoff_time_ns: int
    formula_hash: str
    indicator_version: str
    alpha_config_hash: str
    raw_score: float
    confidence: float
    direction: float
    regime: str
    direction_plugin_names: tuple[str, ...]


@dataclass(slots=True)
class IntentReason:
    portfolio: str
    symbol: str
    cutoff_time_ns: int
    summary: str
    reason_codes: list[str]


@dataclass(slots=True)
class RiskOverlayDecision:
    symbol: str
    cutoff_time_ns: int
    original_target_qty: float
    revised_target_qty: float
    lev_cap: float
    reduce_only: bool
    flags: list[str]


@dataclass(slots=True)
class RiskSnapshot:
    portfolio: str
    ts_ns: int
    equity: float
    high_water_mark: float
    drawdown: float
    lev_cap: float
    reduce_only_mode: bool
    flags: list[str]


@dataclass(slots=True)
class ExecutionOrder:
    client_order_id: str
    symbol: str
    side: int
    quantity: float
    reduce_only: bool
    max_slippage_bps: float
    order_type: str = "MARKET"  # MARKET or LIMIT
    limit_price: float | None = None  # Required for LIMIT orders


@dataclass(slots=True, frozen=True)
class ExecutionOrderAudit:
    """Immutable audit record for one target-tracking V2 order attempt."""

    decision_id: str
    client_order_id: str
    symbol: str
    cutoff_time_ns: int
    approved_target_qty: float
    position_before_order: float
    position_after_fill: float
    order_delta_qty: float
    hard_safety_reason: str | None
    status: str
    remaining_target_delta_qty: float = 0.0


@dataclass(slots=True)
class ExecutionReport:
    client_order_id: str
    symbol: str
    side: int
    status: str
    filled_qty: float
    avg_price: float
    fee: float
    transaction_time_ns: int
    reject_reason: str = ""


@dataclass(slots=True, frozen=True)
class PositionEpisode:
    """Deterministic flat-to-flat (or flip-to-zero) position record."""

    episode_id: str
    symbol: str
    side: int
    opened_at_ns: int
    closed_at_ns: int | None
    entry_price: float
    exit_price: float | None
    max_abs_qty: float
    remaining_qty: float
    gross_realized_pnl: float
    fees: float
    funding_pnl: float
    net_pnl: float
    mfe_pnl: float
    mae_pnl: float
    unrealized_pnl: float
    fill_count: int
    closed: bool
    add_count: int = 0
    reduce_count: int = 0
    holding_duration_ns: int = 0


# ---------------------------------------------------------------------------
# Phase 2 domain types
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class SwingPoint:
    """A confirmed swing high or swing low with its bar timestamp."""

    bar_end_ns: int
    price: float
    is_high: bool  # True = swing high, False = swing low


@dataclass(slots=True)
class SwingStructure:
    """
    Price-structure state computed at each bar close.

    structure_state:
      - UPTREND   : confirmed HH + HL sequence
      - DOWNTREND : confirmed LH + LL sequence
      - NEUTRAL   : mixed or insufficient data
    """

    symbol: str
    timeframe: Timeframe
    cutoff_time_ns: int
    structure_state: str  # UPTREND | DOWNTREND | NEUTRAL
    last_swing_high: float  # price level for stop placement
    last_swing_low: float  # price level for stop placement
    structure_break: bool  # True on the bar where structure flips
    bars_since_break: int  # bars since last structure change
    pivot_lookback: int  # N used for pivot detection


@dataclass(slots=True)
class OpenPositionState:
    """
    Stateful tracking of an open position inside ARCStrategyEngine.
    Preserved across bars — this is the core Phase 2 addition.
    """

    symbol: str
    timeframe: Timeframe
    direction: float  # +1.0 = long, -1.0 = short
    entry_price: float
    entry_bar_ns: int
    base_qty: float  # original entry qty
    current_qty: float  # after pyramids/partial exits
    trailing_stop: float  # current stop price (absolute)
    pyramid_count: int  # how many adds have been made (0 = initial only)
    bars_held: int  # bars since entry
    peak_pnl_pct: float = 0.0  # peak unrealized PnL as fraction of entry notional


@dataclass(slots=True)
class DirectionalBias:
    """
    1D-timeframe directional bias published per symbol.
    4H strategy reads this before computing its own TargetPosition.
    """

    symbol: str
    cutoff_time_ns: int
    direction: float  # +1.0 LONG | -1.0 SHORT | 0.0 FLAT
    regime: str
    confidence: float
    quality_flags: list[str] = field(default_factory=list)


@dataclass(slots=True)
class MarketState:
    """Four-dimensional market state emitted on confirmed bar close.

    Decomposes single 'regime' into orthogonal state dimensions for clearer
    playbook selection and risk management. Each dimension has independent
    confidence score.

    Attributes:
        symbol: Trading symbol (e.g., "BTCUSDT_PERP")
        timeframe: Bar timeframe (4h or 1d)
        cutoff_time_ns: Point-in-time cutoff for this state
        directional_state: Trend direction (UPTREND | DOWNTREND | NEUTRAL | TRANSITION)
        directional_confidence: Confidence in directional classification (0.0-1.0)
        volatility_state: Vol regime (LOW | NORMAL | HIGH | DISTORTED)
        volatility_confidence: Confidence in volatility classification (0.0-1.0)
        positioning_state: Crowding assessment (HEALTHY | CROWDED_LONG | CROWDED_SHORT | UNCERTAIN)
        positioning_confidence: Confidence in positioning classification (0.0-1.0)
        quality_state: Data quality (TRADABLE | NOISY | DEGRADED | UNSAFE)
        quality_confidence: Confidence in quality classification (0.0-1.0)
        regime: Legacy regime field for backward compatibility (TREND | RANGE | STRESS)
        regime_confidence: Overall regime confidence
        direction_signal: Numeric direction signal (1.0 = long, -1.0 = short, 0.0 = flat)
        transition_risk: State instability indicator (0.0 = stable, 1.0 = about to flip)
        quality_flags: Raw quality flags from indicator pack
        reason_codes: Human-readable state classification reasons
    """

    symbol: str
    timeframe: Timeframe
    cutoff_time_ns: int
    directional_state: str
    directional_confidence: float
    volatility_state: str
    volatility_confidence: float
    positioning_state: str
    positioning_confidence: float
    quality_state: str
    quality_confidence: float
    regime: str
    regime_confidence: float
    direction_signal: float
    transition_risk: float = 0.0
    quality_flags: list[str] = field(default_factory=list)
    reason_codes: list[str] = field(default_factory=list)

    def overall_confidence(self) -> float:
        """Geometric mean of all dimension confidences."""
        product = (
            self.directional_confidence
            * self.volatility_confidence
            * self.positioning_confidence
            * self.quality_confidence
        )
        return product**0.25

    def is_tradable(self) -> bool:
        """Quick check if state allows new risk."""
        return (
            self.quality_state != "UNSAFE" and self.volatility_state != "DISTORTED" and self.overall_confidence() > 0.3
        )


@dataclass(slots=True)
class PlaybookSelection:
    """Active trading playbook selected from the current ``MarketState``.

    This object is the Phase 4 bridge between state classification and target
    construction. It keeps strategy routing explicit and auditable without
    embedding playbook rules into the UI or execution layers.
    """

    symbol: str
    timeframe: Timeframe
    cutoff_time_ns: int
    playbook_id: str
    playbook_confidence: float
    allowed_directions: list[float]
    max_leverage: float
    trailing_stop_atr_mult: float
    allow_pyramid: bool
    max_pyramid_count: int
    time_decay_bars: int | None
    selection_reason: str
    reason_codes: list[str] = field(default_factory=list)

    def allows_direction(self, direction: float) -> bool:
        """Return whether the playbook permits this directional stance."""
        return any(abs(allowed - direction) < 1e-9 for allowed in self.allowed_directions)

    def is_reduce_only(self) -> bool:
        """Capital-preservation playbooks only allow flattening exposure."""
        return all(abs(direction) < 1e-9 for direction in self.allowed_directions)


@dataclass(slots=True)
class SizingDecision:
    """Explicit leverage-sizing breakdown used by the strategy engine.

    Phase 5 extracts sizing out of ``ARCStrategyEngine`` so leverage decisions
    can be tested and audited independently from entry and exit logic.
    """

    symbol: str
    timeframe: Timeframe
    cutoff_time_ns: int
    base_size: float
    confidence_multiplier: float
    stress_discount: float
    execution_haircut: float
    final_leverage: float
    reason_codes: list[str] = field(default_factory=list)

    def breakdown(self) -> dict[str, float]:
        """Return the multiplicative sizing components for display/audit."""
        return {
            "base_size": self.base_size,
            "confidence_multiplier": self.confidence_multiplier,
            "stress_discount": self.stress_discount,
            "execution_haircut": self.execution_haircut,
            "final_leverage": self.final_leverage,
        }


@dataclass(slots=True)
class ExitDecision:
    """Exit logic decision extracted from the strategy engine.

    Phase 6 separates exit logic into an auditable, testable module so the
    strategy engine no longer hides exits inside target construction.
    """

    symbol: str
    timeframe: Timeframe
    cutoff_time_ns: int
    exit_type: str
    exit_urgency: str
    target_reduction_pct: float
    stop_price: float | None
    reason_code: str
    reason_detail: str
    reason_codes: list[str] = field(default_factory=list)

    def should_exit(self) -> bool:
        """Quick check for whether any exit action is requested."""
        return self.exit_type != "NONE"

    def is_immediate(self) -> bool:
        """Return whether the exit should flatten immediately."""
        return self.exit_urgency == "IMMEDIATE"


@dataclass(slots=True)
class DeribitRiskPack:
    """Deribit options market indicators used as a risk thermometer.

    This pack is observation-first. It is meant to inform operators and future
    risk-layer enhancements without becoming a primary directional signal.
    """

    symbol: str
    ts_ns: int
    atm_iv_30d: float
    term_structure_slope: float
    put_call_skew_25d: float
    oi_concentration: float
    block_flow_imbalance: float
    quality_flags: list[str] = field(default_factory=list)


@dataclass(slots=True)
class BacktestMetrics:
    """Summary metrics from a single backtest window."""

    symbol: str
    start_ns: int
    end_ns: int
    total_bars: int
    net_pnl: float
    gross_pnl: float
    total_fees: float
    total_funding: float
    sharpe: float
    sortino: float
    max_drawdown: float
    max_dd_duration_bars: int
    win_rate: float
    profit_factor: float
    avg_win: float
    avg_loss: float
    trade_count: int
    turnover_per_day: float
    fee_drag_pct: float  # fees / gross_pnl
    action_trade_counts: dict[str, int] = field(default_factory=dict)
    action_fees: dict[str, float] = field(default_factory=dict)
    action_fee_drag_pct: dict[str, float] = field(default_factory=dict)
    action_realized_counts: dict[str, int] = field(default_factory=dict)
    action_realized_pnl: dict[str, float] = field(default_factory=dict)
    action_profit_factor: dict[str, float] = field(default_factory=dict)
    episode_profit_factor: float | None = None
    episode_profit_factor_status: str = "INSUFFICIENT_SAMPLE"
    closed_episode_count: int = 0
    open_episode_count: int = 0
    position_switch_count: int = 0
    position_switches_per_day: float = 0.0
    executor_created_exposure: int = 0
    unexpected_reversal: int = 0
    target_overshoot: int = 0


@dataclass(slots=True)
class BacktestCycleContext:
    """Cycle-phase context for interpreting BTC backtest metrics."""

    dominant_cycle: int
    dominant_phase: str
    cycle_distribution: dict[str, float]
    phase_distribution: dict[str, float]
    phase_bar_counts: dict[str, int]
    sharpe_by_phase: dict[str, float]


@dataclass(slots=True)
class BacktestDiagnostics:
    """Compact attribution and audit counts from one replay run."""

    playbook_counts: dict[str, int]
    actionable_exit_counts: dict[str, int]
    risk_flag_counts: dict[str, int]
    top_target_reason_codes: dict[str, int]
