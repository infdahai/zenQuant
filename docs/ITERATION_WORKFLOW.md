# Complete Iteration Workflow

**Date**: 2026-03-17
**Status**: Production Ready

---

## Overview

This document describes the complete workflow for iterating on strategy parameters until profitability. It combines historical backtesting, paper-live validation, and continuous optimization.

---

## Phase 1: Initial Parameter Optimization (Historical Data)

### Goal
Find reasonable starting parameters using historical data before paper-live deployment.

### Prerequisites
```bash
# Download Binance historical data (3-6 months recommended)
# https://data.binance.vision/?prefix=data/futures/um/daily/klines/BTCUSDT/

# Organize as:
# data/raw/binance/klines/BTCUSDT/4h/*.parquet
# data/raw/binance/klines/BTCUSDT/1d/*.parquet
# data/raw/binance/funding/BTCUSDT/*.parquet
# data/raw/binance/open_interest/BTCUSDT/4h/*.parquet
```

### Step 1.1: Quick Parameter Sweep (Day 1)

**Objective**: Identify best parameter combinations quickly

```bash
# Run quick sweep (3 combinations, ~5 minutes)
uv run python scripts/param_sweep.py --quick

# View results
uv run python scripts/compare_results.py results/param_sweep.json

# Export for analysis
uv run python scripts/compare_results.py results/param_sweep.json \
    --export results/quick_sweep_analysis.csv
```

**Expected Output**:
- Top 3 parameter combinations by Sharpe
- Parameter sensitivity analysis
- CSV for further analysis

### Step 1.2: Full Parameter Sweep (Day 2)

**Objective**: Comprehensive parameter search

```bash
# Run full sweep (243 combinations, ~30-60 minutes)
uv run python scripts/param_sweep.py --full \
    --output results/full_sweep.json

# Compare results
uv run python scripts/compare_results.py results/full_sweep.json \
    --sensitivity

# Find best Sharpe/drawdown tradeoff
uv run python scripts/compare_results.py results/full_sweep.json \
    --metric sharpe
```

**Expected Output**:
- Best parameters across 243 combinations
- Sensitivity analysis showing which parameters matter most
- Sharpe/drawdown tradeoff analysis

### Step 1.3: Walk-Forward Validation (Day 3)

**Objective**: Validate parameters on out-of-sample data

```bash
# Run walk-forward tuning (prevents overfitting)
uv run python scripts/walk_forward_tune.py --full

# Review results
cat results/walk_forward_tuning.json | jq '.promotion_decision'
```

**Expected Output**:
- Average validation Sharpe across multiple splits
- Recommended parameters (most common best params)
- Promotion decision (PROMOTE/REJECT/REVIEW)

### Step 1.4: Update Default Config (Day 3)

**If promotion decision is PROMOTE**:

```python
# Update zenquant/strategy/playbook_engine.py
@dataclass(slots=True)
class PlaybookEngineConfig:
    preserve_capital_transition_risk: float = 0.70  # Updated from 0.65
    bull_max_leverage: float = 3.5  # Updated from 3.0
    # ... other params
```

**Commit changes**:
```bash
git add zenquant/strategy/
git commit -m "tune: update strategy parameters based on walk-forward validation

- preserve_capital_transition_risk: 0.65 → 0.70
- bull_max_leverage: 3.0 → 3.5

Evidence:
- Walk-forward validation Sharpe: 5.2 → 5.8
- Max drawdown: 0.76% → 0.68%
- 4 splits, all showed improvement

See results/walk_forward_tuning.json for details"
```

---

## Phase 2: Paper-Live Validation (Week 1-2)

### Goal
Validate optimized parameters on real market data without risking capital.

### Step 2.1: Start Paper-Live (Day 4)

```bash
# Set up PostgreSQL
createdb zenquant_live
export ZENQUANT_PG_DSN="postgresql://localhost/zenquant_live"

# Start paper-live server
uv run python -m zenquant.app.server --mode live

# Open dashboard
open http://localhost:8765

# Run validation script (in separate terminal)
uv run zenquant-live-validate --symbol BTCUSDT_PERP --watch 300
```

### Step 2.2: Daily Monitoring (Days 5-11)

**Every morning**:
```bash
# Check validation status
uv run zenquant-live-validate --symbol BTCUSDT_PERP --watch 60

# Export yesterday's data
uv run python scripts/export_live_training_data.py \
    --dsn "$ZENQUANT_PG_DSN" \
    --symbol BTCUSDT_PERP \
    --label "day_$(date +%Y%m%d)"

# Check key metrics
psql $ZENQUANT_PG_DSN -c "
SELECT
    COUNT(*) as decisions,
    AVG(final_leverage) as avg_leverage,
    COUNT(CASE WHEN exit_type != 'NONE' THEN 1 END) as exits
FROM sizing_decisions s
JOIN exit_decisions e ON s.cutoff_time_ns = e.cutoff_time_ns
WHERE s.cutoff_time_ns > EXTRACT(EPOCH FROM NOW() - INTERVAL '24 hours') * 1e9;
"
```

