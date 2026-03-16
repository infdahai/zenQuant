# Backtest Validation

Date: 2026-03-16

## Purpose

This document records the deterministic backtest evidence requested during PR #5 review.

The goal is not to claim production profitability from a synthetic fixture. The goal is to:

- prove that the historical replay path remains deterministic
- capture a stable regression baseline after the Phase 4-6 strategy changes
- document the exact metrics that should be understood when strategy defaults change

## Method

Validation uses the existing historical replay path in:

- [zenquant/historical/replay.py](/Users/disaster/workspace/code/zenQuant/zenquant/historical/replay.py)
- [tests/test_historical_pipeline.py](/Users/disaster/workspace/code/zenQuant/tests/test_historical_pipeline.py)

Dataset:
- synthetic 90-day BTC dataset
- 1d bars for directional bias
- 4h bars for executable strategy decisions
- synthetic funding and open-interest series

Execution assumptions:
- same production strategy, risk, and execution modules used by replay
- simulated gateway with deterministic immediate fills
- bar-close-only decision timing

Why this is still useful:
- it is deterministic
- it catches semantic changes in playbook, sizing, and exit logic
- it gives a stable regression baseline for future PRs

## Current Baseline

Current default strategy configuration includes differentiated playbook leverage caps:

- `BULL_CONTINUATION = 3.0x`
- `BEAR_DEFENSE = 2.25x`
- `RANGE_LIGHT = 1.5x`
- `CAPITAL_PRESERVATION = 0.35x`

Regression metrics from the deterministic fixture:

| Metric | Value |
|---|---:|
| Net PnL | `221696.97` |
| Final equity | `321696.96542258776` |
| Max drawdown | `0.007603` |
| Sharpe | `5.0015` |
| Profit factor | `5.655152` |
| Trade count | `422` |
| Turnover per day | `210019.28` |
| Win rate | `0.655814` |

These values are locked by:

- [tests/test_historical_pipeline.py](/Users/disaster/workspace/code/zenQuant/tests/test_historical_pipeline.py)

## Interpretation

What changed in this pass:

- non-bull playbooks no longer share a flat `3.0x` cap
- bear exposure is now capped below bull exposure
- range exposure is now capped materially lower than trend exposure

Why this is strategically consistent:

- bear mode should carry lower base directional conviction than bull continuation
- neutral/range mode should be smaller and more tactical

What this validation does not prove:

- out-of-sample robustness on real market history
- superiority versus a separately preserved Phase 3 branch
- live execution realism beyond the current simulated gateway assumptions

## Recommended Future Validation

The next step should be a real historical comparison on actual market data, recording:

- total return
- max drawdown
- Sharpe / Sortino
- profit factor
- turnover
- fee drag
- exit-type counts
- playbook-level contribution

That work should be added as a separate validation/reporting task, not hidden inside strategy refactors.
