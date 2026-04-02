# Validation Results

Historical validation evidence and baseline metrics.

---

## Walk-Forward Validation (539 days)

**Date**: 2026-03-17
**Data**: Binance BTCUSDT 539 days (2024-09-23 to 2026-03-17)
**Method**: 3-fold walk-forward, 27 parameter combinations per fold
**Signals**: 5 active (multi_momentum, CVD, liquidity, crowding, macro_regime)

### Results by Split

| Split | Train Period | Val Period | Train Sharpe | Val Sharpe | Val MaxDD | Val PnL |
|-------|-------------|------------|--------------|------------|-----------|---------|
| 1 | 2025-03-17 ~ 06-15 | 2025-06-15 ~ 09-13 | 4.96 | 6.22 | 6.08% | +681,382 |
| 2 | 2025-06-15 ~ 09-13 | 2025-09-13 ~ 12-12 | 6.22 | 5.15 | 12.80% | +642,431 |
| 3 | 2025-09-13 ~ 12-12 | 2025-12-12 ~ 03-12 | 5.15 | 6.78 | 6.45% | +1,124,319 |

### Summary

- **Avg validation Sharpe**: 6.05
- **Avg validation drawdown**: 8.44%
- **Total validation PnL**: +2.45M (on 100K starting equity)
- **Parameter consistency**: All 3 splits selected same best parameters

### Best Parameters (consensus across all splits)

```python
preserve_capital_transition_risk = 0.60   # was 0.65
transition_risk_exit_threshold = 0.80     # was 0.85
bull_max_leverage = 2.50                  # was 3.0
```

Trend: longer time horizons require more conservative parameters.

---

## Full Backtest with Macro Signal (2026-04-01)

**Data**: Binance BTCUSDT 3,234 bars (2024-09-23 to 2026-03-15)
**Signals**: 5 active (multi_momentum 0.45, CVD 0.25, liquidity 0.20, crowding 0.10, macro_regime 0.12)
**Options signal**: Disabled (insufficient historical data)

| Metric | Value |
|--------|-------|
| Net PnL | +1,270,848 |
| Sharpe | 3.27 |
| Max Drawdown | 10.6% |
| Profit Factor | 1.12 |
| Trade Count | 2,127 |

**Macro Signal Attribution** (32% of decisions):
- `MACRO_WEAK_USD`: 400 bars (12.4%)
- `MACRO_RISK_OFF`: 272 bars (8.4%)
- `MACRO_STRONG_USD`: 237 bars (7.3%)
- `MACRO_RISK_ON`: 125 bars (3.9%)

---

## Deterministic Regression Baseline

**Source**: Synthetic 90-day BTC dataset (locked by `test_golden_output.py`)
**Config**: default.toml with plugin registry (macro enabled, options disabled)

| Metric | Value |
|--------|-------|
| Net PnL | +142,097 |
| Sharpe | 4.2891 |
| Max Drawdown | 1.83% |
| Profit Factor | 2.08 |
| Trade Count | 332 |

This baseline guards against regression -- any code change that alters these metrics
will fail Gate C. Updated 2026-04-01 after adding options infrastructure and code cleanup.

---

## Known Limitations

1. **Execution simulation is optimistic**: Uses best-bid/ask with 4bps fixed fee, no partial fills or market impact
2. **Single symbol**: Only BTC validated; ETH/SOL/BNB data not yet collected
3. **No live vs backtest comparison**: Paper-live audit trail not yet compared to historical expectations
4. **Options signal pending**: `options_vol_signal` implemented but disabled -- needs 30+ Deribit snapshots for z-score activation
5. **Macro data daily resolution**: VIX/DXY/Treasury update daily, may lag intraday events

---

## Validation Infrastructure

| Layer | Tool | Status |
|-------|------|--------|
| Historical backtest | `HistoricalBacktest` | Working, 3s per run |
| Walk-forward | `walk_forward_tune.py` | Parallelized (6-8x speedup) |
| Domain randomization | `PerturbationConfig` | Integrated, deterministic seeds |
| Funding accumulation | `_FundingAccumulator` | Integrated |
| AutoResearch campaign | `auto_research.py` | Sweep + WF + robustness + promotion |
| Macro data store | `MacroDataStore` | Active (VIX, DXY, Treasury) |
| Options data store | `OptionsDataStore` | Built, pending data collection |
| Golden output regression | `test_golden_output.py` | Gate C enforcement |
| Regime-split validation | -- | Not yet implemented |
| Live incremental validation | -- | Planned for server deployment |
