# Quant V2 Architecture Expert Review

**Review cutoff:** 2026-08-23

**Research branch:** `codex/late-dev-cost-attribution`

**Implementation baseline:** `7225fce`

## Decision requested from experts

The Quant V2 architecture is acceptable as a deterministic research framework,
but the current August rank1 alpha donor is not acceptable for more training or
promotion. The frozen experiment and the completed late-development attribution
both point to the same operational decision:

```text
promotion_state         = BLOCKED_PAUSE_PROMOTION_AND_TRAINING
terminal_recommendation = stop_donor_training
```

This separates two questions that must not be conflated:

- **Architecture:** keep. Its authority boundaries, replay semantics and audit
  surfaces are materially stronger than the legacy research path.
- **Current donor:** stop. It loses in late-dev before the extra 15bp stress on
  every seed, so another parameter search would optimize a failed donor family.

OptionsState should continue collecting and publishing shadow observations. It
must retain zero authority over target, risk, orders or positions. A proposed
time-space hypothesis belongs to a separately pre-registered shadow study; it
is not an approved trading rule and is not proven by the current result.

Measured results, hashes and rerun commands are maintained in
[QUANT_V2_ACCEPTANCE_HANDOFF.md](QUANT_V2_ACCEPTANCE_HANDOFF.md).

## What is implemented

```text
confirmed BTC 4h close
        |
        v
point-in-time alpha + MarketState + volatility/risk inputs
        |
        v
risk-final immutable DecisionSnapshot
        |
        +---------------------------> OptionsState sidecar
        |                              observation only
        |                              no trading authority
        v
target_tracking_v2 execution kernel
        |
        v
fills -> position episode ledger -> Gate v3 / attribution
```

The completed V2 research path includes:

- immutable 4h `DecisionSnapshot` identities and point-in-time cutoff rules;
- A0 plus fixed-notional A1 sign/confidence direction attribution;
- flat-to-flat / flip-split position episodes and episode-level PF;
- `target_tracking_v2` with no autonomous scoring, DCA or reversal;
- A2 volatility target, A3 regime multiplier, A4 single no-trade band and
  A5 observation-only cost shadow;
- OptionsState cutoff joins, stale fallback and OFF-versus-shadow isolation;
- golden, serialization, parity and execution-purity contract coverage.

The legacy path remains the default. V2 is enabled only by explicit architecture
and execution modes.

## Authority and safety boundary

| Layer | May do | Must not do |
| --- | --- | --- |
| 4h decision | Freeze alpha, state, risk inputs and approved signed target | Recompute after the confirmed cutoff |
| Risk | Cap, shrink, reduce-only or flatten | Add conviction or reverse direction |
| Execution V2 | Track approved target by slicing, delaying, cancelling and retrying | Re-score, DCA, overshoot or invent a flip |
| Hard safety | Cancel, reduce or flatten | Increase risk or open the opposite side |
| Episode ledger | Attribute fills, fees, funding and path metrics | Treat fill fragments as separate trades |
| A5 / Options shadow | Record counterfactual evidence | Block an order or change exposure |

Verified execution invariants are:

```text
executor_created_exposure   = 0
unexpected_reversal         = 0
target_overshoot            = 0
OptionsState trading effect = 0
```

## Latest canonical result

The frozen `6 windows x 5 seeds` matrix completed 30/30 runs with no parameter
search. The same formula and alpha-config identities were used throughout.

| Measure | Result | Interpretation |
| --- | ---: | --- |
| A5 late-dev baseline net PnL | `-6,677.03` to `-1,579.17` | negative on 5/5 seeds before extra stress |
| A5 late-dev extra-15bp net PnL | `-17,814.95` to `-10,736.90` | negative on 5/5 seeds |
| Maximum A5 MDD | `7.4169%` | passes the `<20%` safety gate |
| Minimum train episode PF | `1.1479` | passes the `>=1.0` train gate |
| Maximum switches/day | `0.4305–0.5210` | below the no-penalty `<=1/day` boundary |
| Robust seeds | `0/5` | fails the required `4/5` |

The result is not a turnover rejection. Position-switch cadence is comfortably
inside the approved policy. It is also not merely a 15bp cost flip: baseline A5
PnL is already negative on all five late-dev seeds.

## What the attribution establishes

The analyzer validates the exact frozen artifact rather than rerunning or
retuning the experiment.

| Evidence family | Status | Support | Meaning |
| --- | --- | ---: | --- |
| `directional_edge` | `CONSISTENT` | `5/5` | direction-failure attribution is consistent; this does **not** mean positive alpha |
| `confidence_failed_to_rescue` | `CONSISTENT` | `5/5` | confidence reduced losses but rescued no seed above zero |
| `execution_shortfall` | `CONSISTENT` | `5/5` | observed shortfall corroborates the failed economics |
| `cost_flip` | `UNATTRIBUTABLE` | `0/5` | evidence does not support “extra cost alone caused failure” |
| `regime_localization` | `UNATTRIBUTABLE` | `0/5` | no regime-specific redesign is justified by this result |
| `episode_tail` | `UNATTRIBUTABLE` | `0/5` | no stable episode-tail explanation was established |