**Monitor for**:
- Dashboard updates every 4h bar close
- PostgreSQL writes accumulating
- No crashes or reconnect loops
- Quality flags triggering appropriately
- Trailing stops updating correctly

### Step 2.3: Weekly Analysis (Day 11)

```bash
# Export full week's data
uv run python scripts/export_live_training_data.py \
    --dsn "$ZENQUANT_PG_DSN" \
    --symbol BTCUSDT_PERP \
    --label "week1"

# Generate diagnostic report
uv run python scripts/run_research_pipeline.py \
    --root results/live_training_runs/week1 \
    --symbol BTCUSDT_PERP \
    --label "week1_analysis"

# Review report
open results/research_runs/week1_analysis/report.html
```

**Key Questions**:
1. Are playbook transitions reasonable?
2. Are exit decisions following priority logic?
3. Are sizing decisions respecting leverage caps?
4. Are quality flags triggering appropriately?
5. Is trailing stop logic working correctly?

---

## Phase 3: Continuous Optimization (Weekly Cycle)

### Goal
Continuously improve parameters based on live data.

### Monday: Data Export & Diagnostics

```bash
# Export last week's paper-live data
uv run python scripts/export_live_training_data.py \
    --dsn "$ZENQUANT_PG_DSN" \
    --symbol BTCUSDT_PERP \
    --label "week_$(date +%U)"

# Generate diagnostics
uv run python scripts/run_research_pipeline.py \
    --root results/live_training_runs/week_$(date +%U) \
    --symbol BTCUSDT_PERP \
    --label "week_$(date +%U)_diagnostics"

# Review HTML report
open results/research_runs/week_$(date +%U)_diagnostics/report.html
```

**Review Checklist**:
- [ ] Profit factor by playbook
- [ ] Hit rate by exit type
- [ ] Drawdown by regime
- [ ] Fee drag analysis
- [ ] False-positive rates for state transitions
- [ ] Target-to-fill slippage

### Tuesday-Wednesday: Parameter Tuning

**If diagnostics show issues**:

```bash
# Run targeted parameter sweep
uv run python scripts/param_sweep.py \
    --preserve-capital-risk 0.65,0.70,0.75 \
    --exit-transition-risk 0.80,0.85,0.90 \
    --output results/weekly_tune_$(date +%Y%m%d).json

# Compare against baseline
uv run python scripts/compare_results.py \
    results/weekly_tune_$(date +%Y%m%d).json

# Run walk-forward validation
uv run python scripts/walk_forward_tune.py --full
```

**Decision Criteria**:
- Sharpe improvement > 0.1
- Max drawdown regression < 2%
- At least 10 trades in validation period
- Clear reason for change (documented in commit)

### Thursday: Validation

```bash
# Run replay with new parameters
uv run python scripts/run_research_pipeline.py \
    --root data/raw/binance \
    --symbol BTCUSDT \
    --preserve-capital-risk 0.70 \
    --exit-transition-risk 0.90 \
    --label "proposed_params"

# Compare against baseline
# (Manual comparison of report.html files)
```

**Validation Checklist**:
- [ ] Sharpe improved on out-of-sample data
- [ ] Drawdown did not regress significantly
- [ ] Trade count reasonable (not too few, not too many)
- [ ] Playbook transitions make sense
- [ ] Exit logic still follows priorities

### Friday: Promotion Decision

**If validation passes**:

1. Update default config in code
2. Commit with evidence
3. Restart paper-live server with new params
4. Monitor closely for 24h

**If validation fails**:

1. Document why in `results/tuning_log.md`
2. Keep current parameters
3. Iterate next week

---

## Phase 4: Real Capital Deployment (After 2-4 Weeks)

### Prerequisites

- [ ] 2-4 weeks of stable paper-live operation
- [ ] No crashes or data quality issues
- [ ] Parameters validated on live data
- [ ] Sharpe > 2.0 on paper-live
- [ ] Max drawdown < 5% on paper-live
- [ ] Clear understanding of failure modes

### Step 4.1: Tiny-Capital Shadow (Week 5)

**Start with very small notional**:
```bash
# Configure tiny capital
export ZENQUANT_STARTING_EQUITY=1000  # $1000 only
export ZENQUANT_MAX_LEVERAGE=1.0  # Conservative

# Start live mode (real execution)
uv run python -m zenquant.app.server --mode live --real-execution
```

**Monitor closely**:
- Compare real fills vs simulated fills
- Measure actual slippage
- Track execution quality
- Watch for unexpected behavior

### Step 4.2: Gradual Expansion (Weeks 6-8)

**If tiny-capital shadow is stable**:

Week 6: $5,000 capital, 1.5x max leverage
Week 7: $10,000 capital, 2.0x max leverage
Week 8: $25,000 capital, 2.5x max leverage

**Expansion Criteria**:
- No major incidents in previous week
- Actual Sharpe within 20% of paper-live Sharpe
- Actual slippage < 0.1% per trade
- No quality flag issues

### Step 4.3: Full Deployment (Week 9+)

**Only after**:
- 4+ weeks of real capital operation
- Consistent profitability
- Stable execution quality
- Clear rollback plan

