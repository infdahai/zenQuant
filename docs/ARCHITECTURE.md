# ZenQuant System Architecture

**Status:** canonical architecture review entrypoint

**Snapshot date:** 2026-08-23

**Asset scope:** BTC first

**Promotion status:** research only; not eligible for paper-live

This document separates three things that must not be confused:

1. the production-default legacy architecture;
2. the implemented, explicit research-only Quant System v2 path;
3. the historical Gate v2 evidence and current frozen V2 acceptance result.

For product principles and non-negotiable invariants, read [`../AGENTS.md`](../AGENTS.md). For the current tester and operator contract, read [`LIVE_VALIDATION_HANDOFF_SPEC.md`](LIVE_VALIDATION_HANDOFF_SPEC.md). Neither document is replaced by this architecture review.

## 1. Executive Decision

ZenQuant remains a deterministic, regime-aware BTC trading system. The August
rank1 performance history was produced by defective legacy fee accounting and
is not valid evidence of return, equity, Sharpe, Sortino, drawdown or promotion
readiness. A frozen repaired matrix completed 30/30 runs with one verified
formula identity. Its A5 extra-15bp late-dev net PnL is negative on all five
seeds and robustness is `0/5`; the terminal recommendation remains
`stop_donor_training`. The architecture remains useful, but this donor must not
receive another optimizer or parameter-training round.

The implemented research-only V2 chain is:

```text
confirmed 4h data
      |
      v
directional alpha
      |
      v
state and regime risk multiplier
      |
      v
portfolio risk budget and exposure cap
      |
      v
auditable target position
      |
      v
no-trade band + cost shadow
      |
      v
5m execution scheduling
      |
      v
exchange adapter
```

The direction, risk, and execution authorities must remain separate:

- **Alpha** may propose direction and confidence.
- **State/regime** may reduce risk but may not reverse alpha direction.
- **Risk** may cap, shrink, flatten, or force reduce-only behavior.
- **Execution** may schedule an approved position change but may not invent a new directional view.

All alpha, context, regime, confidence, and volatility inputs used to approve a
target must be frozen at the confirmed 4h decision cutoff. Subsequent 1h or 5m
events may schedule that frozen target or invoke hard safety, but they may not
re-score or resize it using a new market view.

## 2. Non-Negotiable Invariants

The following invariants apply to both the current implementation and the V2 target:

- Directional decisions use confirmed bar closes only.
- Strategy produces target intent before exchange-specific orders.
- Risk can rewrite strategy intent using actual filled portfolio state.
- Historical, replay, and live paths must preserve point-in-time correctness.
- The same event stream and config must produce replayable decisions.
- Fees, slippage, fill state, and position transitions must remain realistic.
- Every material decision must be attributable after the fact.
- The production legacy path is not modified by research experiments without explicit promotion approval.

### 2.1 Legacy accounting integrity boundary

Legacy portfolio cash is a signed balance. A fill and fee must conserve the
following identity without flooring negative cash:

```text
cash_after = cash_before - signed_quantity * fill_price - fee
equity      = cash + sum(position_quantity * mark_price)
```

The removed `max(0, cash - fee)` floor erased financing liability after
leveraged purchases and created equity. The August huge-profit claim is invalid.
The checked-in validity manifest classifies its equity (including final equity
and multiples), return, net PnL, Sharpe, Sortino, drawdown and promotion-gate
families as `CONFIRMED_AFFECTED`. Historical artifacts stay available for
audit, but their performance metrics cannot be used for comparison or
promotion.

The repaired formula identity is:

```text
sha256:82af23a08c68a94641e1d7769ecdf9619059dddbeb531dd3703cc4d2ff809f4a
```

The frozen rerun verified this exact identity, manifest hash, candidate hash
and profile hash for all 30/30 entries. That establishes the repaired run's
identity, not legacy comparability. The old artifact has no replayable
fixed-event ledger, so the exact first affected fee and downstream divergence
remain `INSUFFICIENT_EVIDENCE`. All 210/210 legacy-to-repaired stage records are
`UNVERIFIED_LEGACY`: the historical schema and repaired stage schemas lack the
current payload and donor proof needed for a field-identical comparison.

Funding and financing remain `OUT_OF_SCOPE_NOT_EQUITY_INTEGRATED`; diagnostic
accumulators are not portfolio-equity parity. No promotion or parameter search is authorized by the repair or rerun.

