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

This baseline is the preferred learning target for cadence, time leverage,
staged inventory, and fee-efficiency research.

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