---

## Rollback Plan

### Trigger Conditions

**Immediate rollback if**:
- Drawdown > 10% in single day
- Quality flags stuck in UNSAFE state
- Execution errors > 5% of orders
- Dashboard shows inconsistent state

**Gradual rollback if**:
- Sharpe drops > 50% vs paper-live
- Drawdown > 2x paper-live drawdown
- Slippage > 0.2% per trade consistently
- Playbook transitions become erratic

### Rollback Procedure

```bash
# 1. Stop live server immediately
pkill -f "zenquant.app.server"

# 2. Export all data for post-mortem
uv run python scripts/export_live_training_data.py \
    --dsn "$ZENQUANT_PG_DSN" \
    --symbol BTCUSDT_PERP \
    --label "rollback_$(date +%Y%m%d_%H%M%S)"

# 3. Revert to last known good config
git revert HEAD  # If recent change caused issue
# OR
git checkout <last_good_commit>

# 4. Restart in paper-live mode
uv run python -m zenquant.app.server --mode live  # Paper only

# 5. Analyze what went wrong
uv run python scripts/run_research_pipeline.py \
    --root results/live_training_runs/rollback_* \
    --symbol BTCUSDT_PERP \
    --label "post_mortem"
```

---

## Tools Reference

### Parameter Sweep
```bash
# Quick sweep (3 combinations)
uv run python scripts/param_sweep.py --quick

# Full sweep (243 combinations)
uv run python scripts/param_sweep.py --full

# Custom sweep
uv run python scripts/param_sweep.py \
    --preserve-capital-risk 0.60,0.65,0.70 \
    --bull-leverage 2.5,3.0,3.5 \
    --output results/custom.json
```

### Result Comparison
```bash
# Rank by Sharpe
uv run python scripts/compare_results.py results/sweep.json

# Rank by PnL
uv run python scripts/compare_results.py results/sweep.json --metric pnl

# Sensitivity analysis
uv run python scripts/compare_results.py results/sweep.json --sensitivity

# Export CSV
uv run python scripts/compare_results.py results/sweep.json \
    --export results/analysis.csv
```

### Walk-Forward Tuning
```bash
# Quick tuning (2 splits, 3 params)
uv run python scripts/walk_forward_tune.py --quick

# Full tuning (4 splits, 27 params)
uv run python scripts/walk_forward_tune.py --full
```

### Research Pipeline
```bash
# Historical replay
uv run python scripts/run_research_pipeline.py \
    --root data/raw/binance \
    --symbol BTCUSDT \
    --label baseline

# Live data export
uv run python scripts/export_live_training_data.py \
    --dsn "$ZENQUANT_PG_DSN" \
    --symbol BTCUSDT_PERP \
    --label week1
```

---

## Success Metrics

### Phase 1 (Historical Optimization)
- ✅ Sharpe > 3.0 on historical data
- ✅ Max drawdown < 5%
- ✅ Walk-forward validation passes
- ✅ Parameters make intuitive sense

### Phase 2 (Paper-Live Validation)
- ✅ 2 weeks uptime without crashes
- ✅ Sharpe > 2.0 on paper-live
- ✅ Max drawdown < 5%
- ✅ Quality flags trigger appropriately
- ✅ Trailing stops work correctly

### Phase 3 (Continuous Optimization)
- ✅ Weekly tuning cycle established
- ✅ Parameters improving over time
- ✅ Clear promotion criteria
- ✅ Rollback plan tested

### Phase 4 (Real Capital)
- ✅ Tiny-capital shadow profitable
- ✅ Actual slippage < 0.1% per trade
- ✅ Sharpe within 20% of paper-live
- ✅ No major incidents

---

## Common Issues & Solutions

### Issue: Historical sweep shows no profitable parameters
**Solution**:
- Check data quality (missing bars, incorrect timestamps)
- Verify indicator calculations
- Review playbook selection logic
- Consider expanding parameter ranges

### Issue: Paper-live Sharpe much worse than historical
**Solution**:
- Check for lookahead bias in historical replay
- Verify bar-close timing is correct
- Compare indicator values (historical vs live)
- Check execution slippage assumptions

### Issue: Parameters keep changing every week
**Solution**:
- Increase promotion criteria thresholds
- Require longer validation periods
- Check for overfitting (walk-forward validation)
- Consider regime-dependent parameters

### Issue: Real capital performance worse than paper-live
**Solution**:
- Measure actual slippage vs assumptions
- Check execution quality (fill rates, partial fills)
- Verify orderbook depth at execution times
- Consider reducing position sizes

---

## Next Steps

1. **Today**: Download historical data, run quick sweep
2. **Tomorrow**: Run full sweep, identify best parameters
3. **Day 3**: Walk-forward validation, update config
4. **Day 4**: Start paper-live with optimized parameters
5. **Week 2**: Weekly tuning cycle
6. **Week 5**: Tiny-capital shadow (if paper-live stable)
7. **Week 9**: Full deployment (if all criteria met)

---

**Last Updated**: 2026-03-17
**Next Review**: After Phase 2 completion (2 weeks paper-live)