Confidence weighting improved A1 extra-15bp PnL by approximately `105,171` to
`157,967` across the five seeds, but A1_SCORE remained negative in all five.
The correct conclusion is “useful loss compression, no OOS rescue,” not “no
incremental information” and not “promote confidence as a standalone edge.”

## Evidence boundary and unresolved questions

Established:

- the donor fails late-dev at baseline and under extra 15bp costs;
- confidence compresses loss but does not restore positive expectancy;
- execution does not create unauthorized exposure;
- OptionsState has no order or position effect;
- more training on this donor is not justified.

Not established:

- that time-insensitive decisions are the primary failure mechanism;
- that failure concentrates in the first days of a breakout or an incomplete
  weekly bar;
- that regime information is unimportant—the current result is
  `UNATTRIBUTABLE`, not a negative finding;
- that execution shortfall is the root cause rather than a consequence of poor
  trade selection;
- hostile-window control, because no hostile window and threshold were frozen;
- OptionsState OOS explanatory value, because real options history begins in
  2026 and does not overlap the frozen 2025 late-dev window.

## Proposed time-space hypothesis

The current donor does not explicitly model `trend_age`, `breakout_age`, weekly
bar progress or conditional weekly reversal hazard. The next candidate
hypothesis is:

> After a long decline or long basing period, a confirmed breakout may have a
> persistence horizon that is not captured by price-level signals alone. A
> large long-to-short reversal during the first one or two days, while the first
> weekly bar is incomplete and market structure remains intact, implicitly bets
> that the first weekly impulse will immediately fail or form a long upper wick.
> The system may currently assign too much probability to that event.

This is a falsifiable research hypothesis, not an explanation already proven by
the frozen result. It must not be implemented as a crude minimum holding period.
Such a rule would hide the causal question and could prevent legitimate exits.

A shadow-only study should pre-register, without tuning:

- `trend_age` since the confirmed structural trend transition;
- `breakout_age` in 4h, daily and weekly bars;
- current weekly-bar completion fraction;
- an explicit point-in-time structural-integrity definition;
- reversal hazard conditional on age and structural integrity;
- point-in-time definitions for long decline, long base and confirmed breakout,
  plus frozen age-bucket boundaries;
- early-reversal versus mature-trend net PnL, MFE, MAE, episode PF, trend-return
  retention, valid-stop losses and 15bp sensitivity;
- the cost of early flips versus a frozen no-flip counterfactual.

The 2025 late-dev window helped generate this hypothesis and therefore cannot
serve as an unbiased confirmation set. Acceptance requires a previously
untouched OOS window or forward data, stable direction across frozen seeds and
windows, and rejection when the effect appears only after choosing favorable
age buckets. Until then the fields are diagnostic only and cannot change target
or execution.

## Data and OptionsState status

The 2026-08-23 read-only readiness check shows:

- Deribit option-chain collection: observation-ready and research-ready;
- Deribit block flow: collector running and observation-ready; exact rolling
  counts remain time-sensitive and belong in the readiness artifact;
- Binance liquidation history: blocked by missing history;
- Hyperliquid public history: partial, approximately `65.09%` estimated coverage;
- merged liquidation history: minimum research-ready but not promotion-ready.

Therefore options collection should continue, while liquidation-derived claims
remain non-promotable. This data status does not change the donor verdict.

## Recommended next sequence

1. Keep promotion and parameter training blocked for August rank1.
2. Preserve Quant V2 as the research and audit framework.
3. Continue OptionsState collection and shadow reporting with zero trading authority.
4. Freeze a hostile-window definition and quantitative threshold before any new gate run.
5. Ask experts to approve or reject the time-space hypothesis and its pre-registered
   OOS falsification contract.
6. Only after approval, implement shadow attribution first; do not train or activate it.

## Questions for expert review

1. Do you agree that the current donor should be retired from further training?
2. Is the architecture/donor separation above correct and complete?
3. Is the time-space hypothesis economically coherent enough to pre-register?
4. How should weekly structural integrity and breakout age be defined without
   hindsight or parameter search?
5. What hostile-window definition and loss threshold should be frozen?
6. Should OptionsState continue as a zero-authority shadow sidecar?
7. What evidence would falsify the entire time-space hypothesis early?

## Evidence index

- Canonical experiment and attribution ledger:
  [QUANT_V2_ACCEPTANCE_HANDOFF.md](QUANT_V2_ACCEPTANCE_HANDOFF.md)
- Full architecture and authority map: [ARCHITECTURE.md](ARCHITECTURE.md)
- Operator boundary: [LIVE_VALIDATION_HANDOFF_SPEC.md](LIVE_VALIDATION_HANDOFF_SPEC.md)
- Generated canonical summary:
  `results/quant_v2_acceptance/acceptance_summary.json`
- Generated attribution report:
  `results/quant_v2_late_dev_cost_attribution/late_dev_cost_attribution.json`

Generated result files are intentionally ignored by Git. Their hashes are
recorded in the acceptance handoff. When prose and a generated current artifact
disagree, the valid artifact wins; when a proposal and code disagree, code and
contract tests win.
