# Quant V2 Acceptance Handoff — 2026-08-22

## Decision

**Blocked: pause promotion and parameter training.**

The complete frozen matrix ran successfully with no parameter search: six
windows times five perturbation seeds, 30/30 runs. V2 passes baseline drawdown,
train episode PF, cadence and execution-purity gates. It fails the authoritative
late-dev extra-15bp gate on all five seeds, so robustness is 0/5 rather than the
required 4/5.

## Frozen identity

- Candidate: `august_rank1_20260821`
- Manifest SHA-256: `c982d074b24ec21235dfefbd972d936dc4f5a4ab0360964013576c90729bfbe0`
- Candidate params SHA-256: `d86c92e8837c2febc04d0c2935299abc30b0636fb5d9a7c9e244d670d3499381`
- Profile SHA-256: `ce4fc640b3a0ae2dfb8b6d4bd96652f431a20b5581ce5d2fa67e0722f4841d05`
- Seeds: `42, 123, 456, 789, 1024`
- Perturbation: price noise `0.001`, volume noise `0.05`, bar dropout `0.02`
- Cost stress: `0 / 5 / 15bp`, with 15bp added to observed baseline execution cost
- Formula identity sets across 30 runs: `1`; alpha-config identity sets: `1`

## Hard-gate result

| Gate | Result | Evidence |
| --- | --- | --- |
| A5 MDD below 20% on all seeds | PASS | worst `12.4531%` |
| Train A5 episode PF >= 1.0 | PASS | range `1.1016–1.3978` |
| Late-dev positive at extra 15bp | **FAIL** | net PnL range `-31,847.95` to `-28,943.07` |
| Hostile risk controlled | **NOT EVALUABLE** | manifest has no frozen hostile window/threshold |
| Switch cadence | PASS with soft flags | `1.1490–1.2258/day`; none above `2` or `5` |
| Execution purity | PASS | created exposure/reversal/overshoot all `0` |
| OptionsState order/position effect | PASS | byte contract and A4/A5 material equality |
| Robustness at least 4/5 | **FAIL** | `0/5`, driven by late-dev cost gate |

Per-seed final-stage evidence:

| Seed | Max baseline MDD | Train episode PF | Late-dev extra-15bp net | Max switches/day |
| ---: | ---: | ---: | ---: | ---: |
| 42 | 9.9100% | 1.1074 | -29,573.48 | 1.1764 |
| 123 | 11.3478% | 1.1351 | -30,285.56 | 1.1490 |
| 456 | 12.4531% | 1.3978 | -31,847.95 | 1.1737 |
| 789 | 11.5352% | 1.1383 | -31,796.15 | 1.2258 |
| 1024 | 9.1154% | 1.1016 | -28,943.07 | 1.2038 |

## Direction attribution

Late-dev direction hit rate is below 50% on every seed:

- A1 sign-only: `47.21–48.22%`; baseline net range `-61,034.58` to `11,717.10`;
  extra-15bp is negative for 5/5.
- A1 confidence-weighted: same hit rate; baseline net range `-22,544.75` to
  `3,132.11`; extra-15bp is negative for 5/5.
- A5: `46.82–48.11%`; baseline and extra-15bp are negative for 5/5.

Confidence reduces exposure and loss magnitude but does not add reliable OOS
directional edge. This answers the A1 decision gate: do not continue risk
optimization or training from this donor until the late-dev failure is explained.

## Artifacts

Generated files are intentionally under ignored `results/` and are not committed:

- `results/quant_v2_acceptance/architecture_attribution.json`
  - size: `1,056,561,733` bytes
  - SHA-256: `cae501b9ef86477552d78494e24036cb12b990db814ff5c32101719ffd2f370f`
  - contains all A0 episodes, A1-A5 attribution rows, A2-A5 execution rows,
    complete A2-A5 closed/open position-episode ledgers, Gate v3, cost shadows
    and Options OFF-vs-shadow rows. Each stage PF is mechanically recomputed
    from its serialized closed episodes before the report can be written.
- `results/quant_v2_acceptance/acceptance_summary.json`
  - size: `5,814` bytes
  - SHA-256: `1c545f99bf9a6d7aa49dfb0eb4ccc183fa7643f3b42e91505e1484f9cdfbd393`

Across 120 A2-A5 stage ledgers, the raw artifact contains 62,494 episodes:
62,445 closed and 49 right-censored open.

The compact summary is emitted by tracked repository code in one canonical
sorted-JSON representation. Snapshot, target and OptionsState identities have
deterministic unit and parity coverage.

## Rerun

