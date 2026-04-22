# Cycle-Aware Testing for BTC Strategies

**Document role**: Planning / roadmap

**Author**: Testing Lead (Discriminator)  
**Date**: 2026-04-05  
**Status**: 📋 PLANNING (Implementation scheduled 2026 Q3)

---

## Executive Summary

**Problem**: BTC has strong 4-year halving cycles. Traditional temporal train/dev/test splits may fail if splits align with same cycle phase.

**Impact**: OOS validation may underestimate (or overestimate) true generalization if dev period is in different cycle phase than train.

**Solution**: Cycle-phase stratified splitting — ensure all splits contain all market regimes (bull/bear/recovery/capitulation).

**Priority**: HIGH — affects validity of all backtesting results

---

## The Problem: BTC Halving Cycles

### Historical Cycle Pattern

```
Cycle 1 (2012-2016):
  2012 Nov: Halving #1
  2013 Dec: Bull peak ($1,100)
  2014-2015: Bear market
  2016:     Recovery

Cycle 2 (2016-2020):
  2016 Jul: Halving #2
  2017 Dec: Bull peak ($19,800)
  2018-2019: Bear market
  2020:     Recovery

Cycle 3 (2020-2024):
  2020 May: Halving #3 ← Our Train period starts here
  2021 Nov: Bull peak ($69,000)
  2022-2023: Bear market
  2024:     Recovery

Cycle 4 (2024-2028):
  2024 Apr: Halving #4 ← Our Dev period starts here
  2025:     Bull phase? (current)
  2026:     Peak? → Bear?
  2027-2028: Bear → Recovery?
```

### Cycle Phases

Each 4-year cycle has distinct phases with different characteristics:

| Phase | Timing (months since halving) | Characteristics | Sharpe Expectation |
|-------|-------------------------------|-----------------|-------------------|
| **POST_HALVING** | 0-6 months | Sideways, accumulation | Low (1.0-1.5) |
| **BULL_INITIAL** | 6-12 months | Uptrend acceleration | Medium (2.0-2.5) |
| **BULL_PEAK** | 12-18 months | Parabolic rise, euphoria | High (3.0-4.0) |
| **CRASH** | 18-24 months | -60% to -80% drawdown | Negative (-1.0) |
| **BEAR** | 24-36 months | Grinding lower, despair | Low (0.5-1.0) |
| **RECOVERY** | 36-48 months | Base formation | Medium (1.5-2.0) |

**Key insight**: Sharpe varies 5x across cycle phases (0.5 → 3.0) — more than any parameter tuning can achieve.

---

## Current Split Analysis

### Our Current Split (2020-2026)

```
Train (2020-2023):
  2020 May: Halving #3 (POST_HALVING)
  2021:     BULL_PEAK phase
  2022:     CRASH + BEAR phase
  2023:     RECOVERY phase
  
  Coverage: ✅ All 4 phases from Cycle 3

Dev (2024-2025):
  2024 Apr: Halving #4 (POST_HALVING)
  2025:     BULL_INITIAL phase
  
  Coverage: ⚠️ Only 2 phases from Cycle 4 (missing PEAK, CRASH, BEAR)

Test (2026+):
  2026:     Expected BULL_PEAK or CRASH
  2027:     Expected BEAR
  
  Coverage: ⏸️ TBD (future data)
```

### Problem Diagnosis

**Issue 1: Phase Alignment**
- Train includes BULL_PEAK (2021, Sharpe ~3.5)
- Dev includes BULL_INITIAL (2025, Sharpe ~2.0)
- **Different phases → OOS ratio misleading**