## 3. Current Implemented Architecture

### 3.1 Runtime topology

```text
Market data
   |
   v
IndicatorEngine
   |  IndicatorPack
   v
StateEngine
   |  MarketState
   v
PlaybookEngine
   |
   +----------------------+----------------------+
   |                                             |
   v                                             v
Legacy strategy path                        Plugin research path
hard-coded direction                       signal weighted sum
SizingEngine                               filter AND chain
ExitEngine                                 sizer multiplication
                                            exit priority
   |                                             |
   +----------------------+----------------------+
                          |
                          v
                   TargetPosition intent
                          |
                          v
                     RiskOverlay
              actual fills + risk throttles
                          |
                          v
                revised TargetPosition
                          |
                          v
                   ExecutionRouter
                          |
                          v
                  ExecutionGateway
```

Important implementation files:

| Responsibility | Authoritative code |
|---|---|
| Domain contracts | [`zenquant/domain/models.py`](../zenquant/domain/models.py) |
| Indicators | [`zenquant/indicator/engine.py`](../zenquant/indicator/engine.py) |
| Market state | [`zenquant/state/engine.py`](../zenquant/state/engine.py) |
| Strategy orchestration | [`zenquant/strategy/engine.py`](../zenquant/strategy/engine.py) |
| Legacy sizing | [`zenquant/strategy/sizing_engine.py`](../zenquant/strategy/sizing_engine.py) |
| Plugin composition | [`zenquant/plugins/orchestrator.py`](../zenquant/plugins/orchestrator.py) |
| Risk authority | [`zenquant/risk/guard.py`](../zenquant/risk/guard.py) |
| Execution routing | [`zenquant/execution/router.py`](../zenquant/execution/router.py) |
| Backtest assembly | [`zenquant/app/backtest.py`](../zenquant/app/backtest.py) |

### 3.2 Legacy and plugin paths

The repository contains two strategy paths with different authority:

| Path | Role | Current authority |
|---|---|---|
| Legacy (`plugin_registry=None`) | Production default and baseline | May be used by current replay/live contracts |
| Plugin (`plugin_registry` provided) | Research and candidate generation | Research-only until explicitly promoted |

Plugin composition rules are fixed:

- signals: weighted sum;
- filters: AND chain;
- sizers: multiplicative;
- exits: priority ordered.

Plugin functions receive plain dictionaries and must remain deterministic and side-effect free. A research result must not silently modify the legacy path.

### 3.3 Current timeframe truth

There are three distinct contracts in the repository:

1. **Current production/handoff baseline**
   - BTCUSDT perpetual;
   - 4h bar-close decisions;
   - 1d context;
   - PaulWei-style execution and risk baseline.

2. **Historical Gate v2 research replay contract**
   - 4h remains the directional decision horizon;
   - 1h provides context;
   - 5m is the execution replay timeframe;
   - the frozen test split was not opened.

3. **Quant V2 research contract**
   - immutable 4h risk-final decisions;
   - A0-A5 attribution and episode accounting;
   - target-only 5m execution scheduling;
   - default-off and not promoted.

The second and third contracts are research evidence, not a claim that
production has migrated to V2.

### 3.4 Sizing and risk behavior

Sizing and risk capabilities already exist:

- volatility-targeted base sizing;
- confidence and state discounts;
- maximum leverage caps;
- drawdown throttles;
- volatility and crowding throttles;
- daily loss protection;
- reduce-only and kill behavior;
- risk decisions based on actual execution reports rather than theoretical targets.

The current defaults are aggressive enough to invalidate raw return scale as alpha evidence:

- legacy `target_vol_4h` is `0.60`;
- plugin `vol_target_sizer` also defaults to `0.60` for 4h;
- `RiskOverlayConfig.max_leverage` is `6.0`.

These values are implementation facts, not endorsements for the V2 research experiments.

### 3.5 Existing churn controls

The repository already contains two families of deadband behavior:

- strategy-side target-size hysteresis in `StrategyEngine`;
- execution-side target-delta and reduction deadbands in `ExecutionRouter`.

They are not yet one authoritative, cost-aware no-trade contract. Running similar controls in multiple layers makes attribution harder and can create replay/live policy drift.

## 4. Current Architecture Problems

### 4.1 Execution contains strategy-like decisions

