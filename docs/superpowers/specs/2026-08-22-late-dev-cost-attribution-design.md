# Late-Dev Cost Attribution and Regime-Objective Preparation

## Decision and scope

The frozen Quant V2 result remains **blocked**. This work does not rerun
training, search parameters, alter the August rank-1 donor, change the
window/seed/cost matrix, activate OptionsState, or alter any production path.

The work has two ordered tracks:

1. The authoritative track attributes the frozen late-dev extra-15bp failure.
2. The preparation track records a regime-objective redesign hypothesis only
   after the attribution output identifies a causal failure family.

The first track is the only executable research work in this plan. The second
track produces no strategy code, profile change, campaign, or promotion claim.

## Frozen inputs

The attribution CLI consumes the existing ignored raw artifact and validates
its matrix before computing any output:

- `results/quant_v2_acceptance/architecture_attribution.json`
- manifest identity `c982d074b24ec21235dfefbd972d936dc4f5a4ab0360964013576c90729bfbe0`
- August rank-1 candidate, six fixed windows, five fixed perturbation seeds,
  and `0 / 5 / 15bp` stress scenarios

It must reject incomplete, duplicate, altered, or mismatched matrix inputs.
No live data, current OptionsState data, or repo-local auxiliary factor is a
permitted attribution input.

The raw artifact is reproducible but is not present in the current checkout
after temporary-worktree cleanup. After the analyzer passes all tests, regenerate
it once with the frozen `run_architecture_experiment.py` command recorded in the
acceptance handoff. This replay is not training or search. The analyzer still
fails closed when its input is absent; it never starts regeneration implicitly.

## Track 1: late-dev cost-failure attribution

### Questions

For each seed and each A1/A2/A3/A4/A5 stage, determine whether the late-dev
extra-15bp failure is primarily explained by one or more of:

- negative directional edge;
- confidence having no incremental value;
- turnover or fill frequency;
- estimated fee/slippage and breakeven burden;
- implementation shortfall;
- a regime or playbook subset;
- closed position episode tail loss; or
- insufficient evidence in the frozen artifact.

The output must distinguish an observed accounting contribution from a causal
claim. It may say `UNATTRIBUTABLE` when the raw artifact does not support a
causal decomposition.

### Inputs and joins

Use only point-in-time rows already serialized in the artifact:

- A1 direction-attribution rows for hit rate, sign-only PnL, and
  confidence-weighted PnL;
- A2-A5 execution rows for target delta, fills, fee/slippage estimates,
  breakeven move, forward returns, and implementation shortfall;
- closed episode ledgers for side, duration, MFE/MAE, gross PnL, fees,
  funding, and net PnL;
- the existing MarketState/playbook labels attached at the decision cutoff.

All aggregations are grouped by `(seed, stage, window, stress_bps)` first.
Cross-seed conclusions require the same direction in at least four of five
seeds. Right-censored open episodes stay visible but never enter episode PnL
or profit-factor numerators/denominators.

### Outputs

Write a compact, deterministic JSON and Markdown report under
`results/quant_v2_late_dev_cost_attribution/` with:

- artifact and manifest identities plus a canonical input hash;
- a per-seed late-dev bridge from baseline net PnL to extra-15bp net PnL;
- direction and confidence contribution tables;
- turnover, fills/day, switch cadence, estimated cost, and breakeven tables;
- regime, playbook, side, action, and closed-episode contribution tables;
- implementation-shortfall and A5 observation summaries;
- explicit confidence level: `CONSISTENT`, `MIXED`, `INSUFFICIENT_SAMPLE`,
  or `UNATTRIBUTABLE`;
- one terminal recommendation:
  `stop_donor_training`, `prepare_regime_objective_redesign`, or
  `insufficient_evidence_no_strategy_change`.

The CLI has no parameter, window, seed, cost, or strategy override flags.

### Boundaries

The report is diagnostic only. It cannot change targets, executions, risk
multipliers, no-trade bands, OptionsState, campaign status, or paper/live
eligibility. It must preserve the existing blocked verdict even if one
subgroup appears favourable.

## Track 2: regime-objective redesign preparation

After Track 1 completes, generate a small hypothesis record under
`docs/research/` only when the terminal recommendation is
`prepare_regime_objective_redesign`.

The record must name exactly one failure family supported by Track 1, define
one market-behavior hypothesis, list explicitly excluded old search families,
and specify an OOS-only falsification gate. It must not contain a parameter
grid, candidate configuration, training command, or production activation.

If Track 1 returns `stop_donor_training` without a regime-localized failure,
or `insufficient_evidence_no_strategy_change`, no redesign record is written.

## Error handling

- Missing raw artifact, invalid JSON, hash mismatch, or invalid frozen matrix:
  exit `2`, write no report.
- A valid artifact that lacks required cost, episode, or execution rows:
  write a report with `INSUFFICIENT_SAMPLE`, exit `1`.
- A completed diagnostic: write both outputs, exit `0`; its recommendation is
  informational and never indicates strategy acceptance.

## Tests and acceptance

Tests use small synthetic serialized artifacts and cover:

- input hash and matrix validation;
- deterministic ordering and JSON output;
- baseline-to-15bp PnL bridge arithmetic;
- closed/open episode handling;
- group attribution and four-of-five consistency;
- no-input mutation and no strategy/execution imports;
- valid incomplete output versus invalid-input exit behavior.

The implementation additionally passes `ruff`, relevant import-boundary tests,
the focused attribution tests, and full `pytest`. The diagnostic result does
not authorize another training run; a separate product-owner decision remains
required after reviewing the resulting report.
