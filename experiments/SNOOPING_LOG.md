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
| 2026-04-06 | P1/P3 IC analysis | P1=0.001 P3=0.014 NOISE | P3 disabled, P1 kept (auto-neutral) | MEDIUM — 1 run |
| 2026-04-06 | P2 funding ablation (3-way) | asym 2.04 vs sym 2.21 vs off 1.41 | Restored symmetric (2.0/-2.0) | MEDIUM — 3 runs |

## Test Set (2026+ — SECRET BENCHMARK)

| Date | Sharpe | DD | Pass/Fail | Action |
|------|--------|----|-----------|--------|
| 2026-04-04 | >.5 | <30% | PASS | First benchmark |
| 2026-04-04 | >.5 | <30% | PASS | Post-config extraction |

| 2026-04-07 | Baseline update: 4 new execution features: flip_immediate, adaptive_pyramid, gradual_exit, DCA distance gating. Sharpe 1.75→2.29, DD 9.9→12.5% | Sharpe +0.55 | Updated real_data_baseline.json | LOW — 1 run |

## Budget Assessment

Total Dev experiments: ~30 runs
Risk level: **HIGH** — Dev set extensively explored
Next session: Train-only optimization, Dev for 1x final validation