`ExecutionRouter` currently contains behavior based on regime, confidence, squeeze state, panic reversal, bearish grind, range state, DCA quality, and profit protection. Some of these checks are valid safety controls, but some influence whether a direction is opened, reversed, or expanded.

This means legacy 5m execution has not fully lost directional authority. The
implemented V2 research path distinguishes:

- exchange mechanics and scheduling;
- hard execution safety checks;
- portfolio risk decisions;
- directional strategy decisions.

Only the first two belong in the executor.

The current lower-timeframe path is more than a passive scheduler. It computes
1h and 5m entry scores from momentum, structure, CVD, volatility, VPIN, and
large-trade context; it also applies range/chop confirmation and a
lower-timeframe transition cap before publishing the target. Those behaviors
must not be described as pure execution.

### 4.2 Sizing responsibility is duplicated

Exposure can be changed by:

- playbook leverage limits;
- legacy or plugin sizing;
- strategy lifecycle caps and hysteresis;
- risk overlay multipliers and caps;
- execution pyramiding, DCA, deadbands, and reversal handling.

All of these mechanisms are individually auditable, but their combined legacy
output does not provide clean causal attribution. The V2 research path
establishes a single signed target-exposure calculation before execution
scheduling begins; production migration remains deferred.

### 4.3 Raw compounded return is not capacity evidence

Pre-repair results included equity multiples that varied sharply across
deterministic perturbation seeds. Those performance families are now invalid,
not merely weak capacity estimates. Repaired fixed-notional results remain
useful for rejecting fragile economics, but they do not estimate deployable
profit.

The V2 experiment reports separately:

- fixed-notional alpha;
- volatility-targeted return;
- compounded strategy return;
- turnover and cost drag;
- capacity assumptions.

### 4.4 Research state is fragmented

The repository still contains a machine-read campaign hypothesis contract for a research line that has already been rejected. It remains in place because `scripts/run_framework_campaign.py` and its tests load it mechanically.

This is known architecture debt:

- file: `docs/NEXT_RESEARCH_HYPOTHESIS_EXECUTION_INVENTORY_RETHINK.json`;
- status: rejected historical line, not the current research recommendation;
- constraint: do not delete until the campaign contract is migrated into `config/research/` and its behavior is redesigned and tested.

## 5. Historical Gate v2 Evidence

The bounded Gate v2 rescore is preserved as a historical selection record. It is defined
by [`superpowers/specs/2026-08-21-gate-v2-rescore.md`](superpowers/specs/2026-08-21-gate-v2-rescore.md).
Its summary result is
[`../results/remediation_20260821/gate_v2_rescore/gate_v2_rescore.md`](../results/remediation_20260821/gate_v2_rescore/gate_v2_rescore.md),
with full seed-level evidence in the adjacent JSON file. The newer Quant V2
acceptance and attribution result supersedes Gate v2 as the current decision
authority; see
[`QUANT_V2_ACCEPTANCE_HANDOFF.md`](QUANT_V2_ACCEPTANCE_HANDOFF.md).

Experiment constraints:

- seven frozen candidates;
- five deterministic perturbation seeds;
- train-full, 2020, 2021, 2022, 2023, and late-dev windows;
- price noise, volume noise, and bar dropout;
- additional 0/5/10/15bp cost stress;
- no Optuna, RL, full training, or frozen-test access.

The historical report recorded no candidate passing any of the five seeds
under the complete Gate v2 policy. The legacy performance families in that
result are not current economic evidence; only identities and non-performance
historical context survive the accounting invalidation.

### 5.1 August rank1

August rank1 is a retired historical donor, not a deployable strategy. The old
claims of positive late-dev PnL, Sharpe around `3.56–4.55` and train PnL up to
tens of millions were produced on the affected ledger. They are explicitly
invalid, not conservative estimates of alpha or capacity. Parameter and market
data identities remain auditable, but the old A0 event path is not canonical
repaired performance evidence.

### 5.2 v164 rank1

v164 remains a historical low-frequency comparison sleeve, not a proven
bear-market expert. Its low switch and fill frequency describes the old event
path, but its legacy PnL, Sharpe and drawdown claims are not current performance
evidence. Any future v164 study requires repaired fixed-notional return,
correlation and marginal-portfolio-contribution tests under a separately
approved contract.

### 5.3 Frequency conclusion

The user-approved diagnostic policy is:

- at most one position switch per day: no penalty;
- one to two switches per day: soft penalty, may enter dev;
- more than two switches per day: hard block;
- five switches per day is the absolute operational ceiling;
- monthly direction changes remain diagnostic only.

Gate v2 did not identify position-switch frequency as the main failure. August performs roughly one switch per day while producing more fill actions through staged adds and reductions. Cost and action quality matter more than a crude fill-count cap.

### 5.4 Historical recommendation and current decision

The historical Gate v2 terminal recommendation was:

```text
design_regime_router_before_more_training
```

That recommendation led to the V2 architecture experiment. The experiment is
now complete. Its current terminal recommendation is:

```text
stop_donor_training
```

Corrected evidence shows late-dev A5 baseline net PnL spanning a small loss to
a small gain, while extra-15bp PnL is negative on all five seeds at
`-3,068.23` to `-624.15`. Robustness is `0/5`. Passing MDD, episode-PF, switch
cadence and execution-purity diagnostics do not override that blocked economic
verdict. Consequently, the following architecture principles remain valid,
but they no longer authorize training this donor:

- freeze the directional alpha candidate;
- make risk scaling explicit;
- normalize position and capacity assumptions;
- remove low-value target changes;
- prevent execution from becoming a second alpha model;
- only resume parameter search for a separately approved donor after a
  pre-registered hypothesis produces interpretable OOS evidence.

## 6. Quant System v2 Target Architecture

### 6.1 Target data flow

```text
4h confirmed market event
          |
          v
  Alpha signal layer
  direction + confidence
          |
          v
 State / regime layer
 risk multiplier only
          |
          v
 Portfolio risk budgeter
 vol target + exposure cap
          |
          v
 Signed TargetPosition
          |
          v
 No-trade band + cost shadow
          |
          v
 5m execution scheduler
 slicing + slippage limits
          |
          v
 Risk-checked order command
          |
          v
 Exchange and reconciliation
```

### 6.2 Authority matrix

| Layer | May do | Must not do |
|---|---|---|
| Alpha | Produce direction and confidence at confirmed 4h close | Set exchange orders or bypass risk |
| State/regime | Produce a point-in-time risk multiplier | Reverse long to short or short to long |
| Portfolio risk | Cap exposure, shrink size, flatten, force reduce-only | Add directional conviction |
| No-trade gate | Hold economically insignificant target changes | Change the approved target direction |
| 5m execution | Slice, delay, cancel, and retry within approved limits | Re-score alpha, resize from a new market view, DCA beyond target, or invent reverse exposure |
| Gateway | Submit and reconcile venue-specific orders | Assume fills or rewrite portfolio intent |

### 6.3 Implemented frozen decision snapshot and hard safety

At every confirmed 4h decision cutoff, V2 creates one immutable decision
snapshot:

```text
DecisionSnapshot
  alpha direction and raw score
  alpha confidence
  state and regime
  volatility estimate and budget
  risk multiplier and caps
  approved signed target
  source cutoff timestamp
  formula/config identity
```

The approved target is frozen until the next confirmed 4h decision. The 1h and
5m clocks may determine scheduling and order mechanics, but may not recompute
direction, confidence, regime, or target size.

A separate hard-safety channel may react between 4h decisions to:

- stale or invalid data;
- account or margin risk;
- an exposure-cap violation;
- a kill switch;
- exceptional volatility;
- exchange or instrument trading-state changes.

Hard safety may cancel orders, reduce exposure, or flatten. It may never add
risk. For a non-flip target, every safety transition must satisfy:

```text
abs(position_after_safety) <= abs(position_before_safety)
abs(position_after_safety) <= abs(approved_target) + epsilon
```

Any later reversal requires a new confirmed 4h snapshot. A safety response is
not allowed to open the opposite side.

### 6.4 Implemented fixed research regime behavior

The research path is deterministic and point-in-time. It does not use a new probabilistic model.

Frozen fixed multipliers for the experiment:

| Risk state | Multiplier |
|---|---:|
| normal / tradable | `1.00` |
| transition | `0.50` |
| hostile / stress | `0.10` |
| unknown / unsafe | `0.00` |

These are fixed architecture-test values, not optimized production parameters. A later soft probability model is only justified if the deterministic version exposes a concrete weakness and preserves replay parity.

### 6.5 Implemented research position contract

The target exposure has one auditable formula:

```text
signed_target_exposure
  = direction
  * confidence
  * regime_risk_multiplier
  * volatility_budget
```

The result is then clipped by explicit portfolio and instrument caps. Execution receives the approved signed target and current filled position; it does not recompute alpha or leverage.

### 6.6 No-trade and cost behavior

A target change may be held when it is below one fixed, authoritative no-trade
band. V1 must not estimate expected return or fit a cost threshold before the
economic relationship has been measured. Strategy and execution must not carry
separate competing deadbands.

Emergency reductions are exempt from the no-trade hold when:

- data quality becomes unsafe;
- volatility becomes distorted;
- a kill or drawdown condition activates;
- reduce-only is required;
- the current position violates the approved cap.

The first cost-aware stage is instrumentation-only. It records estimated fees,
estimated slippage, round-trip cost, breakeven move, target delta, and subsequent
realized return, but it does not block an order. A cost gate may be proposed only
after this shadow evidence exists and its expected-edge definition is explicit.

## 7. A0–A5 Architecture Experiment

The completed experiment froze candidate parameters and changed one architecture property at a time.

| Stage | Single change | Question answered |
|---|---|---|
| A0 | Historical August rank1 replay, accounting-affected | What did the old candidate path report? |
| A1-sign | `sign(signal)` with fixed capital, fixed notional, no compounding, maximum 1x | Does direction alone contain alpha? |
| A1-score | Direction multiplied by frozen confidence, otherwise identical to A1-sign | Does confidence add information or only sizing noise? |
| A2 | Frozen direction and confidence, fixed 20% annualized volatility target, maximum 1.5x | Does normalized risk improve robustness? |
| A3 | Deterministic regime risk multiplier frozen in the 4h snapshot | Can hostile loss be reduced without direction prediction? |
| A4 | One authoritative target-position no-trade band | Which resize actions are unnecessary? |
| A5-shadow | Record rebalance economics without blocking trades | What could justify a later fixed cost gate? |

Every stage uses fixed capital, the same five seeds, and the same windows. A1-sign
and A1-score both use maximum 1x exposure and no compounding. No stage may
introduce parameter search.

These stages are causal attribution experiments, not a leaderboard. A later
stage does not need to produce more raw profit than every prior stage. It must
answer its assigned question without degrading the relevant safety contract.

Required reporting:

- fixed-notional net PnL and Sharpe;
- sign-only versus confidence-weighted contribution;
- volatility-targeted return;
- maximum drawdown;
- expected shortfall / CVaR;
- episode-level net profit factor after fees;
- add/reduce action profit factors as diagnostics only;
- turnover and fills per day;
- position switches per day;
- 15bp breakeven behavior;
- seed and regime robustness;
- capacity and exposure assumptions;
- target tracking error and time-to-target;
- implementation shortfall and realized slippage;
- target overshoot and unexpected reversal counts;
- executor-created exposure count.

Declared hard acceptance conditions:

- no seed reaches `20%` maximum drawdown;
- episode-level full-train net profit factor after fees is at least `1.0`;
- late-dev remains positive under 15bp additional cost;
- hostile windows remain controlled; this condition was not frozen in the
  manifest and is `NOT_EVALUABLE` in the current run;
- no more than two switches per day under the normal Gate policy;
- no more than five switches per day under any accepted operating condition;
- no unexplained multi-order-of-magnitude seed dispersion;
- executor-created exposure is exactly zero;
- unexpected executor reversal count is exactly zero;
- executed exposure never exceeds the approved target plus quantity tolerance.

The current fill-level `profit_factor_after_fee` and action profit factors are
not Gate v3 hard metrics. The former is currently a proxy derived by multiplying
gross profit factor by `1 - fee_drag_pct`; the latter is computed from realized
PnL fragments attached to individual actions. Both can produce pathological zero
or extreme values. Gate v3 therefore uses the implemented episode ledger to
measure one complete flat-to-flat or flat-to-flip position episode after fees.

For action-quality research, add and reduce PF remain diagnostics. A later
counterfactual metric may compare actual post-action PnL against a frozen
no-action hold path, net of cost, but that is deferred until its point-in-time
and replay semantics are specified.

### 7.1 Execution purity invariants

For every executor action outside hard safety:

```text
sign(order_delta) == sign(approved_target - current_filled_position)
abs(position_after_fill) <= abs(approved_target) + quantity_epsilon
```

For hard safety:

```text
abs(position_after_fill) <= abs(position_before_fill)
```

The executor may approach the target in slices. It may not cross the target,
increase exposure after a safety override, or open the opposite side. Any
violation is an immediate architecture Gate failure, independent of return.

## 8. Deferred Work

The following items remain outside the completed V2 experiment:

- new Optuna or RL campaigns;
- end-to-end ML position or order control;
- probabilistic regime ML;
- v164 ensemble promotion;
- multi-asset expansion;
- portfolio allocator optimization;
- migration to NautilusTrader or Qlib;
- production or paper-live promotion.

v164 remains deferred. Any future v164 work must treat it as an independent
donor under a separately approved, pre-registered experiment; it cannot inherit
authority from the retired August donor. Multi-asset breadth only begins after
the single-asset contracts are stable.

## 9. Validation and Promotion Boundary

Historical backtests are rejection tools, not proof of live profitability.

The validation stack remains:

1. historical train/dev validation;
2. deterministic domain randomization and cost stress;
3. frozen historical test acceptance;
4. paper-live incremental validation;
5. separately approved capital deployment.

Neither Gate v2 nor the completed Quant V2 acceptance authorizes paper-live. The
current operator contract remains
[`LIVE_VALIDATION_HANDOFF_SPEC.md`](LIVE_VALIDATION_HANDOFF_SPEC.md).

## 10. Questions for Expert Review

Experts reviewing this architecture should focus on unresolved decisions:

1. Is the current evidence sufficient to retire August rank1 from further training?
2. How should the hostile window and quantitative loss threshold be frozen?
3. Is the time-space hypothesis in the expert review coherent enough for a
   pre-registered, shadow-only OOS test?
4. How should trend age, breakout age, weekly structural integrity and reversal
   hazard be defined without hindsight?
5. Which capacity and market-impact model is minimally credible for BTC perpetual execution?
6. What evidence would justify or falsify an independent v164 donor study?
7. What forward sample is sufficient to evaluate OptionsState without granting it trading authority?

## 11. Repository Sources of Truth

| Topic | Source |
|---|---|
| Product invariants | [`../AGENTS.md`](../AGENTS.md) |
| Agent current-state hints | [`../CLAUDE.md`](../CLAUDE.md) |
| Operator and paper-live contract | [`LIVE_VALIDATION_HANDOFF_SPEC.md`](LIVE_VALIDATION_HANDOFF_SPEC.md) |
| Temporal split policy | [`DATASET_SPLIT_POLICY.md`](DATASET_SPLIT_POLICY.md) |
| Current Quant V2 verdict | [`QUANT_V2_ACCEPTANCE_HANDOFF.md`](QUANT_V2_ACCEPTANCE_HANDOFF.md) |
| Expert review and next hypothesis boundary | [`QUANT_V2_ARCHITECTURE_EXPERT_REVIEW.md`](QUANT_V2_ARCHITECTURE_EXPERT_REVIEW.md) |
| Historical Gate v2 policy | [`superpowers/specs/2026-08-21-gate-v2-rescore.md`](superpowers/specs/2026-08-21-gate-v2-rescore.md) |
| Gate v2 summary | [`../results/remediation_20260821/gate_v2_rescore/gate_v2_rescore.md`](../results/remediation_20260821/gate_v2_rescore/gate_v2_rescore.md) |
| Gate v2 full evidence | [`../results/remediation_20260821/gate_v2_rescore/gate_v2_rescore.json`](../results/remediation_20260821/gate_v2_rescore/gate_v2_rescore.json) |
| Candidate manifest | [`../config/research/gate_v2_candidates_20260821.json`](../config/research/gate_v2_candidates_20260821.json) |
| Legacy accounting validity | [`../config/research/legacy_accounting_validity_v1.json`](../config/research/legacy_accounting_validity_v1.json) |
| Repaired accounting evidence | [`QUANT_V2_ACCEPTANCE_HANDOFF.md`](QUANT_V2_ACCEPTANCE_HANDOFF.md) |
| Operations | [`OPERATIONS.md`](OPERATIONS.md) |
| Data freshness | [`../results/data_readiness/data_readiness.md`](../results/data_readiness/data_readiness.md) |

When a document and a generated result disagree about a time-sensitive research outcome, the newest valid result artifact wins. When a proposal and code disagree about implemented behavior, code and contract tests win.
