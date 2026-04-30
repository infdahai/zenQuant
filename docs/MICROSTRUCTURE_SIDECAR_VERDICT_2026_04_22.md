# Microstructure Sidecar Verdict (2026-04-22)

## Summary

Current verdict: **conditionally accepted as a research-only risk/execution sidecar**.

This is not a direction-alpha approval and not a promote-truth approval.

The implementation is acceptable because it preserves the right hierarchy:

- artifact-first admission
- point-in-time sidecar lookup
- bar-aligned enrichment
- `RiskOverlay` authority
- fail-open behavior on stale or missing sidecar data

The implementation should **not** be described as proof of higher expected return. The correct claim is narrower:

- it provides a plausible microstructure stress sensor
- it is wired into the correct layer first
- it remains below direction vote and below promoted truth

## What Is Accepted

- `Bybit BTCUSDT L2` as the first normalized sidecar sample
- dataset / preprocessing / feature baseline / admission artifacts as the required entry discipline
- replay and runtime lookup through `IndicatorPack.values` using `ms_*` risk-only fields
- `RiskOverlay` consumption for:
  - toxicity stress
  - liquidity vacuum stress
  - spread stress
- stale or missing sidecar data failing open instead of breaking core replay

Supporting evidence:

- [`results/microstructure_research/dataset_contract.json`](/Users/disaster/workspace/code/zenQuant/results/microstructure_research/dataset_contract.json)
- [`results/microstructure_research/preprocessing_review.json`](/Users/disaster/workspace/code/zenQuant/results/microstructure_research/preprocessing_review.json)
- [`results/microstructure_research/feature_baseline_review.json`](/Users/disaster/workspace/code/zenQuant/results/microstructure_research/feature_baseline_review.json)
- [`results/microstructure_research/admission_review.json`](/Users/disaster/workspace/code/zenQuant/results/microstructure_research/admission_review.json)
- [`tests/test_microstructure_integration.py`](/Users/disaster/workspace/code/zenQuant/tests/test_microstructure_integration.py)
- [`tests/test_replay.py`](/Users/disaster/workspace/code/zenQuant/tests/test_replay.py)
- [`tests/test_risk_guard.py`](/Users/disaster/workspace/code/zenQuant/tests/test_risk_guard.py)

## What Is Not Accepted

- direction vote
- strategy entry replacement
- `framework_search.py` candidate ranking truth
- `training_input_contract.state_feature_sources`
- promoted truth / authoritative alpha

These remain out of scope until there is separate evidence that the sidecar improves more than local prediction quality and does so without venue-specific leakage or replay/live boundary damage.

## Current Evidence Strength

### Strong evidence

- The artifact-first research flow is professional and correctly constrained.
- The implemented boundary is coherent with repo doctrine: risk first, direction later.
- The `RiskOverlay` integration is semantically correct for this class of signal.
- Tests cover:
  - risk-only field injection
  - replay path enrichment
  - stale sidecar fail-open behavior
  - leverage reduction / reduce-only triggers under severe microstructure stress

### Weak or incomplete evidence

- Current artifacts do not yet establish a robust return or hostile-window improvement claim.
- `feature_baseline_review.json` does not yet provide a strong research verdict that would justify moving beyond risk/execution research usage.
- Venue-specific bias is not yet resolved; Hyperliquid has not yet been used as a formal cross-check.
- There is not yet a repository-level conclusion proving that sidecar-enabled guards materially reduce tail risk on `dev` / hostile windows.

## Correct Product Interpretation

The correct product statement is:

- **accepted as a microstructure stress sensor in the risk path**

The incorrect statements would be:

- "microstructure is now part of the flagship direction logic"
- "the sidecar already proved alpha"
- "the current implementation already improved profitability"
- "the sidecar is part of promoted truth"

## Next Required Evidence

Before any deeper integration, the next research checkpoint should explicitly answer:

1. Does the sidecar improve risk/execution quality only, or does it also improve hostile-window outcomes in a stable way?
2. Does sidecar-enabled risk gating reduce tail damage on `dev` / hostile windows without introducing obvious opportunity loss?
3. Are the sidecar features materially venue-specific when compared against a Hyperliquid cross-check?
4. Does stale / missing / degraded sidecar data continue to preserve fail-safe behavior under replay and runtime stress?

Until those are answered, the current best label is:

- `research-only`
- `risk-overlay-integrated`
- `not direction-approved`
- `not promote-truth-approved`
