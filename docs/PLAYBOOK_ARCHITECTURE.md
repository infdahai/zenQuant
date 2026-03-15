# Playbook-Driven State Architecture for ZenQuant

**Status**: Design Proposal
**Target**: Phase 3+ Evolution
**Author**: Technical Co-Founder (AI Agent)
**Date**: 2026-03-15

---

## Executive Summary

This document proposes upgrading ZenQuant from a **feature-driven strategy** to a **playbook-driven state architecture**. The current system (Phase 2) correctly implements deterministic bar-close decisions, multi-timeframe coordination, and trailing stops. However, it lacks explicit **state abstraction** and **playbook selection** — the two layers that separate profitable medium-term systems from indicator aggregators.

**Core Thesis**: For 4H/1D trading, edge comes not from heavier AI models, but from:
1. Correct **state identification** (what is the market doing?)
2. Disciplined **playbook switching** (which trading logic applies now?)
3. Mature **sizing functions** (how much exposure is appropriate?)
4. Professional **exit systems** (when and how to reduce/close?)

This architecture preserves all existing ZenQuant contracts (determinism, auditability, replay parity, risk veto) while adding the missing strategic layer.

---

## 1. Current Architecture Assessment

### What ZenQuant Does Well (Phase 2)

✅ **Deterministic bar-close decisions** — no lookahead, point-in-time correctness
✅ **Intent-before-orders** — strategy emits target, risk can rewrite, execution handles fills
✅ **Multi-timeframe coordination** — 1D bias filters 4H execution
✅ **Trailing stops** — position-aware exit logic
✅ **Regime classification** — TREND/RANGE/STRESS with confidence
✅ **Risk overlay** — drawdown throttle, crowding discount, quality degradation
✅ **Audit trail** — PostgreSQL persistence, dashboard explainability

### What's Missing

❌ **Explicit state decomposition** — regime is a single string, not orthogonal dimensions
❌ **Playbook abstraction** — no clear "in this state, use this trading logic" layer
❌ **Sizing engine** — sizing is inline calculation, not a composable function
❌ **Exit engine** — trailing stop is one exit type, but no unified exit framework
❌ **State confidence** — regime confidence exists, but state stability/transition warnings don't

**Result**: Adding more features (funding/OI, options, microstructure) will increase complexity without necessarily increasing edge. The system needs **better state abstraction** before it needs **more signals**.

---

## 2. Proposed Architecture: Four-Layer State System

### Layer 1: State Engine

**Purpose**: Answer "What is the market doing right now?"

Instead of a single `regime` field, decompose market state into **four orthogonal dimensions**:

#### 1.1 Directional State
- **Values**: `UPTREND`, `DOWNTREND`, `NEUTRAL`, `TRANSITION`
- **Inputs**: 1D/4H structure, momentum persistence, bars since break
- **Current mapping**: Already computed in `DirectionalBias.direction` and `structure_state`

#### 1.2 Volatility State
- **Values**: `LOW`, `NORMAL`, `HIGH`, `DISTORTED`
- **Inputs**: realized vol percentile, ATR percentile, vol expansion ratio, vol shock flags
- **Current mapping**: Partially in `realized_vol > 2.8` stress check

#### 1.3 Positioning State
- **Values**: `HEALTHY`, `CROWDED_LONG`, `CROWDED_SHORT`, `DELEVERAGING`, `UNCERTAIN`
- **Inputs**: funding level, funding momentum, OI-price divergence (Phase 3)
- **Current mapping**: Partially in `funding_oi_z` crowding discount

#### 1.4 Market Quality State
- **Values**: `TRADABLE`, `NOISY`, `DEGRADED`, `UNSAFE`
- **Inputs**: data quality flags, spread proxy, venue health
- **Current mapping**: Already in `pack.quality_flags`

**Output**: A `MarketState` domain object with all four dimensions + confidence scores.

**Why orthogonal?** Because volatility can be high in both uptrends and downtrends. Positioning can be crowded in both bull and bear. Quality can degrade in any regime. Collapsing these into one `regime` string loses critical information.

---

### Layer 2: Playbook Engine

**Purpose**: Answer "Which trading logic applies in this state?"

Define **4 core playbooks** (extensible later):

#### Playbook 1: Bull Trend Continuation
- **Trigger**: `directional_state=UPTREND`, `volatility_state≠DISTORTED`, `positioning_state≠CROWDED_LONG`
- **Logic**:
  - Pullback continuation on 4H
  - Breakout follow-through
  - Wide trailing stop (3.0x ATR)
  - Allow pyramid (up to 2 adds)
  - Higher base leverage (up to 3.0x)