Use the historical data read-only. Write output in the current worktree:

```bash
uv run python scripts/run_architecture_experiment.py \
  --root /Users/disaster/workspace/code/zenQuant/data/raw/binance \
  --output results/quant_v2_acceptance/architecture_attribution.json

uv run python scripts/build_quant_v2_acceptance_summary.py \
  --input results/quant_v2_acceptance/architecture_attribution.json \
  --output results/quant_v2_acceptance/acceptance_summary.json

shasum -a 256 results/quant_v2_acceptance/architecture_attribution.json
shasum -a 256 results/quant_v2_acceptance/acceptance_summary.json
uv run pytest tests/test_architecture_experiment.py tests/test_decision_snapshot.py \
  tests/test_episode_ledger.py tests/test_gate_v3.py tests/test_execution_router.py \
  tests/test_options_state.py tests/test_options_store.py tests/test_options_shadow.py \
  tests/test_quant_v2_acceptance.py tests/test_replay.py tests/test_parity.py -q --tb=short
uv run zenquant-check
```

The summary command exits `0` only for an acceptance verdict, `1` for a valid
blocked verdict, and `2` for an invalid/incomplete raw artifact. The current
frozen result is valid and intentionally exits `1` after writing the summary.
Invalid input writes no new summary, emits `INVALID:` on stderr, and cannot be
reported as a valid blocked verdict. Do not hide these exit codes in automation.

The runner rejects missing, extra, duplicate or empty matrix entries. Do not add
CLI tuning knobs or alter the manifest in response to a failed result.

## Options collection status

Read-only status at `2026-08-22T02:00:04.953009Z`:

- collector running, no last error, not stale;
- 24 snapshots in 24h and 146 in 7d;
- 24h/7d coverage ratios `1.0`, invalid `0`, duplicate `0`, max gap `4h`;
- `observation_ready=true`, `research_ready=true`.

Collection should continue. The frozen OOS window is 2025 while real OptionsState
history begins in 2026, so all five OOS artifacts correctly report
`INSUFFICIENT_SAMPLE`, `known_sample_count=0`, and no activation recommendation.

## Known limitations

- Changed-file Ruff and format checks passed for all 53 changed Python files.
- Focused V2 suites passed: `455 passed, 34 subtests passed`.
- Full pytest outside the restricted macOS sandbox passed:
  `1337 passed, 4 skipped, 34 subtests passed`.
- `zenquant-check` passed Gates 0/B/C/D/E/F. Gate A is blocked only by pre-existing
  format drift in `scripts/analyze_intent_churn_attribution.py`,
  `zenquant/data/event_calendar.py`, and `zenquant/data/liquidation_pipeline.py`.
  Gate E passed outside the sandbox. No unrelated files were reformatted.
- A hostile-window label and threshold must be added only through a newly frozen,
  separately approved acceptance contract.
- OptionsState remains shadow-only. Activating its multiplier requires separate approval.

## Late-development cost attribution — 2026-08-22 reproduction

The frozen runner was executed exactly once against
`/Users/disaster/workspace/code/zenQuant/data/raw/binance` with no campaign,
training, parameter search, or manifest change. It emitted the complete 30/30
matrix. The canonical acceptance summary again exited `1` with
`BLOCKED_PAUSE_PROMOTION_AND_TRAINING`; promotion and training remain paused.
The final attribution fix reused that exact 1 GB artifact and reran only
`scripts/analyze_quant_v2_late_dev_cost.py`; the frozen experiment was not
rerun.

Current artifact identity:

| Artifact | SHA-256 | Size |
| --- | --- | ---: |
| `results/quant_v2_acceptance/architecture_attribution.json` | `1f327c7c786f511fbba6ac1dd2ca4df395891f1aeb1879714c0ed2af1a87bea2` | `1,003,639,954` bytes |
| `results/quant_v2_acceptance/acceptance_summary.json` | `7d21e514e18f27611f793e2c7f06071ddb121de4654d0bf57a31d23f2f7da637` | `5,822` bytes |
| `results/quant_v2_late_dev_cost_attribution/late_dev_cost_attribution.json` | `b7cb1c84c70fab54b728f0cd6287fcde6fa4c107bf8f3de541a6cffd36de734e` | `92,571` bytes |
| `results/quant_v2_late_dev_cost_attribution/late_dev_cost_attribution.md` | `3f551c6f94ab9c40d8ee5fc4bbf134284c4d2b21981120c5ce562cef166f68a4` | `28,173` bytes |

