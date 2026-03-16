# Multi-Exchange Training Strategy

**Date**: 2026-03-17
**Status**: Design Document

---

## Motivation

Training on a single exchange (Binance) has limitations:

1. **Exchange-specific quirks**: Binance funding rates, liquidation cascades, and market microstructure may not generalize
2. **Limited regime coverage**: 90 days may miss key market regimes (bear markets, volatility spikes, low liquidity periods)
3. **Overfitting risk**: Parameters optimized on one exchange may fail on others

**Solution**: Train on multiple exchanges to improve robustness and generalization.

---

## Data Sources

### Primary: Binance (BTCUSDT Perpetual)
- **Coverage**: 540 days (18 months) - covers full BTC cycle
- **Data**: 4h/1d klines, funding rates, open interest
- **Advantages**: High liquidity, reliable data, well-tested
- **Limitations**: Centralized exchange, potential for manipulation

### Secondary: Deribit (BTC-PERPETUAL)
- **Coverage**: 540 days (18 months)
- **Data**: 1h klines, funding rates, options chain snapshots
- **Advantages**: Options data for volatility regime detection, different market microstructure
- **Limitations**: Lower liquidity than Binance, options-focused user base

### Future: Coinbase, Kraken, Bybit
- **Purpose**: Further diversification, cross-exchange arbitrage detection
- **Timeline**: Phase 2 (after initial validation)

---

## Training Approach

### Option 1: Ensemble Training (Recommended)

Train separate models on each exchange, then ensemble predictions:

```python
# Pseudo-code
binance_signal = strategy.predict(binance_data)
deribit_signal = strategy.predict(deribit_data)

# Weighted ensemble (higher weight to higher liquidity)
final_signal = 0.7 * binance_signal + 0.3 * deribit_signal
```

**Advantages**:
- Captures exchange-specific patterns
- Robust to single-exchange failures
- Can weight by liquidity/reliability

**Disadvantages**:
- More complex implementation
- Requires careful weight tuning

### Option 2: Pooled Training

Combine all exchange data into single training set:

```python
# Pseudo-code
combined_data = pd.concat([binance_data, deribit_data])
strategy.fit(combined_data)
```

**Advantages**:
- Simpler implementation
- More training data
- Learns cross-exchange patterns

**Disadvantages**:
- May dilute exchange-specific signals
- Requires careful normalization

### Option 3: Transfer Learning

Train on Binance (high liquidity), fine-tune on Deribit:

```python
# Pseudo-code
strategy.fit(binance_data)  # Pre-train
strategy.fine_tune(deribit_data)  # Fine-tune
```

**Advantages**:
- Leverages high-quality Binance data
- Adapts to Deribit microstructure
- Efficient use of limited Deribit data

**Disadvantages**:
- Requires ML framework (not rule-based)
- More complex training pipeline

---

## Implementation Plan

### Phase 1: Data Collection (Week 1)

1. Download 540 days of Binance data ✅
2. Download 540 days of Deribit data (in progress)
3. Convert to unified Parquet format
4. Validate data quality (missing bars, outliers)

### Phase 2: Single-Exchange Baseline (Week 2)

1. Run full parameter sweep on Binance data (243 combinations)
2. Run walk-forward validation (4 splits)
3. Establish baseline metrics (Sharpe, drawdown, trade count)
4. Document best parameters for Binance

### Phase 3: Multi-Exchange Validation (Week 3)

1. Run same parameter sweep on Deribit data
2. Compare Binance vs Deribit performance
3. Identify parameters that generalize well
4. Test ensemble approach (weighted combination)

### Phase 4: Production Deployment (Week 4)

1. Deploy best-performing approach (ensemble or single-exchange)
2. Start paper-live validation with multi-exchange data
3. Monitor for regime-specific performance differences
4. Iterate on weighting/ensemble strategy

---

## Data Format Standardization

To support multi-exchange training, standardize data format:

```python
# Standard schema for all exchanges
@dataclass
class StandardizedBar:
    timestamp_ns: int  # Nanosecond precision
    exchange: str  # "binance", "deribit", etc.
    symbol: str  # "BTCUSDT", "BTC-PERPETUAL", etc.
    timeframe: str  # "4h", "1d", etc.
    open: float
    high: float
    low: float
    close: float
    volume: float
    funding_rate: float | None
    open_interest: float | None
```

**Conversion scripts**:
- `scripts/convert_binance_to_standard.py`
- `scripts/convert_deribit_to_standard.py`

---

## Validation Metrics

Track performance across exchanges:

| Metric | Binance | Deribit | Ensemble | Target |
|--------|---------|---------|----------|--------|
| Sharpe Ratio | 6.96 | TBD | TBD | >5.0 |
| Max Drawdown | 4.20% | TBD | TBD | <5% |
| Win Rate | 28.4% | TBD | TBD | >25% |
| Trade Count | 456 | TBD | TBD | >100 |
| Profit Factor | 1.06 | TBD | TBD | >1.5 |

**Success Criteria**:
- Ensemble Sharpe > max(Binance, Deribit) Sharpe
- Ensemble drawdown < max(Binance, Deribit) drawdown
- Parameters generalize across exchanges (correlation > 0.7)

---

## Risk Considerations

### Data Quality
- **Risk**: Deribit data may have gaps or errors
- **Mitigation**: Validate against Binance, flag suspicious bars

### Liquidity Differences
- **Risk**: Deribit has lower liquidity, may not support same position sizes
- **Mitigation**: Scale position sizes by exchange liquidity

### Regime Shifts
- **Risk**: Exchange-specific regimes (e.g., Deribit options expiry)
- **Mitigation**: Track regime-specific performance, adjust weights dynamically

### Overfitting
- **Risk**: Ensemble weights overfitted to historical data
- **Mitigation**: Walk-forward validation, out-of-sample testing

---

## Next Steps

1. **Immediate**: Download Deribit data (540 days)
2. **Week 1**: Convert to standardized format, validate quality
3. **Week 2**: Run parameter sweep on Binance (full 540 days)
4. **Week 3**: Run parameter sweep on Deribit, compare results
5. **Week 4**: Implement ensemble approach, start paper-live

---

## Open Questions

1. **Weighting strategy**: Fixed weights or dynamic based on recent performance?
2. **Regime detection**: Should we use different weights in different regimes?
3. **Execution venue**: Trade on Binance, Deribit, or both?
4. **Data freshness**: How often to retrain/reweight?

---

**Last Updated**: 2026-03-17
**Next Review**: After Deribit data download completes
