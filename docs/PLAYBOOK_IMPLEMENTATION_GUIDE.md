# Playbook Architecture Implementation Guide

**Companion to**: `PLAYBOOK_ARCHITECTURE.md`
**Purpose**: Concrete code examples and migration path
**Date**: 2026-03-15

---

## Phase 3: State Engine Foundation

### Step 1: New Domain Objects

Create `zenquant/domain/state.py`:

```python
"""Market state decomposition for playbook-driven strategy."""

from __future__ import annotations
from dataclasses import dataclass
from enum import Enum


class DirectionalState(str, Enum):
    """Primary trend direction."""
    UPTREND = "UPTREND"
    DOWNTREND = "DOWNTREND"
    NEUTRAL = "NEUTRAL"
    TRANSITION = "TRANSITION"


class VolatilityState(str, Enum):
    """Volatility regime classification."""
    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    DISTORTED = "DISTORTED"


class PositioningState(str, Enum):
    """Market positioning and crowding assessment."""
    HEALTHY = "HEALTHY"
    CROWDED_LONG = "CROWDED_LONG"
    CROWDED_SHORT = "CROWDED_SHORT"
    DELEVERAGING = "DELEVERAGING"
    UNCERTAIN = "UNCERTAIN"


class QualityState(str, Enum):
    """Data and market quality assessment."""
    TRADABLE = "TRADABLE"
    NOISY = "NOISY"
    DEGRADED = "DEGRADED"
    UNSAFE = "UNSAFE"


@dataclass(slots=True, frozen=True)
class MarketState:
    """Four-dimensional market state decomposition.

    Replaces single 'regime' string with orthogonal state dimensions.
    Each dimension has independent confidence score.
    """
    symbol: str
    timeframe: str  # "4h" or "1d"
    cutoff_time_ns: int

    # Four orthogonal state dimensions
    directional: DirectionalState
    volatility: VolatilityState
    positioning: PositioningState
    quality: QualityState

    # Confidence scores (0.0 - 1.0)
    directional_confidence: float
    volatility_confidence: float
    positioning_confidence: float
    quality_confidence: float

    # Backward compatibility: legacy regime field
    legacy_regime: str  # "TREND", "RANGE", or "STRESS"

    # State stability indicator
    transition_risk: float  # 0.0 = stable, 1.0 = about to flip

    def overall_confidence(self) -> float:
        """Geometric mean of all confidence scores."""
        product = (
            self.directional_confidence
            * self.volatility_confidence
            * self.positioning_confidence
            * self.quality_confidence
        )
        return product ** 0.25

    def is_tradable(self) -> bool:
        """Quick check if state allows new risk."""
        return (
            self.quality != QualityState.UNSAFE
            and self.volatility != VolatilityState.DISTORTED
            and self.overall_confidence() > 0.3
        )
```

### Step 2: State Classification Module

Create `zenquant/strategy/state_engine.py`:

```python
"""Market state classification engine."""

from __future__ import annotations
from typing import Any

from zenquant.domain import IndicatorPack, Timeframe
from zenquant.domain.state import (
    MarketState,
    DirectionalState,
    VolatilityState,
    PositioningState,
    QualityState,
)


class StateEngine:
    """Classify market state from indicator packs.

    Decomposes single 'regime' into four orthogonal dimensions:
    - Directional: trend direction
    - Volatility: vol regime
    - Positioning: crowding/stress
    - Quality: data quality
    """

    def __init__(self) -> None:
        # Track last state for hysteresis
        self._last_state: dict[tuple[str, str], MarketState] = {}

    def classify(self, pack: IndicatorPack) -> MarketState:
        """Classify one indicator pack into market state."""
        vals = self._extract_values(pack.values)
        key = (pack.symbol, pack.timeframe.value)
        last_state = self._last_state.get(key)

        # Classify each dimension independently
        directional, dir_conf = self._classify_directional(
            structure=vals["structure_state"],
            momentum_z=vals["momentum_roc_z"],
            cvd_slope=vals["cvd_slope"],
            last_directional=last_state.directional if last_state else None,
        )

        volatility, vol_conf = self._classify_volatility(
            realized_vol=vals["realized_vol"],
            atr=vals["atr"],
            last_volatility=last_state.volatility if last_state else None,
        )

        positioning, pos_conf = self._classify_positioning(
            funding_oi_z=vals["funding_oi_z"],
            last_positioning=last_state.positioning if last_state else None,
        )

        quality, qual_conf = self._classify_quality(
            quality_flags=pack.quality_flags,
        )

        # Compute legacy regime for backward compatibility
        legacy_regime = self._legacy_regime(directional, volatility, quality)

        # Estimate transition risk (simple version: low confidence = high transition risk)
        transition_risk = 1.0 - min(dir_conf, vol_conf, pos_conf, qual_conf)

        state = MarketState(
            symbol=pack.symbol,
            timeframe=pack.timeframe.value,
            cutoff_time_ns=pack.cutoff_time_ns,
            directional=directional,
            volatility=volatility,
            positioning=positioning,
            quality=quality,
            directional_confidence=dir_conf,
            volatility_confidence=vol_conf,
            positioning_confidence=pos_conf,
            quality_confidence=qual_conf,
            legacy_regime=legacy_regime,
            transition_risk=transition_risk,
        )

        self._last_state[key] = state
        return state

    @staticmethod
    def _classify_directional(
        structure: str,
        momentum_z: float,
        cvd_slope: float,
        last_directional: DirectionalState | None,
    ) -> tuple[DirectionalState, float]:
        """Classify directional state with hysteresis."""
        # Strong trend signals
        if structure == "UPTREND" and momentum_z > 0.5:
            return DirectionalState.UPTREND, min(1.0, 0.6 + momentum_z * 0.2)
        if structure == "DOWNTREND" and momentum_z < -0.5:
            return DirectionalState.DOWNTREND, min(1.0, 0.6 + abs(momentum_z) * 0.2)

        # Weak trend signals (use hysteresis)
        if structure == "UPTREND" and momentum_z > 0.2:
            if last_directional == DirectionalState.UPTREND:
                return DirectionalState.UPTREND, 0.5  # stay in trend
            return DirectionalState.TRANSITION, 0.4
        if structure == "DOWNTREND" and momentum_z < -0.2:
            if last_directional == DirectionalState.DOWNTREND:
                return DirectionalState.DOWNTREND, 0.5
            return DirectionalState.TRANSITION, 0.4

        # Neutral or conflicting signals
        if abs(momentum_z) < 0.2 and abs(cvd_slope) < 0.05:
            return DirectionalState.NEUTRAL, 0.6

        # Transition state (conflicting signals)
        return DirectionalState.TRANSITION, 0.3

    @staticmethod
    def _classify_volatility(
        realized_vol: float,
        atr: float,
        last_volatility: VolatilityState | None,
    ) -> tuple[VolatilityState, float]:
        """Classify volatility state with hysteresis."""
        # Distorted: extreme vol, unsafe to trade
        if realized_vol > 2.8:
            return VolatilityState.DISTORTED, 0.9

        # High: elevated but tradable
        if realized_vol > 1.5:
            return VolatilityState.HIGH, 0.7

        # Low: compressed, range-bound
        if realized_vol < 0.4:
            return VolatilityState.LOW, 0.7

        # Normal: typical vol regime
        return VolatilityState.NORMAL, 0.8

    @staticmethod
    def _classify_positioning(
        funding_oi_z: float,
        last_positioning: PositioningState | None,
    ) -> tuple[PositioningState, float]:
        """Classify positioning state (crowding, stress)."""
        # Extreme crowding
        if funding_oi_z > 2.5:
            return PositioningState.CROWDED_LONG, 0.8
        if funding_oi_z < -2.5:
            return PositioningState.CROWDED_SHORT, 0.8

        # Moderate crowding
        if funding_oi_z > 1.5:
            return PositioningState.CROWDED_LONG, 0.6
        if funding_oi_z < -1.5:
            return PositioningState.CROWDED_SHORT, 0.6

        # Healthy positioning
        if abs(funding_oi_z) < 1.0:
            return PositioningState.HEALTHY, 0.8

        # Uncertain (moderate but not extreme)
        return PositioningState.UNCERTAIN, 0.5

    @staticmethod
    def _classify_quality(
        quality_flags: list[str],
    ) -> tuple[QualityState, float]:
        """Classify data quality state."""
        if any(flag in {"VENUE_DOWN", "DATA_FUTURE"} for flag in quality_flags):
            return QualityState.UNSAFE, 0.9

        if any(flag in {"BOOK_STALE", "DATA_STALE"} for flag in quality_flags):
            return QualityState.DEGRADED, 0.8

        if quality_flags != ["OK"]:
            return QualityState.NOISY, 0.6

        return QualityState.TRADABLE, 1.0

    @staticmethod
    def _legacy_regime(
        directional: DirectionalState,
        volatility: VolatilityState,
        quality: QualityState,
    ) -> str:
        """Map new state to legacy regime for backward compatibility."""
        if quality == QualityState.UNSAFE or volatility == VolatilityState.DISTORTED:
            return "STRESS"

        if directional in {DirectionalState.UPTREND, DirectionalState.DOWNTREND}:
            return "TREND"

        return "RANGE"

    @staticmethod
    def _extract_values(values: dict[str, Any]) -> dict[str, Any]:
        """Extract and normalize indicator values."""
        return {
            "realized_vol": float(values.get("realized_vol", 0.0) or 0.0),
            "cvd_slope": float(values.get("cvd_slope", 0.0) or 0.0),
            "funding_oi_z": float(values.get("funding_oi_z", 0.0) or 0.0),
            "atr": float(values.get("atr", 0.0) or 0.0),
            "structure_state": str(
                values.get("structure_state") or values.get("swing_structure") or "NEUTRAL"
            ),
            "momentum_roc_z": float(values.get("momentum_roc_z", 0.0) or 0.0),
        }
```

