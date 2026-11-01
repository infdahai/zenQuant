# Live validation handoff

This is the tester and operator contract for the retained Python baseline. The Go local options and manual paper application records declarations and unassessed intents; it does not place orders, produce fills, or establish paper-live eligibility.

## Admission

The frozen Quant V2 experiment is **blocked**: late-dev was negative under an additional 15bp cost on all five seeds, with strict robustness 0/5. Promotion, further parameter training and OptionsState multiplier activation remain paused. Options data beginning in 2026 does not validate the frozen 2025 out-of-sample period. A new, separately approved frozen experiment must satisfy its hard gates before promotion is reconsidered.

The retained baseline is BTCUSDT perpetual, with decisions on confirmed 4h closes and 1d context. Use train (2020–2023) for learning, dev (2024–2025) for selection and robustness, and test (2026 onward) only for final historical acceptance. Paper-live requires frozen test acceptance, robustness evidence and `results/framework_optimization/framework_optimization.json` with `final_verdict.test_evaluated=true`, `promote_to_paper_live_candidate=true`, and no blockers. A completed campaign alone is not acceptance. Real capital requires separate approval.

The runtime must preserve point-in-time inputs, intent before orders, risk authority over target exposure, actual filled positions, realistic execution, reproducible replay and auditable decisions. Missing or stale inputs must not be represented as valid evidence. Binance liquidation = Tier 1 primary truth; Hyperliquid public proxy = Tier 2 supplemental. Admission evidence is `results/data_source_review/hyperliquid_admission_review.json`; a proxy is not venue-native liquidation truth.

## Verification and operation

Before any approved paper-live run, execute the relevant strategy, risk, replay and parity tests and the repository harness. Confirm data readiness and record the source identities, configuration, commands, timestamps, results, skips and blockers. Inspect `results/data_readiness/data_readiness.json`, `results/framework_optimization/campaign_status.json` and the final verdict artifact; these are generated local artifacts and may be absent. Do not treat absent evidence as a pass.

After explicit admission, the Python operator workflow uses `zenquant-web --mode live`, `zenquant-live-validate`, `zenquant-live-scorecard` and `zenquant-live-export`; check their current CLI help and the configured database before use. Stop on stale or missing critical data, failed risk or replay checks, reconciliation or audit failures, or a portfolio kill condition. Paper-live operation does not itself constitute completed validation or approval for real capital.

The detailed prior operator procedures and historical evidence are available in [the pre-cleanup handoff](https://github.com/infdahai/zenQuant/blob/3b4a508/docs/LIVE_VALIDATION_HANDOFF_SPEC.md). They describe a historical revision and must be checked against current code and admission status before use.
