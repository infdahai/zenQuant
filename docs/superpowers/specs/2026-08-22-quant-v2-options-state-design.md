# Quant V2 Architecture Experiment and OptionsState Shadow Design

## Goal

Establish a deterministic BTC 4h decision contract, measure the frozen August
alpha before adding risk machinery, replace fill-fragment profit factor with a
position-episode ledger, make execution a pure target tracker, and then add
A2-A5 architecture stages one at a time. In parallel, collect and compute an
observation-only BTC options state that cannot change orders or positions.

## Authority and sequencing

The implementation order is fixed:

1. frozen 4h `DecisionSnapshot`;
2. A0/A1 direction attribution;
3. episode ledger and Gate v3 profit factor;
4. execution purity;
5. A2-A5-shadow;
6. OptionsState counterfactual reporting.

Deribit chain collection starts immediately. OptionsState computation may be
associated with a 4h snapshot after the snapshot contract exists, but it is a
shadow sidecar and never enters the approved target formula in V1.

## Global invariants

- BTC only and confirmed 4h bar-close decisions only.
- August rank1 is frozen; no parameter search or retraining.
- A1 uses fixed starting capital, fixed notional, no compounding, and maximum
  1x exposure.
- A2 uses a 20% annualized volatility target and maximum 1.5x exposure.
- A3 multipliers are normal/tradable 1.00, transition/noisy 0.50,
  hostile/stress 0.10, and unsafe/unknown 0.00.
- A4 uses one 4% target-position no-trade band, floored by exchange minimum
  quantity. Safety reductions bypass it.
- A5 is cost instrumentation only and cannot block an order.
- OptionsState is observation-only and uses a neutral multiplier of 1.0 when
  missing or stale.
- Legacy behavior remains the default. V2 behavior requires an explicit
  architecture stage or `target_tracking_v2` execution mode.

## Acceptance

All five deterministic seeds must remain below 20% maximum drawdown. At least
four of five robustness seeds must pass. Train episode-level profit factor must
be at least 1.0, late-dev must remain positive under 15bp additional cost, and
hostile windows must remain controlled. Normal policy permits at most two
position switches per day and any accepted operating condition permits at most
five. Executor-created exposure, unexpected reversals, target overshoots, and
OptionsState-induced order differences must all equal zero.