### Step 3: Integrate into Strategy Engine

Modify `zenquant/strategy/engine.py`:

```python
# Add at top
from zenquant.strategy.state_engine import StateEngine
from zenquant.domain.state import MarketState

class ARCStrategyEngine:
    def __init__(self, bus: InMemoryJetStream, config: ARCStrategyConfig | None = None):
        # ... existing init ...

        # Phase 3: Add state engine
        self._state_engine = StateEngine()
        self._latest_state: dict[tuple[str, str], MarketState] = {}

    def _build_target(self, pack: IndicatorPack) -> TargetPosition:
        """Translate one indicator pack into a target position."""
        # Phase 3: Classify state first
        state = self._state_engine.classify(pack)
        self._latest_state[(pack.symbol, pack.timeframe.value)] = state

        # Publish state for dashboard
        self._bus.publish(
            f"strategy.state.{pack.timeframe.value}.{pack.symbol}",
            state,
        )

        # Use state.legacy_regime for backward compatibility
        regime = state.legacy_regime
        regime_confidence = state.overall_confidence()

        # ... rest of existing logic uses 'regime' and 'regime_confidence' ...
        # (no other changes needed for Phase 3)
```

### Step 4: Update Dashboard

Modify `zenquant/app/live_state.py`:

```python
# Add to DashboardStateStore
from zenquant.domain.state import MarketState

class DashboardStateStore:
    def __init__(self, config: DashboardStateConfig | None = None):
        # ... existing init ...
        self._market_state: dict[tuple[str, str], MarketState] = {}

    def on_market_state(self, state: MarketState) -> None:
        """Store latest market state for dashboard."""
        key = (state.symbol, state.timeframe)
        self._market_state[key] = state

    def snapshot(self, symbol: str | None = None, timeframe: str | None = None):
        # ... existing snapshot logic ...

        # Add market state to snapshot
        state = self._market_state.get((symbol, timeframe))
        snapshot["market_state"] = to_jsonable(state) if state else None

        return snapshot
```