#### Playbook 2: Bear Trend Defense
- **Trigger**: `directional_state=DOWNTREND`, `volatility_state≠DISTORTED`, `positioning_state≠CROWDED_SHORT`
- **Logic**:
  - Tactical short on structure break
  - Tighter trailing stop (2.0x ATR)
  - No pyramid
  - Lower base leverage (up to 2.0x)
  - Faster de-risk on reversal signals

#### Playbook 3: Range / Mean-Reversion Light
- **Trigger**: `directional_state=NEUTRAL`, `volatility_state∈{LOW,NORMAL}`, `quality_state=TRADABLE`
- **Logic**:
  - Liquidity imbalance fade (current `ob_liq_z` logic)
  - Smaller size (max 1.5x leverage)
  - Shorter holding period (exit after 2-3 bars if no follow-through)
  - High selectivity (only trade when `ob_liq_z > 1.5`)

#### Playbook 4: Capital Preservation
- **Trigger**: `volatility_state=DISTORTED` OR `quality_state∈{DEGRADED,UNSAFE}` OR `positioning_state=DELEVERAGING`
- **Logic**:
  - Reduce-only mode
  - Size down to 0.35x max leverage
  - No new entries
  - Tight stops (1.5x ATR)
  - Protect equity first

**Output**: A `PlaybookSelection` object with:
- `playbook_id`: which playbook is active
- `playbook_confidence`: how well current state matches playbook trigger
- `allowed_actions`: `["LONG", "SHORT", "FLAT"]` or `["REDUCE_ONLY"]`
- `exit_profile`: which exit logic to use

**Why playbooks?** Because the same indicator values should produce **different actions** depending on market state. A breakout in a bull trend is a continuation signal. A breakout in a range is a false-breakout trap. Playbooks encode this context-dependent logic explicitly.

---

### Layer 3: Sizing Engine

**Purpose**: Answer "How much exposure is appropriate?"

Current sizing is inline:
```python
vol_budget = target_vol / max(realized_vol, target_vol * 0.25)
crowding_discount = max(0.35, 1.0 - min(0.65, abs(funding_oi_z) / 4.0))
target_leverage = min(max_leverage, vol_budget * regime_confidence * crowding_discount)
```

**Proposed**: Extract into a composable function with explicit components:

```python
final_position_size = (
    base_size              # from vol targeting + equity
    × confidence_multiplier  # from state alignment + playbook confidence
    × stress_discount       # from positioning/volatility/quality stress
    × execution_haircut     # from market quality (future: microstructure)
)
```

#### 3.1 Base Size
- **Inputs**: target volatility, portfolio equity, asset risk budget
- **Current**: `vol_budget * equity`

#### 3.2 Confidence Multiplier
- **Inputs**: state alignment (all 4 dimensions agree?), playbook confidence, 1D/4H agreement
- **Current**: `regime_confidence`
- **Enhancement**: Multiply by `directional_state_confidence × volatility_state_confidence`

#### 3.3 Stress Discount
- **Inputs**: funding momentum extreme, OI-price divergence, volatility stress, drawdown throttle
- **Current**: `crowding_discount`
- **Enhancement**: Add `volatility_stress_discount` and `positioning_stress_discount`

#### 3.4 Execution Haircut
- **Inputs**: market quality, liquidity condition, microstructure warning (Phase 8)
- **Current**: Implicit in `quality_flags` → `STRESS` regime
- **Enhancement**: Explicit 0.7x haircut when `quality_state=NOISY`, 0.0x when `UNSAFE`

**Output**: A `SizingDecision` object with all components visible for dashboard explainability.

---

### Layer 4: Exit Engine

**Purpose**: Answer "When and how should this position reduce/close?"

Current exit logic:
- Trailing stop (Phase 2) ✅
- Regime flip (implicit in target change) ⚠️
- No time decay ❌
- No invalidation exit ❌

**Proposed**: Four exit types, checked in order:

#### 4.1 Invalidation Exit
- **Trigger**: Original hypothesis is false
- **Examples**:
  - Bull playbook, but 1D structure breaks down
  - Entered on breakout, but price closes back inside range
- **Action**: Immediate exit, no trailing

#### 4.2 Trailing Exit
- **Trigger**: Protect profit in winning positions
- **Current**: Already implemented ✅
- **Enhancement**: Playbook-specific ATR multipliers (bull=3.0x, bear=2.0x, range=1.5x)

#### 4.3 Time Decay Exit
- **Trigger**: Position held too long without realizing edge
- **Examples**:
  - Range playbook: exit after 3 bars if no profit
  - Trend playbook: exit after 12 bars if no new high/low
- **Action**: Gradual reduction (50% → 100%)

#### 4.4 Regime Exit
- **Trigger**: Market state changes, playbook no longer valid
- **Examples**:
  - Bull playbook active, but `volatility_state` → `DISTORTED`
  - Range playbook active, but `directional_state` → `UPTREND`
