# Dataset Split Policy

ZenQuant now uses one authoritative BTC temporal split contract to reduce overfitting:

- `train`: `2020-01-01` to `2023-12-31`
- `dev`: `2024-01-01` to `2025-12-31`
- `test`: `2026-01-01` to latest available data

Rules:

- `train` is for parameter learning only.
- `dev` is for candidate ranking, weak-window checks, and robustness selection only.
- `test` is for final historical acceptance only.
- `paper-live` is the only authoritative live OOS layer after `test` passes.

Mechanical enforcement:

- `auto_research` requires an explicit split spec and sweeps only on `train`.
- `run_auto_research.py` is internal/dev-only and must not be used as the final promote path.
- `rl_tuner` trains only on `train` and validates only on `dev`.
- `compare_variants` defaults to `--split dev`; `--split test` must be explicit.
- `run_framework_campaign.py` is the authoritative clean tuning entrypoint.
- framework campaign status and reports record split metadata and `test_evaluated`.

Interpretation:

- `walk_forward_eval.py` is now a `train -> dev` selection-layer tool, not final proof.
- `2023-08` remains a `dev` weak-window check.
- historical adversarial replay should be read separately for `dev` and `test`.
- no candidate may be promoted to `paper-live` without a frozen `test` historical pass.