Modify `zenquant/app/server.py`:

```python
# Add subscription in ZenQuantWebRuntime.__init__
self.bus.subscribe("strategy.state.>", self._on_market_state)

# Add handler
def _on_market_state(self, _: str, payload: object) -> None:
    from zenquant.domain.state import MarketState
    if isinstance(payload, MarketState):
        self.state.on_market_state(payload)
        self._schedule_state_broadcast()
```

### Step 5: Validation

Run backtest and verify:
1. Returns are identical to Phase 2 (state is richer, but strategy logic unchanged)
2. Dashboard shows 4-dimensional state
3. PostgreSQL logs state transitions

---

## Phase 4: Playbook Engine

### Step 1: Playbook Domain Objects

Add to `zenquant/domain/state.py`:

```python
class PlaybookID(str, Enum):
    """Available trading playbooks."""
    BULL_CONTINUATION = "BULL_CONTINUATION"
    BEAR_DEFENSE = "BEAR_DEFENSE"
    RANGE_LIGHT = "RANGE_LIGHT"
    CAPITAL_PRESERVATION = "CAPITAL_PRESERVATION"


@dataclass(slots=True, frozen=True)
class PlaybookSelection:
    """Active playbook and its configuration."""
    symbol: str
    timeframe: str
    cutoff_time_ns: int

    playbook_id: PlaybookID
    playbook_confidence: float  # how well state matches playbook trigger

    # Allowed actions in this playbook
    allowed_directions: list[float]  # [1.0, -1.0, 0.0] or [0.0] for reduce-only

    # Playbook-specific parameters
    max_leverage: float
    trailing_stop_atr_mult: float
    allow_pyramid: bool
    max_pyramid_count: int
    time_decay_bars: int | None  # exit after N bars if no profit

    # Reason for selection
    selection_reason: str
```

### Step 2: Playbook Selection Logic

Create `zenquant/strategy/playbook_engine.py`:

