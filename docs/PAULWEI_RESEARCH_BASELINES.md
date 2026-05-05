# PaulWei Research Baselines

This repo keeps PaulWei-derived baselines as explicit research configs. They are not
primary execution truth and must not be used for promotion without the normal
train/dev/test and robustness gates.

## Baselines

| Baseline | Config | Role |
|---|---|---|
| `paulwei_baseline` | code-frozen in `zenquant.research.comparison` | Legacy execution/risk reference. |
| `paulwei_public_archive_baseline` | `config/research_baselines/paulwei_public_archive_2020_05_2026_04.json` | Full public-ledger behavior mapping, research-only. |
| `paulwei_mature_style_baseline` | `config/research_baselines/paulwei_mature_style_2021_05_28.json` | Primary PaulWei learning baseline from mature-style behavior. |

## Mature Style Cut

The mature-style baseline starts at `2021-05-28T12:00:00Z`, after recovery from
the 2021-04/05 cash-flow adjusted drawdown. This cut excludes the early aggressive
period and uses the later behavior profile:

- `position_switches_per_30d`: `1.6807`
- `median_adds_per_episode`: `18.0`
- `median_reduces_per_episode`: `17.0`
- `win_median_hold_hours`: `25.8007`
- `loss_median_hold_hours`: `34.3481`
- `max_drawdown_pct`: `0.1911`

This baseline is the preferred learning target for cadence, low switch count,
staged unwind, and fee-efficiency research.

## Tranche Behavior

PaulWei's low switch cadence must not be interpreted as low execution activity.
The public archive contains many submitted orders that never trade, and filled
position episodes are built and unwound through many partial trade executions.
Use the tranche profile to separate:

- submitted orders
- trade executions that actually filled
- net position episodes and direction switches
- add/reduce fill rows inside each episode

The mature BTC profile is the better learning target for intent design: low
direction switching, many small adds, and many small reduces.

## Early Convexity Audit

The early public-ledger period should not be summarized as a single headline USD
multiple. The research target is `paulwei_early_convexity_train_only`: a
research-only audit that separates BTC-unit wealth, actual wallet USD, and
cash-flow adjusted USD.

This distinction matters for this repo because the strategy is USDT-margined.
BTC-collateral convexity is not a direct promotion target. The transferable
parts are phase-aware time-in-market, staged add/reduce behavior, and explicit
cash-flow adjusted evaluation with actual-wallet drawdown reporting.

## Dual-Phase Learning

PaulWei has two different behaviors worth learning, and they should not be
collapsed into one headline Sharpe target.

1. `early_convexity_usdt_projection`: learn the early expansion mechanism as a
   USDT-safe projection. The transferable pieces are time leverage in confirmed
   expansion regimes, staged adds when the position is working, and delayed
   flips. The forbidden piece is copying BTC-collateral convexity or the early
   public-ledger drawdown tolerance into a USDT-margin strategy.
2. `mature_low_switch_control`: learn the later control mechanism. The
   transferable pieces are low direction-switch cadence, many small fills inside
   each position episode, staged reduces, and fee-efficient flip/reduce behavior.

The train-only search mode for this combined target is
`paulwei_dual_phase_train_only`. It enables a Stage1 objective adjustment only in
that mode; existing PaulWei balanced-tranche and legacy search modes keep their
previous scoring.

The narrowed repair mode is `paulwei_dual_phase_fee_repair_train_only`. It keeps
the same two learning templates, but adds a third training context label,
`dual_phase_fee_repair`, and narrows the grid around lower target volatility,
slower entry/flat confirmation, and longer time-leverage holds. This mode is for
repairing the v102 train fee miss without moving the fix into execution-layer
order filtering.

## Commands

Compare default current and frozen PaulWei:

```bash
uv run python scripts/compare_variants.py --root data/raw/binance --symbol BTCUSDT --split dev --skip-walk-forward
```

Compare with the full public archive baseline:

```bash
uv run python scripts/compare_variants.py --root data/raw/binance --symbol BTCUSDT --split dev --skip-walk-forward --include-paulwei-public-baseline
```

Compare with the mature-style baseline:

```bash
uv run python scripts/compare_variants.py --root data/raw/binance --symbol BTCUSDT --split dev --skip-walk-forward --include-paulwei-mature-baseline
```

Compare all PaulWei research baselines together:

```bash
uv run python scripts/compare_variants.py --root data/raw/binance --symbol BTCUSDT --split dev --skip-walk-forward --include-paulwei-public-baseline --include-paulwei-mature-baseline
```

Build the order/fill/episode tranche profile:

```bash
uv run python scripts/analyze_paulwei_tranche_behavior.py --data-root data/raw/paulwei_public --output-dir results/paulwei_tranche_behavior
```

Build the early BTC-unit convexity audit:

```bash
uv run python scripts/analyze_paulwei_early_convexity.py --data-root data/raw/paulwei_public --spot-root data/raw/binance_spot --output-dir results/paulwei_early_convexity
```

Run a smoke search for the dual-phase learning objective:

```bash
uv run python scripts/run_framework_campaign.py --root data/raw/binance --symbol BTCUSDT --budget smoke --optuna-trials 12 --rl-episodes 0 --search-space-mode paulwei_dual_phase_train_only --development-dev-gate --output-dir results/regime_objective_candidate_search_v102_paulwei_dual_phase_smoke
```

Run a smoke search for the dual-phase fee-repair objective:

```bash
uv run python scripts/run_framework_campaign.py --root data/raw/binance --symbol BTCUSDT --budget smoke --optuna-trials 12 --rl-episodes 0 --search-space-mode paulwei_dual_phase_fee_repair_train_only --development-dev-gate --output-dir results/regime_objective_candidate_search_v103_paulwei_dual_phase_fee_repair_smoke
```
