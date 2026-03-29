# Validation Results

Historical validation evidence and baseline metrics.

---

## Walk-Forward Validation (539 days)

**Date**: 2026-03-17
**Data**: Binance BTCUSDT 539 days (2024-09-23 to 2026-03-17)
**Method**: 3-fold walk-forward, 27 parameter combinations per fold

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

## Deterministic Regression Baseline

**Source**: Synthetic 90-day BTC dataset (locked by `test_historical_pipeline.py`)

| Metric | Value |
|--------|-------|
| Net PnL | +221,697 |
| Sharpe | 5.0015 |
| Max Drawdown | 0.76% |
| Profit Factor | 5.66 |
| Trade Count | 456 |

This baseline guards against regression — any code change that alters these metrics
will fail the test suite.

---

## Known Limitations

1. **Funding costs not yet validated**: Funding accumulator is integrated but historical funding data needs to be downloaded
2. **Execution simulation is optimistic**: Uses best-bid/ask with 4bps fixed fee, no partial fills or market impact
3. **Single symbol**: Only BTC validated; ETH/SOL/BNB data not yet collected
4. **No live vs backtest comparison**: Paper-live audit trail not yet compared to historical expectations

---

## Validation Infrastructure

| Layer | Tool | Status |
|-------|------|--------|
| Historical backtest | `HistoricalBacktest` | Working, 3s per run |
| Walk-forward | `walk_forward_tune.py` | Parallelized (6-8x speedup) |
| Domain randomization | `PerturbationConfig` | Integrated, deterministic seeds |
| Funding accumulation | `_FundingAccumulator` | Integrated, awaiting data |
| Regime-split validation | — | Not yet implemented |
| Live incremental validation | — | Planned for server deployment |
