# Validation Results

Historical validation evidence and baseline metrics.

Strict temporal split policy now governs all new parameter research:

- `train`: `2020-01-01` to `2023-12-31`
- `dev`: `2024-01-01` to `2025-12-31`
- `test`: `2026-01-01` to latest

See [`DATASET_SPLIT_POLICY.md`](/Users/disaster/workspace/code/zenQuant/docs/DATASET_SPLIT_POLICY.md).

## Current Historical Status (2026-04-07)

The authoritative validation handoff path for the current strategy is:

- Handoff spec: [`LIVE_VALIDATION_HANDOFF_SPEC.md`](/Users/disaster/workspace/code/zenQuant/docs/LIVE_VALIDATION_HANDOFF_SPEC.md)
- Execution record: [`HISTORICAL_VALIDATION_2026_04_07.md`](/Users/disaster/workspace/code/zenQuant/docs/HISTORICAL_VALIDATION_2026_04_07.md)

Current conclusion:

- `2023-08` bearish-grind repair is validated on historical replay
- `walk_forward_eval.py` is now treated as a `train -> dev` selection-layer check
- `adversarial_stress.py --historical --split dev|test` is split-aware
- `current` and `paulwei_baseline` are still identical on the current historical path, so the framework is validated but no superior promoted variant has been established yet

Use the handoff spec for re-run commands, paper-live startup, and acceptance criteria. Treat this file as a summary, not as the source of truth for test contracts.

---

## Current Performance (v2.0 + Pyramid, 2026-04-04)

**Architecture**: confidence × weight × value + pyramid execution + equity risk overlay

### Train Set (2020-2023)

| Metric | Value |
|--------|-------|
| Sharpe | 1.53 |
| Max DD | 6.2% |
| Losing Years | 0/4 |
| TREND PnL | +$40,883 |

| Year | Sharpe | Max DD |
|------|--------|--------|
| 2020 | +2.0 | 3.5% |
| 2021 | +1.1 | 3.7% |
| 2022 | +0.9 | 4.5% |
| 2023 | +1.8 | 6.2% |

### Test Set (2024-2026)

| Year | Sharpe | Max DD |
|------|--------|--------|
| 2024 | +2.1 | 4.3% |
| 2025 | +1.3 | 11.5% |
| 2026 | +1.2 | 1.8% |

### Stress Test: 8/8 PASS

| Test | Sharpe | DD |
|------|--------|-----|
| Full period | 3.54 | 15.0% |
| 2022 bear market | 1.17 | 8.0% |
| 2025 chop market | 1.14 | 11.4% |
| Noise seed=42 | 3.00 | 8.6% |
| Noise seed=123 | 2.94 | 9.6% |
| Noise seed=456 | 3.75 | 10.2% |
| Noise seed=789 | 3.33 | 6.7% |
| Noise seed=1024 | 4.06 | 13.7% |

---

## Signal IC Analysis (Train Set, 12,766 bars)

| Signal | IC | Verdict |
|--------|-----|---------|
| crash_reversal (conditional) | -0.21 | STRONG (behavioral alpha) |
| vol_mean_reversion | +2.98%/5d excess | STRONG (66% win rate) |
| TLT momentum | +0.085 | MODERATE |
| multi_momentum | -0.08 | WEAK (confidence self-adjusts) |
| funding_alpha | -0.03 | WEAK |
| cvd_slope | -0.03 | WEAK |
| VIX/DXY | -0.01 | NOISE (but kept as risk modifier) |
| BTC/ETH divergence | ≈0 | NOISE (not used) |
| On-chain (MVRV) | 0.15-0.25 monthly | N/A (not collected) |

---

## Key Architecture Decisions

1. **Confidence-weighted direction** — Signals self-regulate via confidence; no manual regime classification needed
2. **Pyramid execution** — 30% initial, +30% on profit, don't add on loss (PaulWei pattern)
3. **Adaptive IC weights** — Online learning: signals with declining IC automatically get reduced weight
4. **Equity risk overlay** — QQQ+IWM crash → reduce BTC leverage (not direction signal)
5. **High-cycle sizing** — cycle_position > 1.3 → reduce leverage (not stop trading)

---

## Overfitting Assessment

- Train/Test Sharpe: 1.53 / 1.74 (test > train → not classical overfitting)
- 50+ experiments on 12,766 bars → implicit multiple testing risk
- Structural changes (confidence architecture, pyramid) more likely to generalize than parameter tuning
- Live trading is the only true out-of-sample validation

---

## Validation Infrastructure

| Layer | Tool | Status |
|-------|------|--------|
| Historical backtest | HistoricalBacktest | Working, 3s per run |
| Stratified evaluation | scripts/stratified_eval.py | Per-year, per-regime |
| Stress testing | scripts/stress_test.py | 8/8 pass |
| IC analysis | scripts/factor_ic_analysis.py | All factors |
| Decision logging | zenquant/research/decision_logger.py | Full chain per bar |
| Clean tuning campaign | scripts/run_framework_campaign.py | Authoritative train/dev/test path |
| Domain randomization | PerturbationConfig | 5-seed robustness |
| AutoResearch | zenquant/research/auto_research.py | Internal/dev-only worker library, not final promote path |
| Factor health | tests/test_factor_health.py | Gate 0 |
| Paper trading | scripts/paper_trade.py | Needs stable network |
| HF features | zenquant/indicator/hf_features.py | Awaiting 5min data |
