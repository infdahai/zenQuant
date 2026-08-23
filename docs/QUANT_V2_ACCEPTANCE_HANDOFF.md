# Quant V2 Accounting Repair and Acceptance Handoff — 2026-08-23

This file is the authoritative compact handoff for the frozen August donor
after the legacy portfolio-accounting repair. Generated results remain ignored
under `results/`; their hashes below bind this document to the reviewed bytes.

## Decision

Identity-complete 30-run verdict:

```text
verdict                 = BLOCKED_PAUSE_PROMOTION_AND_TRAINING
parameter_search        = false
operator_evidence       = identity_complete_30_run_rerun_completed
```

The new immutable output passed the accounting-kernel, formula, legacy-envelope
and full replay-input identity contract before compute and publication. The
verdict is economically authoritative for this frozen donor and denies further
parameter training or promotion. The older repaired 30/30 remains an old
pre-contract diagnostic only and must not be edited or backfilled.

On 2026-08-23, the product owner explicitly accepted the repaired Gate C
golden as the new accounting regression baseline:

```text
final_equity = 97549.27227977359
net_pnl      = -2450.73
sharpe       = -9.7916
max_drawdown = 0.028809
profit_factor = 2.556359
```

This acceptance approves the signed-cash accounting semantics and their
regression fixture only. It does not validate August profitability, prove
legacy-to-repaired causal attribution, authorize promotion or parameter
search, or waive any product gate below.

The August huge-profit result is invalid. Legacy fee application floored
negative cash at zero and erased financing liability after leveraged fills.
The affected equity, return, net PnL, equity-multiple, Sharpe, Sortino,
drawdown and promotion-gate families cannot be used as performance evidence.

The identity-complete frozen matrix completed 30/30 runs with no search.
Late-dev A5 averages `+379.21` at observed baseline costs (4/5 positive),
`-353.44` with 5bp additional cost (1/5 positive), and `-1,818.72` with 15bp
additional cost (0/5 positive; range `-3,068.23` to `-624.15`). The implied
additional-cost breakeven is only about `2.59bp`. Robustness is `0/5`, below
the required `4/5`. Passing MDD, train-episode-PF, switch-cadence and
execution-purity checks do not override the blocked economic verdict.

## Frozen and repaired identity

- Candidate: `august_rank1_20260821`
- Manifest SHA-256: `c982d074b24ec21235dfefbd972d936dc4f5a4ab0360964013576c90729bfbe0`
- Candidate params SHA-256: `d86c92e8837c2febc04d0c2935299abc30b0636fb5d9a7c9e244d670d3499381`
- Profile SHA-256: `ce4fc640b3a0ae2dfb8b6d4bd96652f431a20b5581ce5d2fa67e0722f4841d05`
- Indicator formula hash: `sha256:82af23a08c68a94641e1d7769ecdf9619059dddbeb531dd3703cc4d2ff809f4a`
- Required accounting-kernel semantic SHA-256: `sha256:a2e7a4039556aa427ec258a2f38d302c6edf5e37e8e81b9cedd4a36897b66c1a`
- Frozen legacy envelope SHA-256: `sha256:0c66255a9020985ec5b3c2c10c2da4a38e96ed070c6c7c0801beced9b26f689c`
- Frozen replay input Parquet corpus (read-only snapshot at 2026-08-23 13:48 CST): `5,114` files / `21,375,864,438` bytes / `sha256:17fe13a2b55cfd8a34fb0e9b2c0c616e7af4c38a25e9eed705eb4b9cc0d6be04`
- Rerun contract schema: `legacy-accounting-rerun-contract.v3`
- Replay input corpus schema: `replay-input-parquet-corpus.v1`
- Required replay store availability: `macro=true`, `options=true`
- Rerun contract SHA-256: `c76f7c027698ac8146b549d551d38b0f6963e0d6932b1e77cabebf4fab397a19`
- Seeds: `42, 123, 456, 789, 1024`
- Windows: `train_full, 2020, 2021, 2022, 2023, late_dev_2025`
- Perturbation: price noise `0.001`, volume noise `0.05`, bar dropout `0.02`
- Cost stress: `0 / 5 / 15bp`, with 15bp added to observed baseline cost

Every one of the historical 30/30 repaired entries matched the ordered
manifest, candidate-parameter hash, profile hash and indicator formula hash.
Those checks do not establish the current accounting implementation or input
corpus. A replacement 30/30 artifact is identity-complete only when every run
also carries the current accounting-kernel hash and the wrapper verifies the
frozen legacy source/envelope and the complete `data/raw` Parquet corpus before
compute and again before publication. Before matrix entry 1, the wrapper also
constructs the first frozen donor context without calling `run()` and rejects
missing macro/options stores or any actual indicator-formula mismatch.

## Artifact identity

