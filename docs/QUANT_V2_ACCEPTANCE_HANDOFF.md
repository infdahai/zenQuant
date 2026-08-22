# Quant V2 Acceptance Handoff — 2026-08-23

This file is the canonical compact evidence ledger for the frozen Quant V2
experiment and its late-development attribution. It contains one current set of
numbers. Earlier superseded hashes and tables have been removed.

## Decision

```text
verdict                 = BLOCKED_PAUSE_PROMOTION_AND_TRAINING
terminal_recommendation = stop_donor_training
```

The complete frozen matrix ran successfully with no parameter search: six
windows times five perturbation seeds, 30/30 runs. V2 passes MDD, train episode
PF, cadence and execution-purity gates. It fails late-dev at baseline and under
the additional 15bp cost stress on all five seeds. Robustness is `0/5`, below
the required `4/5`.

## Frozen identity

- Candidate: `august_rank1_20260821`
- Manifest SHA-256: `c982d074b24ec21235dfefbd972d936dc4f5a4ab0360964013576c90729bfbe0`
- Candidate params SHA-256: `d86c92e8837c2febc04d0c2935299abc30b0636fb5d9a7c9e244d670d3499381`
- Profile SHA-256: `ce4fc640b3a0ae2dfb8b6d4bd96652f431a20b5581ce5d2fa67e0722f4841d05`
- Seeds: `42, 123, 456, 789, 1024`
- Perturbation: price noise `0.001`, volume noise `0.05`, bar dropout `0.02`
- Cost stress: `0 / 5 / 15bp`, with 15bp added to observed baseline cost
- Formula identity sets across 30 runs: `1`
- Alpha-config identity sets across 30 runs: `1`

## Canonical artifact identity

Generated files remain ignored and non-promotable under `results/`.

| Artifact | SHA-256 | Size |
| --- | --- | ---: |
| `results/quant_v2_acceptance/architecture_attribution.json` | `1f327c7c786f511fbba6ac1dd2ca4df395891f1aeb1879714c0ed2af1a87bea2` | `1,003,639,954` bytes |
| `results/quant_v2_acceptance/acceptance_summary.json` | `7d21e514e18f27611f793e2c7f06071ddb121de4654d0bf57a31d23f2f7da637` | `5,822` bytes |
| `results/quant_v2_late_dev_cost_attribution/late_dev_cost_attribution.json` | `b7cb1c84c70fab54b728f0cd6287fcde6fa4c107bf8f3de541a6cffd36de734e` | `92,571` bytes |
| `results/quant_v2_late_dev_cost_attribution/late_dev_cost_attribution.md` | `3f551c6f94ab9c40d8ee5fc4bbf134284c4d2b21981120c5ce562cef166f68a4` | `28,173` bytes |

The frozen 1GB experiment was run once. Final attribution fixes reused that
exact artifact and reran only the deterministic analyzer.

## Hard-gate result

| Gate | Result | Evidence |
| --- | --- | --- |
| A5 MDD below 20% on all seeds | PASS | worst `7.4169%` |
| Train A5 episode PF >= 1.0 | PASS | minimum `1.1479` |
| Late-dev baseline positive | **FAIL** | net PnL range `-6,677.03` to `-1,579.17` |
| Late-dev positive at extra 15bp | **FAIL** | net PnL range `-17,814.95` to `-10,736.90` |
| Hostile risk controlled | **NOT EVALUABLE** | no frozen hostile window/threshold |
| Switch cadence | PASS | `0.4305–0.5210/day`, below `1/day` |
| Execution purity | PASS | created exposure/reversal/overshoot all `0` |
| OptionsState order/position effect | PASS | OFF/ON execution material identical |
| Robustness at least 4/5 | **FAIL** | `0/5` |

Per-seed final-stage evidence:

| Seed | Max A5 MDD | Train episode PF | Late-dev baseline net | Late-dev extra-15bp net | Max switches/day |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 42 | `6.6310%` | `1.2019` | `-1,579.17` | `-10,736.90` | `0.4305` |
| 123 | `6.5914%` | `1.1479` | `-5,911.57` | `-15,196.07` | `0.4497` |
| 456 | `5.7272%` | `1.3328` | `-4,714.50` | `-14,148.18` | `0.4717` |
| 789 | `7.4169%` | `1.2871` | `-6,677.03` | `-17,814.95` | `0.5210` |
| 1024 | `4.8447%` | `1.1998` | `-2,170.89` | `-12,503.99` | `0.4497` |

## Direction and cost attribution

Late-dev direction hit rate is below 50% on every seed:

- A1 sign-only: `47.21–48.22%`; baseline net `-61,034.58` to `11,717.10`;
  extra-15bp negative for 5/5.
- A1 confidence-weighted: the same hit rate; baseline net `-22,544.75` to
  `3,132.11`; extra-15bp negative for 5/5.
- A5: `46.38–49.38%`; baseline and extra-15bp negative for 5/5.

