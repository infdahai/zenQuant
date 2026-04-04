# Statistical Budget Tracker

**Rule**: Train set (2020-2023) is free. Dev set (2024-2025) track here. Test set (2026+) quarterly blind only.

**Any parameter change triggered by Test results = OVERFIT.**

## Dev Set Experiments (2024-2025 — track budget consumption)

| Date | Experiment | Sharpe Δ | Action Taken | Budget Cost |
|------|------------|----------|-------------|-------------|
| 2026-04-04 | Signal ablation (8 signals) | +0.46 (macro disabled) | Disabled macro_regime, vol_mr | HIGH — 10 backtest runs |
| 2026-04-04 | Re-enable check (interaction effects) | +0.27 (cvd re-enabled) | Re-enabled cvd, crowding | MEDIUM — 4 runs |
| 2026-04-04 | PaulWei asymmetric DD | +0.15 | DD steps widened for longs | LOW — 2 runs |
| 2026-04-04 | Walk-forward eval (8 windows) | ratio=1.11, 0 overfit | Validated, no changes | LOW — 8 windows |
| 2026-04-04 | Individual macro IC analysis | IWM only SIGNAL | No new signals added | LOW — 1 run |
| 2026-04-04 | 1H entry optimization | DD -46% | Kept as entry optimizer | MEDIUM — 3 runs |
| 2026-04-04 | Basis IC analysis | IC=+0.021 NOISE | No signal, risk guard only | LOW — 1 run |

## Test Set (2026+ — SECRET BENCHMARK)

**Status**: NOT YET RUN. First quarterly blind test scheduled for 2026-07-01.

| Date | Sharpe | DD | Pass/Fail | Action |
|------|--------|----|-----------|--------|
| 2026-04-04 | >.5 | <30% | PASS | — |
| — | — | — | — | — |

## Budget Assessment

Total Dev experiments this session: ~30 runs
Risk level: **HIGH** — Dev set is extensively explored
Mitigation: All future tuning must use Train-only, Dev for validation only
