# RL / Optuna Explainer

This note explains how ZenQuant actually uses `RL` and `Optuna` so that the
system can be described accurately to technical reviewers, managers, and
research collaborators.

## Executive Summary

In ZenQuant, `RL` and `Optuna` are both constrained to the research layer.
Neither is allowed to rewrite the trading system's architecture.

- `Optuna` is the primary train-split search engine.
- `RL` is a secondary train-split explorer.
- Both operate on top of deterministic backtests.
- Both only tune parameters.
- Final promotion decisions happen in the shared validation pipeline, not
  inside the search algorithm.

The most accurate one-line description is:

> ZenQuant uses RL and Optuna as research-time parameter search tools for a
> deterministic replay framework, not as black-box trading brains.

## 1. What Optuna Does In ZenQuant

Relevant code:

- [zenquant/research/auto_research.py](/Users/disaster/workspace/code/zenQuant/zenquant/research/auto_research.py)
- [zenquant/research/framework_search.py](/Users/disaster/workspace/code/zenQuant/zenquant/research/framework_search.py)

### Role

`Optuna` is the main searcher for framework-level parameter exploration.

Its job is to:

- sample candidate parameter combinations on the `train` split only
- run deterministic backtests for each candidate
- rank candidates by a primary metric such as `sharpe`
- emit a shortlist that later goes through shared validation and promotion logic

### How The Loop Works

Inside `auto_research`, the `bayesian` sweep path:

- builds a parameter search space from strategy and plugin parameters
- creates an `Optuna` study with `TPESampler(seed=42)`
- defines `objective(trial)` by sampling categorical values from the search space
- calls `_run_backtest_worker(...)`
- receives a metrics dictionary from the backtest worker
- returns the selected primary metric to `Optuna`

The backtest worker returns metrics such as:

- `sharpe`
- `sortino`
- `net_pnl`
- `max_drawdown`
- `win_rate`
- `profit_factor`
- `trade_count`
- `fee_drag_pct`

### Result Return Path

ZenQuant does not use a Rust/Python RPC bridge, Redis queue, or external API
hop for Optuna reward propagation.

The result path is much simpler:

1. `objective(trial)` assembles parameters
2. `_run_backtest_worker(...)` executes the deterministic backtest
3. the worker returns a Python metrics dictionary
4. `objective()` returns the chosen metric to `Optuna`

This is an in-process Python research orchestration path.

JSON outputs such as:

- `stage1_search.json`
- `framework_optimization.json`
- `campaign_status.json`

exist for checkpointing, auditability, resumability, and handoff. They are not
the mechanism used to feed reward back into Optuna.

## 2. What RL Does In ZenQuant

Relevant code:

- [zenquant/research/rl_tuner.py](/Users/disaster/workspace/code/zenQuant/zenquant/research/rl_tuner.py)
- [zenquant/app/rl_tune.py](/Users/disaster/workspace/code/zenQuant/zenquant/app/rl_tune.py)
- [tests/test_rl_tuner.py](/Users/disaster/workspace/code/zenQuant/tests/test_rl_tuner.py)

### Role

ZenQuant's RL path is intentionally narrow.

It is not a live trading agent and it does not learn order-by-order behavior.
It is a parameter explorer wrapped in an RL interface.

### Actual Design

The implementation uses:

- `stable_baselines3.PPO`
- a custom `Gymnasium` environment
- `MultiDiscrete` action space

Each action dimension selects one value for one tunable parameter.

Each environment step is:

1. choose a parameter combination
2. run a complete backtest
3. compute reward from the resulting metrics
4. end the episode

This means the RL tuner behaves more like a guided search policy than a
continuous online trading policy.

### What RL Is Allowed To Tune

The tunable parameters come from:

- [zenquant/research/framework_params.py](/Users/disaster/workspace/code/zenQuant/zenquant/research/framework_params.py)

Examples include:

- signal weights such as `w_momentum`, `w_crash`, `w_cvd`
- risk thresholds such as `vpin_threshold`, `crowding_threshold`, `vol_shock_threshold`
- leverage and target volatility such as `max_leverage`, `target_vol_4h`
- execution settings such as `dca_max_adverse_atr`, `trailing_stop_atr_mult`
- optional liquidation research toggles

