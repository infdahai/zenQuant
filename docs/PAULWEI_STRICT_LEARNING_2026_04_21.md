# PaulWei Strict Learning Audit (2026-04-21)

**Document role**: Research spec

This document is a research audit appendix. It is not a replacement for the tester / operator handoff entrypoint in [LIVE_VALIDATION_HANDOFF_SPEC.md](/Users/disaster/workspace/code/zenQuant/docs/LIVE_VALIDATION_HANDOFF_SPEC.md).

Primary fact sources:

- [`results/paulwei_research/strict_execution_learning_review.json`](/Users/disaster/workspace/code/zenQuant/results/paulwei_research/strict_execution_learning_review.json)
- [`results/paulwei_research/promotion_eligibility_review.json`](/Users/disaster/workspace/code/zenQuant/results/paulwei_research/promotion_eligibility_review.json)
- [`results/paulwei_research/ahr999_execution_guidance_review.json`](/Users/disaster/workspace/code/zenQuant/results/paulwei_research/ahr999_execution_guidance_review.json)

## Summary

- `paulwei_public_archive_baseline` is stronger than the legacy `paulwei_baseline` on research metrics.
- It is still `research-only`.
- The primary blocker is `drawdown_outside_current_band`, not hostile-window regression.
- The value of this line is execution / risk philosophy transfer, not hidden signal-engine reconstruction.

## What Is Worth Learning

The current strict-learning conclusion is that the following dimensions are worth absorbing into the next baseline PK round:

- long-side multi-tranche inventory building with capped short-side expansion
- gradual protective exits instead of all-at-once de-risking
- slower drawdown recovery ramp after deep equity damage
- moderate flip confirmation floor instead of noisy reversals
- stronger volatility-shock compression during unstable periods

These correspond to the current research modules:

- `inventory_build_profile`
- `protective_exit_profile`
- `recovery_profile`
- `flip_profile`
- `trend_hold_profile`

## What Is Not Learnable From The Public Ledger

The following dimensions remain explicitly non-transferable:

- discretionary macro inputs
- news / narrative interpretation
- cross-exchange context
- hidden conviction scaling beyond observable executed inventory

This means the public archive should not be used to invent a PaulWei signal engine. It is only valid as an auditable behavior-to-config mapping source.

## Why The Public Baseline Is Still Research-Only

Current research conclusion:

- the public archive baseline improves research metrics relative to the legacy PaulWei baseline
- it also improves over `current` on selected Sharpe / hostile-window metrics
- but it still expands drawdown beyond the current acceptable band

Therefore:

- it is valid as a `stretch` research baseline
- it is not yet valid as an authoritative default baseline
- promotion remains blocked until drawdown is brought back inside the `current` band

## AHR999 Role Clarification

`AHR999` currently has a strict role boundary:

Allowed:

- `regime_prior`
- `risk_budget_modifier`
- `dca_guidance_signal`
- `explainability_context`

Prohibited:

- `intraday_trigger`
- `execution_signal`
- `direction_vote`

Current overlap conclusion:

- `AHR999` is highly related to `MVRV / cycle_position`
- it is not treated as a trivial alias
- it is only eligible for slow-risk / DCA guidance
- it must not be described as already entering intraday execution

## Audit Conclusion

For audit purposes, the PaulWei line should currently be described as:

- auditable
- reusable as a strict learning track
- useful for baseline PK
- still research-only

The next legitimate use of this line is to absorb the learnable execution / risk deltas into a new comparison baseline and re-run PK under the existing gate regime.