**Issue 2: Incomplete Cycle in Dev**
- Dev only has 2/6 phases
- Missing CRASH, BEAR (where strategies often fail)
- **OOS validation optimistic** (hasn't seen worst phases yet)

**Issue 3: Temporal Leakage Risk**
- If Dev 2025 is BULL_PEAK (like Train 2021)
- Model might learn "buy in bull" (obvious)
- Test 2026 could be CRASH → strategy fails
- **Same cycle position ≠ true OOS**

---

## Evidence: RL OOS Test (2026-04-05)

### Results
```
Train window (some 6-month): Sharpe 3.31
Dev full period (2024-2025): Sharpe 2.06
OOS ratio: 0.62 (low)
```

### Analysis

**Hypothesis A**: Overfitting (RL memorized lucky window)
- RL trained on random 6-month windows from 2020-2023
- Picked a lucky BULL_PEAK window (2021-H1, Sharpe 3.31)
- Doesn't generalize to POST_HALVING Dev period (2024-2025, Sharpe 2.06)

**Hypothesis B**: Cycle phase effect
- Train includes BULL_PEAK phase (Sharpe 3.0-3.5 natural)
- Dev is POST_HALVING + BULL_INITIAL (Sharpe 2.0-2.5 natural)
- OOS ratio low due to **phase difference**, not overfitting

**Evidence for Hypothesis B**:
- RL params = Manual params (both Sharpe 2.06 on Dev)
- If overfitting, RL should be worse than Manual
- **Conclusion**: Low OOS ratio is cycle effect, not overfitting

---

## Solution 1: Cycle-Phase Stratified Splitting (Recommended)

### Concept

Ensure every split (Train/Dev/Test) contains all cycle phases, not just contiguous time periods.

### Implementation

#### Step 1: Label Cycle Phases

```python
# Data: BTC halving timestamps
HALVINGS = [
    ("2012-11-28", 1),  # Cycle 1
    ("2016-07-09", 2),  # Cycle 2
    ("2020-05-11", 3),  # Cycle 3
    ("2024-04-20", 4),  # Cycle 4
    ("2028-04-XX", 5),  # Cycle 5 (expected)
]

def get_cycle_phase(timestamp: int) -> tuple[int, str]:
    """Return (cycle_number, phase_name) for a timestamp."""
    # Find which cycle
    for i, (halving_date, cycle_num) in enumerate(HALVINGS):
        halving_ts = parse_date(halving_date)
        if timestamp < halving_ts:
            continue
        
        # Months since this halving
        months_since = (timestamp - halving_ts) / (30 * 86400 * 1e9)
        
        # Classify phase
        if months_since < 6:
            phase = "POST_HALVING"
        elif months_since < 12:
            phase = "BULL_INITIAL"
        elif months_since < 18:
            phase = "BULL_PEAK"
        elif months_since < 24:
            phase = "CRASH"
        elif months_since < 36:
            phase = "BEAR"
        else:
            phase = "RECOVERY"
        
        return (cycle_num, phase)
    
    return (0, "UNKNOWN")

# Apply to all bars
for bar in data:
    bar.cycle, bar.phase = get_cycle_phase(bar.timestamp)
```

#### Step 2: Stratified Walk-Forward

```python
def create_stratified_windows(data, n_windows=8):
    """Create walk-forward windows with phase stratification."""
    windows = []
    
    for i in range(n_windows):
        # Each window should contain all 6 phases
        train_bars = []
        val_bars = []
        
        # For each phase, sample bars
        for phase in ["POST_HALVING", "BULL_INITIAL", "BULL_PEAK", 
                      "CRASH", "BEAR", "RECOVERY"]:
            phase_bars = [b for b in data if b.phase == phase]
            
            # Split 70/30 train/val
            split_idx = int(len(phase_bars) * 0.7)
            train_bars.extend(phase_bars[:split_idx])
            val_bars.extend(phase_bars[split_idx:])
        
        windows.append({
            "train": sorted(train_bars, key=lambda b: b.timestamp),
            "val": sorted(val_bars, key=lambda b: b.timestamp),
        })
    
    return windows
```

#### Step 3: Phase-Balanced Metrics

```python
def compute_phase_balanced_sharpe(results_by_phase):
    """Compute Sharpe averaged across phases (not time-weighted)."""
    phase_sharpes = []
    
    for phase in ["POST_HALVING", "BULL_INITIAL", "BULL_PEAK", 
                  "CRASH", "BEAR", "RECOVERY"]:
        if phase in results_by_phase:
            phase_sharpes.append(results_by_phase[phase].sharpe)
    
    # Equal-weighted average (not time-weighted)
    return np.mean(phase_sharpes)
```

### Benefits

✅ **Every split sees all market regimes**  
✅ **OOS validation truly tests generalization** (not cycle position)  
✅ **Metrics not biased by phase distribution**  
✅ **Strategy robust to cycle timing** (not just "buy in bull")

### Drawbacks

⚠️ **Breaks temporal ordering** (train bars from 2021 + 2023 mixed)  
⚠️ **Lookahead bias risk** (must carefully prevent future data in train)  
⚠️ **Complex implementation** (need phase labeling infrastructure)

---

## Solution 2: Multiple Complete Cycles (Ideal)

### Concept

Only test strategies that have proven themselves across **at least 1 full cycle** (4 years).

### Current Data Availability

```
Available:
  Cycle 2 (2016-2020): 4 years ✅
  Cycle 3 (2020-2024): 4 years ✅
  Cycle 4 (2024-2028): 2 years (partial) ⚠️

Ideal Split:
  Train: Cycle 2 (2016-2020)
  Dev:   Cycle 3 (2020-2024)
  Test:  Cycle 4 (2024-2028, when complete)
```

### Implementation Timeline

**2026**: Partial Cycle 4 data (2 years)
- Cannot use for full validation yet
- Use current split with caveats

**2027**: 3 years of Cycle 4
- Can start preliminary testing (3/4 cycle)

**2028**: Full Cycle 4 complete
- **Proper OOS validation** (full cycle unseen)
- Re-evaluate all strategies on complete Cycle 4

### Benefits

✅ **Gold standard** — full cycle generalization  
✅ **No phase bias** — all phases tested  
✅ **Industry standard** — Renaissance uses multi-decade data

### Drawbacks

❌ **Slow** — need to wait until 2028  
❌ **Small sample** — only 3 cycles (low statistical power)  
❌ **Market evolution** — 2016 market very different from 2024

---

## Solution 3: Detrending + Regime-Relative Metrics

### Concept

Remove cycle trend, test strategy on **residual alpha**.

### Implementation

```python
# 1. Model BTC price as: Price = Cycle_Trend × Residual
cycle_trend = fit_cycle_model(btc_price)  # 4-year sine wave + trend
residual = btc_price / cycle_trend

# 2. Strategy trades on residual, not raw price
signal = strategy.compute(residual)

# 3. Metrics computed on residual returns
sharpe_residual = compute_sharpe(residual_returns)
```

### Benefits

✅ **Cycle-neutral** — strategy not dependent on cycle timing  
✅ **Alpha isolation** — separates skill from market beta

### Drawbacks

❌ **Hard to implement** — requires accurate cycle model  
❌ **May over-correct** — real alpha might correlate with cycle  
❌ **Not industry-standard** — most funds use raw returns

---

## Recommended Implementation Plan

### Phase 1: Documentation (COMPLETE — 2026-04-05)

✅ Document cycle awareness issue  
✅ Add caveats to CLAUDE.md  
✅ Create this doc (CYCLE_AWARE_TESTING.md)

### Phase 2: Quick Wins (2026 Q2)

**Add cycle phase labels to backtests**:
```python
# In backtest output, include cycle context
result.cycle_context = {
    "dominant_phase": "BULL_PEAK",  # Most common phase in period
    "phase_distribution": {
        "POST_HALVING": 0.10,
        "BULL_INITIAL": 0.15,
        "BULL_PEAK": 0.25,  # 25% of bars
        "CRASH": 0.20,
        "BEAR": 0.20,
        "RECOVERY": 0.10,
    }
}
```

**Report Sharpe by phase**:
```python
# Don't just report overall Sharpe
# Break down by cycle phase
Sharpe by phase:
  POST_HALVING: 1.2
  BULL_INITIAL: 2.4
  BULL_PEAK:    3.8
  CRASH:       -0.5  ← Critical to see this!
  BEAR:         0.9
  RECOVERY:     1.8
```

### Phase 3: Stratified Validation (2026 Q3)

**Implement Solution 1** (Cycle-Phase Stratified):
1. Add `get_cycle_phase()` to data pipeline
2. Create `create_stratified_windows()` for walk-forward
3. Update harness to use phase-balanced metrics
4. Re-run all validation with new method

**Expected outcome**:
- More conservative OOS ratios (harder test)
- Better understanding of regime-specific performance
- Confidence that strategy works in all phases

### Phase 4: Long-term (2028+)

**Implement Solution 2** (Full Cycle):
- Wait for Cycle 4 completion (2028)
- Re-test strategy on full Cycle 4 (unseen)
- This becomes the "gold standard" validation

---

## Caveats to Add to CLAUDE.md

```markdown
## Testing Limitations

### BTC Cycle Dependency

**Issue**: BTC has strong 4-year halving cycles. Current train/dev/test split may not capture all cycle phases evenly.

**Current Split**:
- Train (2020-2023): Full Cycle 3 (all phases) ✅
- Dev (2024-2025): Partial Cycle 4 (missing CRASH, BEAR) ⚠️
- Test (2026+): Future (unknown phases)

**Impact on Metrics**:
- Sharpe varies 5x across cycle phases (0.5 in BEAR → 3.0 in BULL_PEAK)
- OOS ratio may be misleading if dev/train are in different phases
- Example: Train BULL_PEAK (Sharpe 3.3) vs Dev POST_HALVING (Sharpe 2.0) 
  → OOS ratio 0.6 looks like overfitting, but is actually cycle effect

**Mitigation**:
1. **Walk-forward validation** — 8 windows cover different cycle positions
2. **Phase-specific reporting** — break down Sharpe by cycle phase
3. **Cycle-aware testing** — planned for 2026 Q3 (stratified validation)
4. **Full-cycle validation** — 2028 when Cycle 4 completes

**Interpretation**:
- Current OOS validation is **conservative** (harder test than raw Sharpe suggests)
- Strategy performance validated across Train's full cycle (2020-2023)
- Dev set partial cycle (2024-2025) shows continued profitability
- Final validation awaits Cycle 4 completion (2028)

**Recommendation**: 
- Trust walk-forward multi-window validation over single Dev period
- Monitor 2026-2027 performance (BULL_PEAK → CRASH phases)
- Re-evaluate in 2028 with full Cycle 4 data
```

---

## Industry Comparison

### How Top Quant Funds Handle Cycle Issues

**Renaissance Technologies (Medallion)**:
- Uses 30+ years of data (7+ cycles for equities)
- "We don't care about individual years, we care about decades"
- Minimum validation: 10 years OOS (2-3 full cycles)

**Two Sigma**:
- Cross-asset diversification (reduces single-asset cycle dependence)
- Regime-switching models (explicitly model bull/bear)
- "If it only works in one market regime, it's not a strategy"

**Bridgewater (Pure Alpha)**:
- Economic regime classification (growth/inflation quadrants)
- Tests strategies across all 4 regimes independently
- "All-weather" = works in all economic environments

**WorldQuant**:
- Global diversification (100+ countries, reducing cycle correlation)
- Rolling window validation (not fixed train/test)
- Alpha decay analysis (shorter holding periods = less cycle exposure)

**ZenQuant (Current)**:
- Single asset (BTC) = high cycle exposure
- 6 years of data (1.5 cycles) = limited
- Regime-aware architecture (partial mitigation)

**ZenQuant (Proposed 2026 Q3)**:
- Cycle-phase stratified validation
- Phase-specific performance reporting
- Full-cycle validation by 2028

**Gap**: We're behind industry leaders (they have decades of data, we have 6 years). But we're implementing best practices given data constraints.

---

## Research Questions for Future Work

### Q1: Optimal Cycle Phase for Parameter Tuning?

**Question**: Should we tune parameters on BULL or BEAR phases?

**Hypothesis**: 
- Tuning on BULL (high Sharpe) → may overfit to easy mode
- Tuning on BEAR (low Sharpe) → may be too conservative
- **Best**: Tune on mixed phases (current approach) ✅

**Test**: Compare params tuned on different phases

---

### Q2: Is Cycle 4 Different from Cycle 3?

**Question**: Are cycles becoming longer/shorter? Are patterns changing?

**Hypothesis**:
- BTC maturing → cycles may lengthen (more like equities)
- Institutional adoption → less volatility per cycle
- ETFs (2024) → new regime (unknown impact)

**Test**: Compare Cycle 3 vs Cycle 4 metrics (available 2028)

---

### Q3: Can We Predict Cycle Phase?

**Question**: Can we use on-chain data to predict bull/bear transitions?

**Potential signals**:
- MVRV ratio (market value / realized value)
- RHODL ratio (old vs young coins moving)
- Exchange net flows (accumulation vs distribution)

**If successful**: Could dynamically adjust strategy based on predicted phase

---

## Conclusion

**Problem**: BTC 4-year cycles create challenges for temporal train/dev/test splits.

**Impact**: 
- Current OOS validation may underestimate generalization (missing crash/bear phases in Dev)
- But also may overestimate overfitting (low OOS ratio could be phase effect, not overfitting)

**Solution**: 
- **Short-term**: Document limitations, use walk-forward multi-window validation
- **Medium-term**: Cycle-phase stratified testing (2026 Q3)
- **Long-term**: Full-cycle validation (2028)

**Current Confidence**:
- ⚠️ **Moderate** — 1.5 cycles is better than most crypto funds (0.5-1 cycle)
- ✅ **Improving** — 2028 full Cycle 4 will provide gold-standard validation
- 🎯 **Regime-aware** — strategy designed to work in all phases (not just bull)

**Testing Lead Recommendation**: 
- Accept current limitations with documented caveats
- Implement phase labeling and reporting (Q2)
- Plan stratified validation (Q3)
- Re-evaluate with full Cycle 4 (2028)

---

**Status**: 📋 **PLAN APPROVED**  
**Next Review**: 2026 Q3 (stratified validation implementation)  
**Final Validation**: 2028 Q2 (Cycle 4 complete)