The v2 attribution report validates all 30 runs and returns
`stop_donor_training`. It now emits 30 frozen-order A1-A5 bridges, 20 A2-A5
episode summaries, and 20 A2-A5 execution/cost/cadence summaries. Required
inputs are complete. Directional failure remains the decision-leading family;
execution shortfall independently corroborates the diagnosis at five-seed
support. Confidence weighting improved extra-15bp PnL in every seed but rescued
none above zero, so its truthful public family is `confidence_failed_to_rescue`
rather than a claim of no incremental edge.

| Evidence family | Status | Supporting seeds |
| --- | --- | ---: |
| `directional_edge` | `CONSISTENT` | `5/5` |
| `confidence_failed_to_rescue` | `CONSISTENT` | `5/5` |
| `cost_flip` | `UNATTRIBUTABLE` | `0/5` |
| `regime_localization` | `UNATTRIBUTABLE` | `0/5` |
| `episode_tail` | `UNATTRIBUTABLE` | `0/5` |
| `execution_shortfall` | `CONSISTENT` | `5/5` |

Confidence rescue diagnostics preserve the literal A1_SCORE minus A1_SIGN
extra-15bp PnL delta; hit-rate equality remains diagnostic only:

| Seed | A1_SIGN 15bp PnL | A1_SCORE 15bp PnL | Delta | Rescued above zero |
| ---: | ---: | ---: | ---: | --- |
| 42 | `-226,512.04` | `-72,673.40` | `+153,838.65` | no |
| 123 | `-184,678.85` | `-62,604.80` | `+122,074.05` | no |
| 456 | `-235,784.58` | `-77,817.96` | `+157,966.62` | no |
| 789 | `-202,088.14` | `-65,273.33` | `+136,814.81` | no |
| 1024 | `-156,132.90` | `-50,961.44` | `+105,171.46` | no |

The A5 execution surface is observed and complete for every seed. Fills/day
uses the exact positive finite run duration; action counts use only nested
filled-position transitions. Actual and estimated costs below are serialized
diagnostics, not recomputed strategy decisions:

| Seed | Execution obs. | Positive fills | Fills/day | Switches/day | Actual fee | Actual slippage | Actual shortfall | Estimated round trip | Breakeven mean |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 42 | `2,140` | `645` | `1.7671` | `0.4004` | `2,442.06` | `915.77` | `3,357.83` | `6,715.71` | `0.000365` |
| 123 | `2,150` | `665` | `1.8219` | `0.4497` | `2,475.87` | `928.45` | `3,404.32` | `6,885.39` | `0.000383` |
| 456 | `2,156` | `673` | `1.8438` | `0.4442` | `2,515.65` | `943.37` | `3,459.01` | `7,000.21` | `0.000382` |
| 789 | `2,150` | `763` | `2.0904` | `0.5210` | `2,970.11` | `1,113.79` | `4,083.91` | `8,167.85` | `0.000430` |
| 1024 | `2,150` | `725` | `1.9863` | `0.4497` | `2,755.49` | `1,033.31` | `3,788.80` | `7,577.69` | `0.000415` |

The per-seed A5 bridge reconciles baseline net PnL to the extra-15bp result:

| Seed | Baseline net PnL | Additional 15bp cost | Extra-15bp net PnL | Reconciliation error |
| ---: | ---: | ---: | ---: | ---: |
| 42 | `-1,579.17` | `9,157.73` | `-10,736.90` | `7.28e-12` |
| 123 | `-5,911.57` | `9,284.50` | `-15,196.07` | `7.28e-12` |
| 456 | `-4,714.50` | `9,433.67` | `-14,148.18` | `5.46e-12` |
| 789 | `-6,677.03` | `11,137.93` | `-17,814.95` | `-1.46e-11` |
| 1024 | `-2,170.89` | `10,333.10` | `-12,503.99` | `3.64e-12` |
| Range | `-6,677.03` to `-1,579.17` | `9,157.73` to `11,137.93` | `-17,814.95` to `-10,736.90` | `-1.46e-11` to `7.28e-12` |

No regime-objective redesign hypothesis was created. That artifact is allowed
only for the exact recommendation `prepare_regime_objective_redesign`, which the
real evidence does not support. All generated result files remain ignored and
non-promotable.

The first attribution attempt exposed a JSON boundary mismatch: the frozen
producer canonicalizes object keys, while the analyzer incorrectly treated a
per-run JSON object order as causal meaning. Commit `ac89021` preserves the
ordered top-level stage contract and validates each run by exact stage key set
and payload declaration. The 1GB frozen experiment was not rerun after this
read-only validator correction.
