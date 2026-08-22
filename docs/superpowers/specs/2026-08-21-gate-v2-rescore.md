# Gate v2 Candidate Rescore Specification

## Objective

Re-score selected historical BTC candidates without parameter search or frozen-test access. The experiment determines whether another bounded search is justified or whether the next design step must be a deterministic regime router.

## Candidate Set

- frozen PaulWei baseline
- v118 rank-1 low-cadence candidate
- v163 rank-1 temporal candidate
- v164 rank-1 temporal candidate
- August 2026 rank-1, rank-2, and rank-3 candidates

Candidate parameters remain sourced from their existing train/dev research reports. They are not copied into the manifest and are not modified by this experiment.

## Evaluation Scope

- BTCUSDT only
- authoritative research replay contract from the source campaigns: 5m execution, 1h context, and 5m strategy execution
- windows: full train, calendar years 2020, 2021, 2022, 2023, and late dev (2025)
- deterministic perturbation seeds: 42, 123, 456, 789, 1024
- price noise: 0.1%
- volume noise: 5%
- bar dropout: 2%
- no Optuna, RL, parameter mutation, high-budget training, or frozen-test evaluation

## Cost Stress

Apply additional round-trip cost scenarios of 0, 5, 10, and 15 basis points to realized turnover after each replay:

`stressed_net_pnl = reported_net_pnl - turnover_notional * extra_cost_bps / 10_000`

Turnover notional is reconstructed from the report's `turnover_per_day` and exact window duration. This is deliberately a deterministic rescore of realized behavior; it does not claim to model order-book impact or change the strategy's decisions.

## Gate v2 Policy

Hard blockers per seed:

- any window drawdown at or above 20%
- more than one negative-PnL regime window
- worst regime Sharpe below -2.0
- late-dev net PnL not positive
- full-train profit factor after fee below 1.0
- full-train reduce-action profit factor below 1.0 when reduce actions exist
- position switches above 5 per day
- full-train or late-dev PnL not positive under the additional 15bp stress
- equity multiple above 10 without a capacity model
- missing required metrics or replay errors
- any required window with zero completed trades

Campaign pass requires at least four of five seeds to pass.

Soft diagnostics:

- position switches above 1 and at most 5 per day
- total fills above 1 per day
- protective reduce share below 0.75

Monthly switch cadence, raw Sharpe, raw PnL, and protective reduce share remain diagnostic and cannot independently promote a candidate.

## Outputs

- one immutable comparison payload per seed
- `gate_v2_rescore.json` containing the complete gate evidence and terminal recommendation
- `gate_v2_rescore.md` containing a compact candidate matrix

The only terminal recommendations are:

- `bounded_search_allowed`
- `design_regime_router_before_more_training`
