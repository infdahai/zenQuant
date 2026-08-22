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
Do not hide that exit code in automation.

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
- Focused V2 suites passed: `449 passed, 34 subtests passed`.
- Full pytest outside the restricted macOS sandbox passed:
  `1331 passed, 4 skipped, 34 subtests passed`.
- `zenquant-check` passed Gates 0/B/C/D/E/F. Gate A is blocked only by pre-existing
  format drift in `scripts/analyze_intent_churn_attribution.py`,
  `zenquant/data/event_calendar.py`, and `zenquant/data/liquidation_pipeline.py`.
  Gate E passed outside the sandbox with `1320 passed, 4 skipped, 34 subtests`.
  No unrelated files were reformatted.
- A hostile-window label and threshold must be added only through a newly frozen,
  separately approved acceptance contract.
- OptionsState remains shadow-only. Activating its multiplier requires separate approval.