- **Action**: Reduce to playbook-appropriate size (may not be zero)

**Output**: An `ExitDecision` object with:
- `exit_type`: which exit triggered (or `NONE`)
- `exit_urgency`: `IMMEDIATE`, `GRADUAL`, `TRAILING`
- `target_reduction_pct`: how much to reduce (0-100%)

**Why separate exit engine?** Because exits are **more important than entries** for medium-term systems. A good entry with a bad exit loses money. A mediocre entry with a disciplined exit can still profit. Making exits first-class improves both returns and explainability.

---

## 3. Integration with Existing ZenQuant Contracts

### 3.1 Determinism Preserved
- All state/playbook/sizing/exit logic operates on **confirmed bar-close data only**
- No lookahead, no future leakage
- State transitions are pure functions of `IndicatorPack` + prior state

### 3.2 Intent-Before-Orders Preserved
- Playbook engine emits `PlaybookSelection`
- Sizing engine emits `SizingDecision`
- Exit engine emits `ExitDecision`
- Strategy engine combines these into `TargetPosition` (existing contract)
- Risk overlay can still rewrite (existing contract)
- Execution kernel handles fills (existing contract)

### 3.3 Replay Parity Preserved
- All new state is stored in `IndicatorPack.values` or separate domain objects
- No hidden mutable state
- Backtest/replay/live use same code paths

### 3.4 Auditability Enhanced
- Dashboard can now show:
  - Current state (4 dimensions)
  - Active playbook + confidence
  - Sizing breakdown (base × confidence × stress × haircut)
  - Active exit logic + trigger levels
- PostgreSQL persistence extended to new domain objects

---

## 4. Phase 3+ Roadmap

### Phase 3: State Engine Foundation
**Goal**: Decompose `regime` into 4-dimensional state

**Tasks**:
1. Create `MarketState` domain object with 4 dimensions
2. Refactor `_classify_regime()` into `_classify_directional_state()`, `_classify_volatility_state()`, etc.
3. Update `IndicatorPack` to include state dimensions (or emit separate `StateUpdate` event)
4. Update dashboard to show 4-dimensional state
5. **Do not change strategy logic yet** — just expose richer state

**Validation**: Backtest returns should be identical (state is richer, but strategy still uses old logic)

### Phase 4: Playbook Engine
**Goal**: Explicit playbook selection layer

**Tasks**:
1. Create `PlaybookSelection` domain object
2. Implement 4 core playbooks (Bull/Bear/Range/Preservation)
3. Add playbook selection logic based on `MarketState`
4. Update strategy engine to route through playbook layer
5. Update dashboard to show active playbook + confidence

**Validation**: Backtest with playbook routing should match or exceed Phase 3 (better state → better decisions)

### Phase 5: Sizing Engine Refactor
**Goal**: Composable sizing function

**Tasks**:
1. Create `SizingDecision` domain object
2. Extract sizing into separate module with explicit components
3. Add stress discounts for volatility/positioning/quality
4. Update dashboard to show sizing breakdown

**Validation**: Backtest should show improved risk-adjusted returns (better sizing in stress states)

### Phase 6: Exit Engine Upgrade
**Goal**: Unified exit framework

**Tasks**:
1. Create `ExitDecision` domain object
2. Implement 4 exit types (invalidation, trailing, time, regime)
3. Make trailing stop playbook-aware (different ATR multipliers)
4. Add time decay exits for range playbook
5. Update dashboard to show active exit logic

**Validation**: Backtest should show higher profit factor (better exits → better asymmetry)

### Phase 7: Funding/OI Integration (Original Phase 3)
**Goal**: Feed positioning signals into `PositioningState`

**Tasks**:
1. Add funding/OI features to `IndicatorPack`
2. Use them in `_classify_positioning_state()`
3. Let positioning state influence playbook selection and stress discount
4. **Do not add funding/OI as direct direction signals** — use them for state classification only

**Validation**: Backtest should show better drawdown control (avoid crowded trades)

### Phase 8: Optional ML for State Estimation
**Goal**: Improve state classification confidence, not replace strategy

**Tasks**:
1. Train HMM or small classifier on historical state transitions
2. Use ML output as **confidence adjustment** for state engine
3. Use ML for **instability detection** (state about to flip?)
4. **Do not let ML directly emit direction or leverage**

**Validation**: Backtest should show smoother state transitions, fewer whipsaws

---

## 5. Why This Architecture Answers the "Lightweight AI" Concern

The expert's core insight: **For 4H/1D medium-term systems, edge comes from correct state abstraction and disciplined playbook execution, not from heavier models.**

