# Statistical Budget Tracker

**Rule**: Train set (2020-2023) is free. Dev set (2024-2025) track here. Test set (2026+) quarterly blind only.

**Any parameter change triggered by Test results = OVERFIT.**

## Dev Set Experiments (2024-2025)

| Date | Experiment | Sharpe Delta | Action | Budget Cost |
|------|------------|-------------|--------|-------------|
| 2026-04-04 | Signal ablation (8 signals) | +0.46 (macro disabled) | Disabled macro_regime, vol_mr | HIGH |
| 2026-04-04 | Re-enable check (interaction) | +0.27 (cvd re-enabled) | Re-enabled cvd, crowding | MEDIUM |
| 2026-04-04 | PaulWei asymmetric DD | +0.15 | DD steps widened for longs | LOW |
| 2026-04-04 | Walk-forward (8 windows) | ratio=1.11 | Validated, no changes | LOW |
| 2026-04-04 | Individual macro IC | IWM only SIGNAL | No new signals added | LOW |
| 2026-04-04 | 1H entry optimization | DD -46% | Kept as entry optimizer | MEDIUM |
| 2026-04-04 | Basis IC analysis | +0.021 NOISE | No signal, risk guard only | LOW |

## Test Set (2026+ — SECRET BENCHMARK)

| Date | Sharpe | DD | Pass/Fail | Action |
|------|--------|----|-----------|--------|
| 2026-04-04 | >.5 | <30% | PASS | First benchmark |
| 2026-04-04 | >.5 | <30% | PASS | Post-config extraction |

## Budget Assessment

Total Dev experiments: ~30 runs
Risk level: **HIGH** — Dev set extensively explored
Next session: Train-only optimization, Dev for 1x final validation