### What RL Is Not Allowed To Change

RL may not change:

- strategy architecture
- regime logic
- risk precedence
- decision chain ordering
- PaulWei execution philosophy defaults

This constraint is deliberate and is also covered by tests.

## 3. Reward, Observation, And Anti-Overfit Discipline

### Observation Design

ZenQuant's RL environment does not use raw tick-level market state as the
training observation.

Instead, it uses a compact summary of prior backtest outcomes, including:

- normalized train Sharpe
- normalized PnL
- max drawdown
- normalized trade count
- dev Sharpe proxy
- dev/train ratio proxy

This makes the RL loop closer to parameter preference learning than direct
market policy learning.

### Reward Design

Base reward is derived from backtest quality, centered on Sharpe.

Additional penalties are applied when:

- `max_drawdown > 25%`
- `trade_count < 20`

This prevents the tuner from preferring degenerate solutions that look good on
one metric while violating basic risk or activity expectations.

### Anti-Overfit Discipline

The split contract is defined in:

- [zenquant/research/splits.py](/Users/disaster/workspace/code/zenQuant/zenquant/research/splits.py)
- [docs/DATASET_SPLIT_POLICY.md](/Users/disaster/workspace/code/zenQuant/docs/DATASET_SPLIT_POLICY.md)

Current BTC split:

- `train`: `2020-01-01` to `2023-12-31`
- `dev`: `2024-01-01` to `2025-12-31`
- `test`: `2026-01-01` to latest

The RL tuner follows that contract:

- training happens only on `train`
- episodes sample random 6-month windows from the train period
- candidates are deduplicated before dev evaluation
- `dev` is used for candidate ranking
- `test` remains reserved for final historical acceptance

So the key point is not "RL found a high train score." The key point is whether
the candidate survives the same shared validation structure as every other
search path.

## 4. The Authoritative Path

Relevant code and docs:

- [scripts/run_framework_campaign.py](/Users/disaster/workspace/code/zenQuant/scripts/run_framework_campaign.py)
- [docs/HARNESS_ARCHITECTURE.md](/Users/disaster/workspace/code/zenQuant/docs/HARNESS_ARCHITECTURE.md)
- [CLAUDE.md](/Users/disaster/workspace/code/zenQuant/CLAUDE.md)

ZenQuant does not let Optuna or RL self-declare a winner.

The shared campaign path is:

- Stage 1A: Optuna train-only search
- Stage 1B: RL train explorer
- Stage 1C: dev ranking and robustness checks
- Stage 2: frozen test verdict
- Stage 3: authoritative JSON and Markdown outputs

This matters because:

- RL and Optuna are candidate generators
- promotion is controlled by the validation harness
- auditability is preserved in shared campaign artifacts
- search tools cannot bypass the regime/risk architecture

## 5. Suggested Interview Framing

For a manager or technical lead, a good plain-language explanation is:

> In ZenQuant, RL and Optuna are not treated as magic trading engines. They are
> constrained to parameter search on top of a deterministic backtest harness.
> Optuna is the primary searcher and RL is a secondary explorer. Neither is
> allowed to change the strategy skeleton or risk priority. The real decision is
> made later by the shared train/dev/test validation pipeline.

If asked how Optuna receives the result:

> It is a direct Python research orchestration path. The Optuna objective
> samples parameters, calls the backtest worker, gets a metrics dict back, and
> returns the primary metric. JSON files are for checkpointing and audit, not
> for reward transport.

If asked how "real" the RL is:

> It behaves more like PPO-driven parameter exploration than an online trading
> policy. One step is not "place an order." One step is "choose parameters and
> run a full backtest."

## 6. Boundaries And Caveats

- This explanation describes the current repository implementation, not a
  generic RL trading architecture.
- The explanation is intended for management and technical review, not for a
  formal academic discussion of RL theory.
- If the implementation changes, this note should be kept aligned with the
  authoritative sources listed above.
