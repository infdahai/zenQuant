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