```python
"""Playbook selection based on market state."""

from zenquant.domain.state import (
    MarketState,
    PlaybookID,
    PlaybookSelection,
    DirectionalState,
    VolatilityState,
    PositioningState,
    QualityState,
)


class PlaybookEngine:
    """Select trading playbook based on market state."""

    def select(self, state: MarketState) -> PlaybookSelection:
        """Select playbook for current market state."""

        # Priority 1: Capital preservation in hostile states
        if self._should_preserve_capital(state):
            return self._capital_preservation_playbook(state)

        # Priority 2: Bull trend continuation
        if self._should_trade_bull(state):
            return self._bull_continuation_playbook(state)

        # Priority 3: Bear trend defense
        if self._should_trade_bear(state):
            return self._bear_defense_playbook(state)

        # Default: Range / mean-reversion light
        return self._range_light_playbook(state)

    @staticmethod
    def _should_preserve_capital(state: MarketState) -> bool:
        """Check if state requires capital preservation mode."""
        return (
            state.volatility == VolatilityState.DISTORTED
            or state.quality in {QualityState.DEGRADED, QualityState.UNSAFE}
            or state.positioning == PositioningState.DELEVERAGING
            or state.overall_confidence() < 0.25
        )

    @staticmethod
    def _should_trade_bull(state: MarketState) -> bool:
        """Check if state supports bull trend continuation."""
        return (
            state.directional == DirectionalState.UPTREND
            and state.volatility != VolatilityState.DISTORTED
            and state.positioning != PositioningState.CROWDED_LONG
            and state.quality in {QualityState.TRADABLE, QualityState.NOISY}
            and state.directional_confidence > 0.5
        )

    @staticmethod
    def _should_trade_bear(state: MarketState) -> bool:
        """Check if state supports bear trend defense."""
        return (
            state.directional == DirectionalState.DOWNTREND
            and state.volatility != VolatilityState.DISTORTED
            and state.positioning != PositioningState.CROWDED_SHORT
            and state.quality in {QualityState.TRADABLE, QualityState.NOISY}
            and state.directional_confidence > 0.5
        )

    @staticmethod
    def _bull_continuation_playbook(state: MarketState) -> PlaybookSelection:
        """Bull trend continuation playbook."""
        confidence = min(
            1.0,
            state.directional_confidence * 0.6
            + state.volatility_confidence * 0.2
            + state.positioning_confidence * 0.2,
        )

        return PlaybookSelection(
            symbol=state.symbol,
            timeframe=state.timeframe,
            cutoff_time_ns=state.cutoff_time_ns,
            playbook_id=PlaybookID.BULL_CONTINUATION,
            playbook_confidence=confidence,
            allowed_directions=[1.0, 0.0],  # long or flat only
            max_leverage=3.0,
            trailing_stop_atr_mult=3.0,  # wide stop
            allow_pyramid=True,
            max_pyramid_count=2,
            time_decay_bars=None,  # no time decay in trends
            selection_reason="UPTREND + healthy positioning + tradable quality",
        )

    @staticmethod
    def _bear_defense_playbook(state: MarketState) -> PlaybookSelection:
        """Bear trend defense playbook."""
        confidence = min(
            1.0,
            state.directional_confidence * 0.6
            + state.volatility_confidence * 0.2
            + state.positioning_confidence * 0.2,
        )

        return PlaybookSelection(
            symbol=state.symbol,
            timeframe=state.timeframe,
            cutoff_time_ns=state.cutoff_time_ns,
            playbook_id=PlaybookID.BEAR_DEFENSE,
            playbook_confidence=confidence,
            allowed_directions=[-1.0, 0.0],  # short or flat only
            max_leverage=2.0,  # more conservative
            trailing_stop_atr_mult=2.0,  # tighter stop
            allow_pyramid=False,
            max_pyramid_count=0,
            time_decay_bars=None,
            selection_reason="DOWNTREND + healthy positioning + tradable quality",
        )

    @staticmethod
    def _range_light_playbook(state: MarketState) -> PlaybookSelection:
        """Range / mean-reversion light playbook."""
        confidence = min(
            1.0,
            (1.0 - state.directional_confidence) * 0.5  # prefer low directional confidence
            + state.volatility_confidence * 0.3
            + state.quality_confidence * 0.2,
        )

        return PlaybookSelection(
            symbol=state.symbol,
            timeframe=state.timeframe,
            cutoff_time_ns=state.cutoff_time_ns,
            playbook_id=PlaybookID.RANGE_LIGHT,
            playbook_confidence=confidence,
            allowed_directions=[1.0, -1.0, 0.0],  # both directions
            max_leverage=1.5,  # small size
            trailing_stop_atr_mult=1.5,  # tight stop
            allow_pyramid=False,
            max_pyramid_count=0,
            time_decay_bars=3,  # exit after 3 bars if no profit
            selection_reason="NEUTRAL direction + low vol + tradable quality",
        )

    @staticmethod
    def _capital_preservation_playbook(state: MarketState) -> PlaybookSelection:
        """Capital preservation playbook (reduce-only)."""
        return PlaybookSelection(
            symbol=state.symbol,
            timeframe=state.timeframe,
            cutoff_time_ns=state.cutoff_time_ns,
            playbook_id=PlaybookID.CAPITAL_PRESERVATION,
            playbook_confidence=0.9,  # high confidence in preservation mode
            allowed_directions=[0.0],  # flat only (reduce-only)
            max_leverage=0.35,  # minimal exposure
            trailing_stop_atr_mult=1.5,  # tight stop
            allow_pyramid=False,
            max_pyramid_count=0,
            time_decay_bars=1,  # exit immediately
            selection_reason=f"STRESS state: vol={state.volatility}, quality={state.quality}",
        )
```

