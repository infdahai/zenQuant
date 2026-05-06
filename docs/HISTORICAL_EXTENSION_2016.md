# Historical Extension to 2016

This repository now supports extending the BTC research data window in two layers:

1. slow daily context
2. replay-base candidates

These layers must remain distinct.

## Layer 1: Daily Context

Use this layer first for:

- BTC MVRV
- BTC daily spot reference series
- long-cycle valuation and regime work

Recommended commands:

```bash
uv run python scripts/download_spot_data.py --symbol BTCUSDT --start-date 2016-01-01 --timeframes 1d,4h,1h
uv run python scripts/convert_csv_to_parquet.py --source binance_spot --symbol BTCUSDT
uv run python scripts/download_macro_data.py --btc-mvrv-csv <path-to-mvrv-csv>
```

This layer is safe to use for:

- regime priors
- risk budgeting
- explainability
- long-cycle stress tests

## Layer 2: Replay-Base Candidates

Do not automatically promote 2016-2019 data into the main replay truth.

Before using 2016-2019 in formal training windows, verify:

- data continuity
- point-in-time correctness
- replay parity assumptions
- funding/OI/liquidation coverage compatibility
- spot/perp truth boundaries

Current rule:

- `2016-2019` may be used first for research-only validation.
- It is not formal train/dev/test truth until the replay-base contract is reviewed.

## Truth Boundary

Do not silently merge:

- BTC spot historical context
- later futures/perps execution truth

If a training or validation run uses mixed-era data, the result artifact must declare the source contract explicitly.

## Mechanical Coverage Audit

Use this before any training-window expansion:

```bash
uv run python scripts/data_coverage_audit.py --data-root data/raw --symbol BTCUSDT --output-dir results/data_source_review
```

The audit writes:

- `results/data_source_review/data_coverage_audit.json`
- `results/data_source_review/data_coverage_audit.md`

The output separates physical parquet/csv coverage from training integration status. A source may have local data and
still be blocked from formal train truth.

## BitMEX 2017-2020 Replay Candidate

BitMEX XBTUSD can be reviewed as a pre-2020 inverse-perpetual replay-base candidate, but it is not automatically part of
formal train/dev/test truth.

```bash
uv run python scripts/review_bitmex_replay_candidate.py --data-root data/raw --symbol XBTUSD --normalized-symbol BTCUSD --output-dir results/data_source_review
```

The contract writes:

- `results/data_source_review/bitmex_replay_candidate_contract.json`
- `results/data_source_review/bitmex_replay_candidate_contract.md`

This candidate must stay separate from Binance spot context until coverage, funding/contract semantics, point-in-time
replay, and parity checks pass.