| Evidence family | Status | Supporting seeds |
| --- | --- | ---: |
| `directional_edge` | `CONSISTENT` | `5/5` |
| `confidence_failed_to_rescue` | `CONSISTENT` | `5/5` |
| `cost_flip` | `UNATTRIBUTABLE` | `0/5` |
| `regime_localization` | `UNATTRIBUTABLE` | `0/5` |
| `episode_tail` | `UNATTRIBUTABLE` | `0/5` |
| `execution_shortfall` | `CONSISTENT` | `5/5` |

Here `directional_edge=CONSISTENT` means that direction-failure attribution is
consistent across seeds; it does not assert positive directional alpha.

Confidence weighting improved every A1 extra-15bp result but rescued none:

| Seed | A1_SIGN 15bp PnL | A1_SCORE 15bp PnL | Improvement | Rescued |
| ---: | ---: | ---: | ---: | --- |
| 42 | `-226,512.04` | `-72,673.40` | `+153,838.65` | no |
| 123 | `-184,678.85` | `-62,604.80` | `+122,074.05` | no |
| 456 | `-235,784.58` | `-77,817.96` | `+157,966.62` | no |
| 789 | `-202,088.14` | `-65,273.33` | `+136,814.81` | no |
| 1024 | `-156,132.90` | `-50,961.44` | `+105,171.46` | no |

The correct reading is that confidence compresses loss magnitude but does not
restore positive OOS expectancy. No regime-objective redesign artifact was
created because the real evidence does not support that recommendation.

## Execution attribution

All five A5 execution surfaces are complete. Actual fee, slippage and
implementation shortfall are serialized observations, not recomputed strategy
decisions.

| Seed | Positive fills | Fills/day | Switches/day | Actual shortfall | Extra 15bp cost |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 42 | `645` | `1.7671` | `0.4004` | `3,357.83` | `9,157.73` |
| 123 | `665` | `1.8219` | `0.4497` | `3,404.32` | `9,284.50` |
| 456 | `673` | `1.8438` | `0.4442` | `3,459.01` | `9,433.67` |
| 789 | `763` | `2.0904` | `0.5210` | `4,083.91` | `11,137.93` |
| 1024 | `725` | `1.9863` | `0.4497` | `3,788.80` | `10,333.10` |

The baseline-to-15bp bridge reconciles to floating-point tolerance for every
seed. `execution_shortfall` corroborates failed economics but does not establish
that execution is the primary causal defect.

## Options and data readiness

Frozen OptionsState OOS evidence remains `INSUFFICIENT_SAMPLE`: the real chain
begins in 2026 and does not overlap the frozen 2025 late-dev window. There is no
activation recommendation.

Read-only status on 2026-08-23:

- Deribit option chain: collector running, observation-ready and research-ready;
- Deribit block flow: collector running, observation-ready, `609` rows in 24h;
- Binance liquidation history: blocked by missing history;
- Hyperliquid public history: partial, estimated coverage `0.6509`;
- merged liquidations: minimum research-ready, not promotion-ready.

Continue collection. Do not use partial liquidation history or OptionsState to
promote or alter the current strategy.

## Verification state

- Changed-file Ruff and format checks: pass.
- Full pytest: `1480 passed, 10 skipped, 34 subtests passed`.
- `zenquant-check` Gates 0/B/C/D/E: pass.
- Gate A is blocked only by three unchanged pre-existing format drifts:
  `scripts/analyze_intent_churn_attribution.py`,
  `zenquant/data/event_calendar.py`, and
  `zenquant/data/liquidation_pipeline.py`.
- Gate F passed when the current worktree code was run against the parent
  checkout's read-only data: 4/4 windows, 0 overfit, average OOS ratio `1.19`.

## Rerun

Use historical data read-only and write generated outputs into the current
worktree:

```bash
uv run python scripts/run_architecture_experiment.py \
  --root /Users/disaster/workspace/code/zenQuant/data/raw/binance \
  --output results/quant_v2_acceptance/architecture_attribution.json

uv run python scripts/build_quant_v2_acceptance_summary.py \
  --input results/quant_v2_acceptance/architecture_attribution.json \
  --output results/quant_v2_acceptance/acceptance_summary.json

uv run python scripts/analyze_quant_v2_late_dev_cost.py \
  --input results/quant_v2_acceptance/architecture_attribution.json \
  --output-dir results/quant_v2_late_dev_cost_attribution

shasum -a 256 results/quant_v2_acceptance/architecture_attribution.json \
  results/quant_v2_acceptance/acceptance_summary.json \
  results/quant_v2_late_dev_cost_attribution/late_dev_cost_attribution.json
uv run pytest -q
uv run zenquant-check
```

The acceptance summary exits `0` only for acceptance, `1` for a valid blocked
verdict and `2` for invalid or incomplete input. The current valid result exits
`1`. Do not hide this exit code in automation and do not rerun the 1GB experiment
merely to regenerate the attribution report.

## Current boundary

- Do not promote, paper-live or continue parameter training.
- Do not reinterpret `UNATTRIBUTABLE` as evidence for a regime redesign.
- Do not activate OptionsState.
- Do not modify frozen windows, seeds, costs or identities in response to failure.
- Any time-space hypothesis is a new, separately approved shadow study; 2025
  late-dev cannot be reused as its unbiased confirmation set.