### Step 3: Integrate into Strategy

Modify `zenquant/strategy/engine.py`:

```python
from zenquant.strategy.playbook_engine import PlaybookEngine

class ARCStrategyEngine:
    def __init__(self, ...):
        # ... existing init ...
        self._playbook_engine = PlaybookEngine()

    def _build_target(self, pack: IndicatorPack) -> TargetPosition:
        # Phase 3: Classify state
        state = self._state_engine.classify(pack)

        # Phase 4: Select playbook
        playbook = self._playbook_engine.select(state)

        # Publish playbook for dashboard
        self._bus.publish(
            f"strategy.playbook.{pack.timeframe.value}.{pack.symbol}",
            playbook,
        )

        # Use playbook parameters instead of hardcoded config
        max_leverage = playbook.max_leverage
        trailing_stop_mult = playbook.trailing_stop_atr_mult

        # Check if direction is allowed by playbook
        direction = self._direction(...)  # existing logic
        if direction not in playbook.allowed_directions:
            direction = 0.0
            reason_codes.append("PLAYBOOK_DIRECTION_BLOCK")

        # ... rest of sizing logic uses playbook.max_leverage ...
```

---

## Phase 5-6: Sizing and Exit Engines

(Similar pattern: extract into separate modules, integrate via strategy engine)

---

## Migration Checklist

### Phase 3 (State Engine)
- [ ] Create `zenquant/domain/state.py` with `MarketState`
- [ ] Create `zenquant/strategy/state_engine.py` with `StateEngine`
- [ ] Integrate into `ARCStrategyEngine._build_target()`
- [ ] Update dashboard to show 4D state
- [ ] Add PostgreSQL persistence for `MarketState`
- [ ] Run backtest validation (returns should match Phase 2)
- [ ] Update tests

### Phase 4 (Playbook Engine)
- [ ] Add `PlaybookSelection` to `zenquant/domain/state.py`
- [ ] Create `zenquant/strategy/playbook_engine.py`
- [ ] Integrate into `ARCStrategyEngine`
- [ ] Update dashboard to show active playbook
- [ ] Add PostgreSQL persistence for `PlaybookSelection`
- [ ] Run backtest validation (returns should improve)
- [ ] Update tests

### Phase 5 (Sizing Engine)
- [ ] Create `zenquant/strategy/sizing_engine.py`
- [ ] Extract sizing logic from `_build_target()`
- [ ] Add stress discounts for vol/positioning/quality
- [ ] Update dashboard to show sizing breakdown
- [ ] Run backtest validation

### Phase 6 (Exit Engine)
- [ ] Create `zenquant/strategy/exit_engine.py`
- [ ] Implement 4 exit types
- [ ] Make trailing stop playbook-aware
- [ ] Add time decay exits
- [ ] Update dashboard to show exit logic
- [ ] Run backtest validation

---

## Testing Strategy

### Unit Tests
- Test each state classifier independently
- Test playbook selection logic with synthetic states
- Test sizing components in isolation
- Test exit triggers with mock positions

### Integration Tests
- Test full pipeline: indicator → state → playbook → sizing → exit → target
- Verify backward compatibility (legacy regime field)
- Verify determinism (same inputs → same outputs)

### Backtest Validation
- Compare Phase 3 vs Phase 2 (should be identical)
- Compare Phase 4 vs Phase 3 (should improve)
- Compare Phase 5 vs Phase 4 (should improve risk-adjusted returns)
- Compare Phase 6 vs Phase 5 (should improve profit factor)

### Replay Validation
- Run historical replay with new architecture
- Verify state transitions match expectations
- Verify playbook switches are logical
- Verify no lookahead or future leakage

---

**End of Implementation Guide**