| Artifact | SHA-256 | Size |
| --- | --- | ---: |
| Legacy `results/quant_v2_acceptance/architecture_attribution.json` | `1f327c7c786f511fbba6ac1dd2ca4df395891f1aeb1879714c0ed2af1a87bea2` | `1,003,639,954` bytes |
| Repaired `full_system_attribution.json` | `3259f41a54a4ca688fd17cc59ad5c784564c49032f1c2a34721ea0ad237916d9` | `2,114,782,500` bytes |
| `fixed_event_attribution.json` | `17acf89e2c796a8bfa73093e9e20351d0bf2c5f7c3f24e22f6053c2f01c85c58` | `1,654` bytes |
| `stage_validity.json` | `bee2ff89d2257a8974ed544e24a574ab2dfef70fd6c445f8dbf40376cfe8beb3` | `104,422` bytes |
| `summary.json` | `fb455bcbeb7ccc584748db28f3e66996845b43b5f598d148e253fc1e99469c43` | `2,585` bytes |
| `acceptance_summary.json` | `27b344630b9737446897cc20a195c44b01584fadc25f9a739057208a0a97c723` | `6,039` bytes |

The legacy input hash remained unchanged across the historical rerun. Generated
artifacts remain ignored and are evidence, not commit payloads. The table above
binds this handoff to the current identity-complete output bytes.

## Attribution validity

### Fixed-event comparison

```text
status                         = INSUFFICIENT_EVIDENCE
legacy_state                   = UNVERIFIED_LEGACY
event_stream_identity          = null
first_affected_fee             = null
fills_marks_and_fees_identical = null
accounting_delta               = null
reason                         = legacy_artifact_has_no_replayable_fixed_event_ledger
funding_and_financing          = OUT_OF_SCOPE_NOT_EQUITY_INTEGRATED
```

The old artifact does not contain a replayable fill/mark/fee ledger. Therefore
the exact first clamped fee, fixed-event cash/equity delta and first downstream
risk, target, order or fill divergence are not known. Aggregate results must
not be used to invent that missing path.

### Stage comparison

All 210/210 comparisons—30 runs times A0, A1_SIGN, A1_SCORE, A2, A3, A4 and
A5_SHADOW—remain `UNVERIFIED_LEGACY`:

```json
{
  "CONFIRMED_AFFECTED": 0,
  "PROVEN_UNAFFECTED": 0,
  "SUPERSEDED_REPAIRED": 0,
  "UNVERIFIED_LEGACY": 210
}
```

Both sides lack proof required by the current auditor. The old artifact omits
current stage payload and donor identities; repaired A0/A1 schemas omit required
episode payloads, and repaired A2-A5 donor rows do not satisfy the current
canonical donor schema. Thus none of the 210 comparisons can prove
field-identical behavior. The first unverified key is
`train_full / seed 42 / A0`; there is no confirmed first divergence.

The retained generated `summary.json` says
`legacy_stage_provenance_incomplete`. That wording is historical output from
the pre-hardening rerun and is not the authoritative explanation. The detailed
stage records and Task 5 report prove that both sides are incomplete. Future
reruns use the corrected two-sided reason:
`legacy_and_repaired_stage_provenance_incomplete`.

## Identity-complete economic result

The values below are the authoritative frozen-donor acceptance result. They do
not authorize live use; they establish why this donor is blocked.

| Gate | Result | Repaired evidence |
| --- | --- | --- |
| A5 MDD below 20% on all seeds | PASS | maximum `5.0611%` |
| Train A5 episode PF >= 1.0 | PASS | minimum `1.2856` |
| Late-dev A5 extra 15bp positive | **FAIL** | all five negative; `-3,068.23` to `-624.15` |
| Robustness at least 4/5 | **FAIL** | `0/5` |
| Hostile risk controlled | **NOT EVALUABLE** | no frozen hostile window or threshold |
| Switch cadence | PASS | `0.1426–0.1920/day` |
| Execution purity | PASS | created exposure, reversal and overshoot all `0` |
| OptionsState execution influence | PASS | A4/A5 execution material remains equal |

The repaired A5 baseline late-dev range is `-596.20` to `1,013.77`; its mean
is `+379.21` with 4/5 positive seeds. At an additional 5bp, the mean is
`-353.44` with only 1/5 positive. The extra-15bp result is negative for every
seed and averages `-1,818.72`:

| Seed | A5 extra-15bp net PnL | Max A5 MDD | Train episode PF | Max switches/day |
| ---: | ---: | ---: | ---: | ---: |
| 42 | `-1,967.52` | `3.0357%` | `1.4472` | `0.1563` |
| 123 | `-2,180.62` | `5.0611%` | `1.4465` | `0.1673` |
| 456 | `-1,253.11` | `3.3602%` | `1.5810` | `0.1426` |
| 789 | `-3,068.23` | `4.0254%` | `1.3849` | `0.1920` |
| 1024 | `-624.15` | `2.6843%` | `1.2856` | `0.1536` |

