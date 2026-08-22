# Quant V2 Accounting Repair and Acceptance Handoff — 2026-08-23

This file is the authoritative compact handoff for the frozen August donor
after the legacy portfolio-accounting repair. Generated results remain ignored
under `results/`; their hashes below bind this document to the reviewed bytes.

## Decision

```text
verdict                 = BLOCKED_PAUSE_PROMOTION_AND_TRAINING
terminal_recommendation = stop_donor_training
parameter_search        = false
```

The August huge-profit result is invalid. Legacy fee application floored
negative cash at zero and erased financing liability after leveraged fills.
The affected equity, return, net PnL, equity-multiple, Sharpe, Sortino,
drawdown and promotion-gate families cannot be used as performance evidence.

The corrected frozen matrix completed 30/30 runs with no search. Late-dev A5
extra-15bp net PnL is negative on all five seeds, from `-3,068.23` to
`-624.15`. Robustness is `0/5`, below the required `4/5`. Passing MDD,
train-episode-PF, switch-cadence and execution-purity checks do not override
the blocked economic verdict.

## Frozen and repaired identity

- Candidate: `august_rank1_20260821`
- Manifest SHA-256: `c982d074b24ec21235dfefbd972d936dc4f5a4ab0360964013576c90729bfbe0`
- Candidate params SHA-256: `d86c92e8837c2febc04d0c2935299abc30b0636fb5d9a7c9e244d670d3499381`
- Profile SHA-256: `ce4fc640b3a0ae2dfb8b6d4bd96652f431a20b5581ce5d2fa67e0722f4841d05`
- Repaired formula hash: `sha256:82af23a08c68a94641e1d7769ecdf9619059dddbeb531dd3703cc4d2ff809f4a`
- Seeds: `42, 123, 456, 789, 1024`
- Windows: `train_full, 2020, 2021, 2022, 2023, late_dev_2025`
- Perturbation: price noise `0.001`, volume noise `0.05`, bar dropout `0.02`
- Cost stress: `0 / 5 / 15bp`, with 15bp added to observed baseline cost

Every one of the 30/30 repaired entries matched the exact ordered manifest,
candidate-parameter hash, profile hash and repaired formula hash. The report
contains one formula identity set and one alpha-config identity set. These
checks establish repaired-run identity and determinism; they do not prove that
the old and repaired stage paths are comparable.

## Artifact identity

| Artifact | SHA-256 | Size |
| --- | --- | ---: |
| Legacy `results/quant_v2_acceptance/architecture_attribution.json` | `1f327c7c786f511fbba6ac1dd2ca4df395891f1aeb1879714c0ed2af1a87bea2` | `1,003,639,954` bytes |
| Repaired `full_system_attribution.json` | `101fdaaafa8b46bd3266838485022e5d42e85ea026e85bd42d077b5c3c7b08f3` | `2,114,612,999` bytes |
| `fixed_event_attribution.json` | `30395969363d2ae57b1414c7f93e7914088f23d4e6b20c161d8e5768cafda6d9` | `1,103` bytes |
| `stage_validity.json` | `3207f2c3d04308e8feab1f92dd63d2c3607896da32f4a3782cab22b5a3cf8e12` | `103,871` bytes |
| `summary.json` | `68a50ad3407938556fa7a8eac257e842cfc50887e70f45acdaba2e356d629495` | `1,984` bytes |
| `acceptance_summary.json` | `eb91d38c86b650e75dacb9a954e71ef34fd61de6dcc7bdade266b4e7ce6a8589` | `5,830` bytes |

The legacy input hash remained unchanged across the rerun. Generated artifacts
remain ignored and are evidence, not commit payloads.

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

## Corrected economic result

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

The repaired A5 baseline late-dev range is `-596.20` to `1,013.77`; it spans
loss and profit and is not a five-seed positive result. The extra-15bp result
is negative for every seed:

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
30/30 identity checks. They block product promotion on their own terms. No
product-owner acceptance is claimed by this handoff.

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

The reviewed real rerun used this exact command before immutable-output
hardening:

```bash
uv run python scripts/rerun_legacy_accounting_baseline.py \
  --root data/raw/binance \
  --legacy-input results/quant_v2_acceptance/architecture_attribution.json \
  --output-dir results/accounting_integrity_20260823
```

Current code rejects every existing output path. A future rerun must use a new,
versioned output directory and may not change seed, window, parameter, cost or
search-space inputs:

```bash
uv run python scripts/rerun_legacy_accounting_baseline.py \
  --root data/raw/binance \
  --legacy-input results/quant_v2_acceptance/architecture_attribution.json \
  --output-dir results/accounting_integrity_<new-version>

uv run python scripts/build_quant_v2_acceptance_summary.py \
  --input results/accounting_integrity_<new-version>/full_system_attribution.json \
  --output results/accounting_integrity_<new-version>/acceptance_summary.json

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
