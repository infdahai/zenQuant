# Microstructure Sidecar Research

This repo now includes a research-only microstructure sidecar track.

The intent is narrow:

- keep the flagship BTC framework bar-close and audit-friendly,
- allow raw L2 / trade-tick preprocessing research to happen separately,
- only permit future feedback into `risk_overlay` or `execution_modifier`,
- keep `direction_vote` and `promoted truth` explicitly out of scope.

## Current scope

The sidecar pipeline is implemented by:

- [`scripts/research_microstructure.py`](/Users/disaster/workspace/code/zenQuant/scripts/research_microstructure.py)
- [`zenquant/research/microstructure.py`](/Users/disaster/workspace/code/zenQuant/zenquant/research/microstructure.py)

It expects a normalized research dataset root containing:

- `snapshots.parquet`
- `trades.parquet`

Current assumptions:

- one venue per run
- one symbol per run
- top-of-book and depth arrays are already normalized into parquet rows
- outputs are research-only and point-in-time aligned

## Output artifacts

The pipeline writes these review artifacts under `results/microstructure_research/`:

- `dataset_contract.json|md`
- `preprocessing_review.json|md`
- `feature_baseline_review.json|md`
- `admission_review.json|md`

It also writes bar-aligned sidecar features under:

- `data/raw/microstructure_sidecar/<venue>/<symbol>/<cadence>/features.parquet`
- `data/raw/microstructure_sidecar/<venue>/<symbol>/<cadence>/metadata.json`

## Allowed uses

Current policy is fixed:

- `research_only = true`
- `execution_modifier_only = true`
- `promote_truth = false`
- `pit_safe = true` is required before lookup is considered valid

Current allowed use classes:

- `execution_modifier`
- `risk_overlay_input`

Current forbidden use classes:

- `direction_vote`
- direct strategy entry replacement
- promoted truth / authoritative alpha

## Run command

```bash
uv run python scripts/research_microstructure.py \
  --data-root data/raw/microstructure/bybit_btc \
  --output-dir results/microstructure_research \
  --feature-output-root data/raw/microstructure_sidecar \
  --venue bybit \
  --symbol BTCUSDT \
  --depth 20 \
  --cadence-ms 1000 \
  --horizon-ms 1000
```

## Design boundary

This sidecar is intentionally not wired into:

- `IndicatorPack` public semantics
- `MarketState`
- authoritative replay truth
- the current flagship strategy decision path

If it proves useful later, the only acceptable integration path is:

1. bar-aligned aggregated sidecar features
2. research-only gating
3. `risk_overlay` / `execution_modifier` first
4. direction logic last, and only with separate evidence

## Approved rollout order

The current approved rollout order is fixed:

1. Bybit BTCUSDT normalized L2 sample and first artifact set
2. risk-only sidecar lookup injected into `IndicatorPack.values`
3. `risk_overlay` research-only guards
4. Hyperliquid cross-check as a second-source stability check
5. optional `execution_modifier` expansion
6. direction path last, and only with separate evidence

Current boundaries:

- Bybit is the primary first-sample venue
- Hyperliquid is a supplemental cross-check, not the first truth sample
- `framework_search.py` candidate ranking is out of scope
- `training_input_contract.state_feature_sources` is out of scope
- direction plugins are out of scope