### What Kills Medium-Term Strategies
❌ Trading the wrong playbook in the wrong state (e.g., trend-following in a range)
❌ Oversizing in stress states
❌ Holding losers too long (no invalidation exit)
❌ Cutting winners too early (no playbook-aware trailing)
❌ Ignoring positioning stress (crowding, deleveraging)

### What This Architecture Fixes
✅ **Playbook switching** — right logic for right state
✅ **Stress-aware sizing** — smaller in hostile conditions
✅ **Invalidation exits** — cut losers fast
✅ **Playbook-aware trailing** — let winners run in trends, take profit fast in ranges
✅ **Positioning state** — avoid crowded trades

### Where Lightweight AI Fits
- **State confidence estimation** — is this state stable or about to flip?
- **Anomaly detection** — is current market behavior unusual?
- **Regime transition prediction** — early warning of state change

### Where Lightweight AI Does NOT Fit
- ❌ Direct long/short prediction (playbook handles this)
- ❌ Direct leverage sizing (sizing engine handles this)
- ❌ Direct exit triggers (exit engine handles this)

**Result**: A small HMM or gradient boosting model can improve state estimation without replacing the strategy. This is **exactly the right role for lightweight AI** in a medium-term system.

---

## 6. Expected Outcomes

### Immediate Benefits (Phase 3-4)
- **Better explainability**: Dashboard shows state + playbook, not just regime
- **Easier debugging**: "Why did we go flat?" → "Playbook switched from Bull to Preservation because volatility_state → DISTORTED"
- **Clearer feature prioritization**: New signals feed into state dimensions, not directly into direction

### Medium-Term Benefits (Phase 5-6)
- **Better risk-adjusted returns**: Stress-aware sizing + disciplined exits
- **Lower drawdowns**: Capital preservation playbook activates in hostile states
- **Higher profit factor**: Better exit asymmetry (cut losers fast, let winners run)

### Long-Term Benefits (Phase 7-8)
- **Scalable feature integration**: Funding/OI/options feed into positioning state, not ad-hoc direction tweaks
- **ML-ready architecture**: State engine is the natural place for ML, not the final decision layer
- **Multi-asset expansion**: Playbook framework works for BTC, ETH, SOL with asset-specific state thresholds

---

## 7. Implementation Principles

### 7.1 Incremental Migration
- **Do not rewrite everything at once**
- Each phase should be independently testable
- Backtest validation after each phase
- Keep old logic as fallback during transition

### 7.2 Backward Compatibility
- New domain objects extend existing ones (e.g., `MarketState` includes old `regime` field)
- Dashboard supports both old and new views during transition
- PostgreSQL schema migrations are additive (new columns, not dropped columns)

### 7.3 Fail-Safe Defaults
- If state engine fails, fall back to current regime logic
- If playbook engine fails, fall back to current direction logic
- If sizing engine fails, use conservative 1.0x leverage
- If exit engine fails, use current trailing stop only

### 7.4 Audit Trail
- Every state transition logged to PostgreSQL
- Every playbook switch logged with reason
- Every sizing component logged for explainability
- Every exit trigger logged with type + urgency

---

## 8. Open Questions for Product Owner

1. **Phase 3 Priority**: Should we start with state engine refactor, or finish current Phase 3 (funding/OI) first?
   - **Recommendation**: State engine first. Funding/OI will be easier to integrate once positioning state exists.

2. **Playbook Count**: Start with 4 playbooks, or add more (e.g., "Volatility Breakout", "Squeeze Relief")?
   - **Recommendation**: Start with 4. Add more only after validating the framework works.

3. **ML Timeline**: When should we add HMM/classifier for state confidence?
   - **Recommendation**: After Phase 6 (exit engine). Need clean state history first.

4. **Multi-Asset**: When should we expand beyond BTC?
   - **Recommendation**: After Phase 6. Playbook framework should work on BTC first.

5. **Dashboard Complexity**: How much state detail should the dashboard show?
   - **Recommendation**: Show all 4 state dimensions + active playbook. Users need to trust the system, and transparency builds trust.

---

## 9. Conclusion

ZenQuant's current architecture (Phase 2) is **solid but incomplete**. It has the right contracts (determinism, auditability, risk veto) but lacks the **strategic layer** that separates profitable medium-term systems from indicator aggregators.

The playbook-driven state architecture proposed here:
- **Preserves** all existing contracts
- **Extends** the system with explicit state/playbook/sizing/exit layers
- **Prepares** the system for funding/OI/options/ML integration
- **Addresses** the "lightweight AI" concern by showing where AI fits (state confidence) and where it doesn't (final decisions)

**Next Step**: Review this design with the product owner, then begin Phase 3 (State Engine Foundation) implementation.

---

**End of Document**