Passing safety and purity diagnostics answer narrow questions. They do not
rescue negative cost-stressed economics, satisfy robustness or authorize a
promotion.

## Current repository gate blockers

The full data-present `zenquant-check` is **NOT green**. Its Gate E historical
checks preserve two product thresholds: DCA-off Sharpe sensitivity is
approximately `45%`, above the `40%` limit, and corrected max drawdown is
`26.8%`, above the `25%` limit. Gate F passes its code-level four-window check
with zero `OVERFIT` classifications, but the average OOS ratio is `-4.12` and
the runner emits a severe train-to-dev overfitting warning. These are preserved
product blockers, not implementation defects. Do not weaken these thresholds.
No promotion or parameter search is authorized while they remain unresolved.

The two Gate E failures do not invalidate the accounting implementation or the
pre-contract 30/30 diagnostics. They block product promotion on their own terms. The
product-owner acceptance recorded above applies only to the repaired Gate C
golden; it does not accept or waive these product failures.

## Accounting boundary and limitations

- Cash is signed; each finite non-negative fee is subtracted exactly once.
- Funding and financing are `OUT_OF_SCOPE_NOT_EQUITY_INTEGRATED`. Their
  diagnostic accumulators are not included in legacy equity and no total-net
  parity is claimed.
- Fixed-event attribution remains insufficient; the repaired full-system run
  cannot reconstruct the missing historical event ledger.
- Stage comparison remains unverified because both legacy and repaired stage
  schemas lack current proof fields.
- The repaired full-system report is 2.11 GB and the real run peaked near
  10.52 GiB RSS; streaming the legacy source does not remove the repaired
  report builder's in-memory aggregation cost.
- The frozen manifest has no explicit hostile window/threshold. That gate is
  not evaluable, not passed.
- Options shadow OOS overlap remains insufficient. There is no activation
  recommendation.

## Reproduction and verification

The reviewed real rerun used this exact command before accounting-kernel and
full replay-input identity hardening:

```bash
uv run python scripts/rerun_legacy_accounting_baseline.py \
  --root data/raw/binance \
  --legacy-input results/quant_v2_acceptance/architecture_attribution.json \
  --output-dir results/accounting_integrity_20260823
```

A later attempt mixed an absolute main-repository Binance `--root` with a
worktree cwd that had no matching `data/raw` auxiliary stores. It completed the
30 matrix entries but failed the final formula check, published no output
directory, and is not evidence. Do not edit old results or reuse that invocation.

Current code rejects every existing output path. A future rerun must use a new,
versioned output directory, must match
`config/research/legacy_accounting_rerun_v2.json`, and may not change seed,
window, parameter, cost or search-space inputs. Run from the isolated worktree
whose `data` link resolves to the frozen read-only snapshot, so relative
auxiliary stores and `--root` share one immutable `data/raw`:

```bash
cd /Users/disaster/workspace/code/zenQuant/.worktrees/late-dev-cost-attribution
uv run python scripts/rerun_legacy_accounting_baseline.py \
  --root data/raw/binance \
  --legacy-input results/quant_v2_acceptance/architecture_attribution.json \
  --output-dir results/accounting_integrity_20260823_v2

uv run python scripts/build_quant_v2_acceptance_summary.py \
  --input results/accounting_integrity_20260823_v2/full_system_attribution.json \
  --output results/accounting_integrity_20260823_v2/acceptance_summary.json

uv run pytest -q \
  tests/test_risk_guard.py \
  tests/test_execution_router.py \
  tests/test_parity.py \
  tests/test_legacy_accounting_validity.py \
  tests/test_legacy_accounting_provenance.py \
  tests/test_legacy_accounting_rerun.py \
  tests/test_architecture_experiment.py \
  tests/test_quant_v2_acceptance.py
uv run zenquant-check
```

The acceptance summary exits `0` only for acceptance, `1` for a valid blocked
verdict and `2` for invalid input. The reviewed result exits `1`; automation
must preserve rather than mask that blocked decision.

## Current boundary

- Do not promote, paper-live, train or run parameter search for this donor.
- Do not compare or rank candidates using invalidated legacy performance.
- Do not describe passing MDD, PF, cadence or purity checks as economic
  acceptance.
- Do not claim a fixed-event delta, first affected fee or first downstream
  divergence without a replayable historical event ledger.
- Do not classify any A0-A5 stage as proven unaffected while its provenance is
  incomplete.
- Do not integrate funding/financing into portfolio-equity claims without a
  separately approved accounting design.
- Any future hypothesis is a separately approved, pre-registered study. This
  repair authorizes no promotion or search.
