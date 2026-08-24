# Trend Participation Two-Layer Design

## Decision

Build two isolated BTC research layers:

1. `TimeSpace Shadow` measures whether early flips abandon structurally intact trends.
2. `Trend Participation Donor` tests whether a slow, hysteretic directional state captures persistent trends after realistic costs.

Neither layer has production, paper-live, target-position, risk or execution authority in V1. The August rank1 donor is retired as an Alpha candidate and retained only as frozen historical context. Legacy behavior, A1-A5, `DecisionSnapshot`, risk and execution remain unchanged. Activation is a later, separately approved task after genuine forward evidence.

The objective is not a higher daily hit rate. It is a distribution in which a small number of persistent BTC trends contribute meaningful net profit without hidden leverage, excessive switching or weaker safety controls.

## Why this design

The identity-complete August rerun found thin edge rather than excessive switching. Late-dev A5 retained only about `2.59bp` of additional cost capacity after observed costs and funding. A minimum holding lock would hide signal instability without creating edge.

Research supports slow trend measurement and hysteresis, but not a universal BTC breakout expiry or unconditional minimum holding period. Fast signals may detect turns earlier but also create false reversals. V1 therefore separates slow directional authority from fast explanatory state.

## Non-goals

- no minimum holding days or `breakout_age > N => exit` rule;
- no parameter search over lookbacks, thresholds, features or age buckets;
- no change to August raw score or confidence;
- no OptionsState multiplier, ML, change-point model or learned hazard model;
- no live-path import or event publication;
- no claim that reused 2025 or 2026 history is clean OOS evidence.

## Architecture and isolation

```text
frozen PIT source rows
        |
        +-----------------------------+
        |                             |
        v                             v
TimeSpace Shadow              Trend Participation Donor
flip-regret attribution       z84 latent state + hysteresis
        |                             |
        v                             v
independent report            research-only fixed-1x intent
        |                             |
        +-------------+---------------+
                      v
          episode/cost/robustness reports
                      |
       no production target, order or authority
```

The layers may read frozen point-in-time exports and reuse the audited historical accounting/execution kernel from an isolated research runner. They do not share mutable state. Strategy, risk, execution and live adapters must not import either research module.

Do not add fields to `ArchitectureAlphaSnapshot`, `ArchitectureObservation`, `DecisionSnapshot`, `TargetPosition`, A1-A5 rows, runtime episode state, risk or execution. Shadow OFF/ON must leave existing snapshots, targets, orders, fills, equity and episodes byte-identical.

## Phase 0: data readiness before research

The current normalized Binance spot daily corpus is not ready: it contains only 87 rows from 2026-01-02 through 2026-03-31 and misses at least 2026-02-01 and 2026-03-01; the available unnormalized CSV also stops before the requested August cutoff. The present status is `INSUFFICIENT_DATA_SPOT_DAILY_GAPS`.

Before any donor result is computed:

1. backfill, normalize and deduplicate `BINANCE_SPOT_BTCUSDT_1D`;
2. validate a unique monotonic UTC daily grid, finite OHLC, complete registered windows and an exact last complete cutoff;
3. freeze the immutable source corpus and its row/content digests;
4. record all inputs in a source matrix with `REQUIRED | DIAGNOSTIC | DISABLED`, producer/version, venue, instrument, frequency, expected grid, gap/duplicate/staleness policy and fallback ID;
5. freeze spot fallback as `NONE`.

Source gaps and experiment-injected dropout are different. Source integrity is checked before perturbation. An injected dropout row is marked and cannot conceal or repair a real source gap.
Every REQUIRED spot, perpetual 5m and funding source fails closed with
`INSUFFICIENT_DATA` on missing, duplicate or stale rows. Perpetual candles may
not proxy spot, funding may not default to zero or carry forward, and
fee/slippage models are frozen models rather than data fallbacks. The source
envelope binds raw-file digests, normalization-code identity and normalized
output digests.

## Layer 1: TimeSpace Shadow

The existing specification remains authoritative for Layer 1 semantics:
`docs/superpowers/specs/2026-08-23-time-space-shadow-design.md`. The stricter
two-record registration and evidence-governance rules in this umbrella spec
apply to both layers.

Its primary population is an `alpha_flip` whose breakout is in the same UTC week, whose old-side structure is `INTACT`, and whose source has no gap. Its primary label is fixed-1x `hold_minus_flip_6` over six retained 4h bars. It answers one question: did a flip abandon an intact young trend? It does not decide what a replacement donor should trade.

Layer 1 keeps its own registration receipt and sample floor: at least 180 elapsed forward days, 30 unique post-registration matured primary flips and one confirmed market-state transition. Age shuffling may test an age-conditional relationship only; it is not a placebo for the unconditional primary effect. Any future primary placebo must randomize pre-registered event time or side and requires a separate spec revision.

## Layer 2: Trend Participation Donor

### Exact point-in-time signal

The only active signal source is `BINANCE_SPOT_BTCUSDT_1D`. A UTC daily bar covering `[D, D+1 day)` has `available_at_ns = D+1 day`. It may first affect the first confirmed 4h decision whose `decision_cutoff_ns > available_at_ns`; a bar available at 00:00 UTC therefore first affects the 04:00 UTC decision, never the 00:00 decision.

For 85 consecutive confirmed closes `P_(t-84), ..., P_t`, form exactly 84 log returns `r_i = log(P_i / P_(i-1))`. Define:

```text
sigma84 = sample_standard_deviation(r_(t-83), ..., r_t, ddof=1)
z84 = log(P_t / P_(t-84)) / (sigma84 * sqrt(84))
```

Any missing/duplicate/non-finite row, non-positive close, fewer than 85 consecutive closes, or `sigma84 <= 1e-12` produces `UNKNOWN`. A real source gap resets warmup; the signal becomes valid only after 85 new consecutive closes. No perpetual candle or other proxy may substitute for spot.

### Mutually exclusive latent state machine

Thresholds are frozen and symmetric. Each confirmed daily observation creates at most one transition:

```text
FLAT:
  if z84 >= +0.50: LONG
  elif z84 <= -0.50: SHORT
  else: FLAT

LONG:
  if z84 <= -0.50: SHORT
  elif z84 <= 0.00: FLAT
  else: LONG

SHORT:
  if z84 >= +0.50: LONG
  elif z84 >= 0.00: FLAT
  else: SHORT
```

If no applicable transition condition is met, state persists. This is signal hysteresis, not a time lock. An invalid observation sets the latent signal to `UNKNOWN` and approved research exposure to flat. Recovery starts from `FLAT` after the complete 85-close warmup; it never silently restores the old side.

`TrendParticipationSignalState` is distinct from safety-approved research exposure. A kill/reduce-only condition may flatten exposure but may not mutate a valid latent direction. After safety clears, exposure may follow that direction only at the next confirmed 4h decision. Hard safety can never add risk.

Both paired paths use the same latest-as-of safety snapshot satisfying
`available_at_ns <= decision_cutoff_ns`. Projection is exact:

```text
latent_target in {-1, 0, +1}

if KILL or UNSAFE:
  approved_target = 0
elif REDUCE_ONLY:
  if current_filled * latent_target <= 0:
    approved_target = 0
  else:
    approved_target = sign(current_filled) *
                      min(abs(current_filled), abs(latent_target))
elif CAP_VIOLATION:
  approved_target = sign(current_filled) *
                    min(abs(current_filled), cap_value)
else:
  approved_target = latent_target
```

`cap_value` is frozen in `[0, 1]`. A safety-clear transition may follow latent
direction only at the next confirmed 4h decision. These functions never cross
zero or add risk while a hard-safety state is active.

### Research execution and accounting

The primary attribution uses fixed initial capital, fixed 1x notional and no
compounding. `TrendParticipationResearchIntent` is its only authority record.
A private `TrendParticipationReplayAdapter` stores
`signal_decision_cutoff_ns` separately from `execution_event_ns` and creates an
execution event at the first retained perpetual 5m bar open whose
`bar_start_ns > decision_cutoff_ns`. The strict inequality forbids the
same-timestamp opening print. It cannot fill at the daily close or 4h decision
price.

The current shared router backdates gateway time to target cutoff. If it cannot
carry the independent execution timestamp, V1 reuses only its audited
fee/slippage and accounting primitives, not that router entry point. An adapter
may construct unchanged transport types only inside a private
`InMemoryJetStream`, with an isolated research portfolio/identity namespace;
they are not production `DecisionSnapshot` records, cannot enter August
artifacts and cannot publish a production/live subject. The manifest binds the
perpetual instrument, 5m price field, accounting-kernel version, quantity
rounding, fill model and funding settlement rule.

A fully completed direct `LONG <-> SHORT` target change has expected target
turnover of 2x, but gates use realized turnover only:

```text
realized_turnover_notional = sum(abs(filled_qty) * actual_fill_price)
```

Each fill is split at zero: the close portion belongs to the old episode and
only the remainder after actual position crosses zero opens the new episode.
Fees and implementation-shortfall diagnostics are allocated by fill-notional
proportion. Unfilled residual persists to the next eligible 5m event until
filled or superseded by a newer confirmed 4h authority; supersession cancels
the residual before calculating the new target delta.

```text
net_pnl_0bp  = gross_pnl_from_actual_fills + funding - actual_fees
net_pnl_5bp  = net_pnl_0bp - realized_turnover_notional * 0.0005
net_pnl_15bp = net_pnl_0bp - realized_turnover_notional * 0.0015
```

`gross_pnl_from_actual_fills` uses actual fill prices, so their embedded
slippage is not deducted again. Implementation shortfall versus the frozen
reference price is reported separately.

The forward ledger and latent FSM both begin `FLAT`. Pre-registration rows may
initialize rolling z-score buffers only. The first eligible daily observation
at or after `forward_start_ns` may trigger a transition; no pre-start side,
exposure, PnL or episode is carried forward.

### Explanatory state without authority

The following point-in-time fields are diagnostics only:

- `trend_age_days` and `log1p(trend_age_days)`;
- `breakout_age_4h_bars` from a boundary visible at `t-1`;
- fast/slow disagreement from the median sign of independently standardized `z7`, `z14` and `z28` versus the slow side;
- signed 7-day curvature, `z7(t) - z7(t-7d)`;
- 28-day path efficiency, absolute net close change divided by the sum of absolute daily close changes;
- episode drawdown from the most favorable confirmed close divided by current `sigma84`;
- UTC week, completed fraction of week, old-side structural integrity and quality flags.

Each short-horizon z-score uses the same consecutive-close, log-return, `ddof=1` and zero-variance rules as `z84`. Breakout age is `UNKNOWN`, not zero, when no prior boundary exists. A current-bar pivot cannot define a same-bar breakout. No diagnostic may be selected, weighted or thresholded after viewing results.

## Evidence registration and identities

Each layer uses two immutable records. A hypothesis manifest is committed before
data repair and freezes formulas, thresholds, gates, source contracts, seeds
`42, 123, 456, 789, 1024`, costs, `training=false` and
`parameter_search=false`. It also freezes the reused diagnostic window as
`[2026-01-01T00:00:00Z, 2026-08-24T00:00:00Z)`. Missing data returns
`INSUFFICIENT_DATA`; the execution manifest may not shorten, shift or replace
this window. The hypothesis manifest deliberately cannot bind a corpus that
does not yet exist. Phase 0 is blind data engineering: it may validate and freeze the
raw-to-normalized envelope but may not compute a donor indicator or result.
After Phase 0, an execution manifest binds the hypothesis hash,
formula/config/source-set hashes, the exact reused diagnostic window and end
cutoff, immutable corpus digests, accounting-kernel version, forward checkpoint
rules, and a list plus digest of dirty/untracked paths. A manifest never stores
the SHA of the commit containing itself; the receipt envelope binds those two
identities. Clean-tree text alone is insufficient.

Both records receive independent append-only remote-provider or CI receipts.
The first binds the hypothesis-manifest SHA; the second binds the
execution-manifest SHA, hypothesis-manifest SHA, commit SHA and
repository/remote identity. Local Git timestamps are not trusted. Define
`registration_received_at_ns` as the later of the two server-side receipt
times.

```text
registration_cutoff_ns = first confirmed 4h cutoff
                         >= registration_received_at_ns + 24 hours
forward_start_ns = registration_cutoff_ns
```

A missing or unverifiable receipt yields `REGISTRATION_UNVERIFIED`; the run may remain diagnostic but cannot accrue confirmation evidence. The receipt is stored in a separate registration envelope so the manifest does not hash or timestamp itself.
Each later forward checkpoint has an append-only report envelope binding its
`forward_end_ns`, incremental source digests and prior receipt chain; extending
observation never rewrites the registered execution manifest.

Artifacts are independent:

- `time_space_shadow_hypothesis_manifest.json`;
- `time_space_shadow_execution_manifest.json`;
- `time_space_shadow_registration_envelope.json`;
- `time_space_shadow_attribution.json`;
- `trend_participation_hypothesis_manifest.json`;
- `trend_participation_execution_manifest.json` and registration envelope;
- `trend_participation_direction_attribution.json`;
- `trend_participation_episode_ledger.json`;
- `trend_participation_acceptance.json`.

## Evidence roles and execution order

The mandatory order is:

1. commit and remotely register each frozen hypothesis manifest before looking
   at repaired results;
2. complete Phase 0 data repair, validation and corpus freeze without changing
   the hypotheses;
3. commit and remotely register each execution manifest, binding its hypothesis
   and final corpus;
4. run current-history diagnostics;
5. begin independent forward observation from each registered cutoff.

Evidence roles never aggregate into one gate:

- 2025 late-dev is `DISCOVERY_ONLY`;
- 2026 through the exact frozen cutoff is `TEST_REUSED_DIAGNOSTIC` because it already informed prior testing and baseline decisions;
- post-receipt rows are `FORWARD_CONFIRMATION` only after the registered start.

The roles are mechanically disjoint:

```text
reused_diagnostic_end_ns <= forward_start_ns
reused row:  market_event_cutoff_ns < forward_start_ns
forward row: market_event_cutoff_ns >= forward_start_ns
```

Role is determined by market-event cutoff, never ingest or backfill time. A
registration-era repair of an older market event remains reused diagnostic.

Reused data may reject the hypothesis, expose leakage or show weak economics. It may never confirm or promote it. Frozen August A5 may appear in a context appendix only; it is not rerun on 2026/forward windows and does not participate in any donor gate.

## Causal comparison and trend-capture measurement

The only causal comparison uses the same market rows, starting capital,
execution/cost model and seeds. Each path creates and reconciles its own orders,
fills, turnover and costs because hysteresis is expected to change them:

1. `z84_sign_fixed_1x`: `sign(z84)`, flat only at exactly zero/UNKNOWN;
2. `z84_hysteresis_fixed_1x`: the frozen state machine above.

```text
paired_net_delta_15bp = hysteresis_net_pnl_15bp - sign_net_pnl_15bp
net_edge_bps_15 = 10_000 * net_pnl_15bp / realized_turnover_notional
```

Zero turnover yields `INSUFFICIENT_SAMPLE`. Net edge per turnover is secondary and cannot override negative total PnL or a failed paired comparison.

Trend capture uses an outcome-independent grid rather than selecting donor
winners. The grid is consecutive non-overlapping UTC calendar months. A month
is eligible only when `sigma84` is valid at its start. Its donor PnL is the
change in fixed-capital marked-to-market perpetual research equity over the
half-open month, including all fills, costs and funding assigned within that
month. A `persistent-trend month` is an evaluation label when absolute monthly
spot log return is at least start-of-month
`sigma84 * sqrt(daily_intervals)` and full-month path efficiency is at least
`0.50`. Path efficiency is the absolute open-to-close log move divided by the
sum of absolute daily log moves. This ex-post label is independent of donor
outcomes and is never executable. Its oracle side is the sign of that monthly
spot return. Oracle gross PnL uses the
same fixed 1x capital and perpetual prices at the first retained 5m opens at or
after the month boundaries; it does not use spot PnL.

Aggregate capture is a ratio of sums, never a mean of per-month ratios:

```text
gross_capture = sum(donor_gross_pnl_m) / sum(abs(oracle_gross_pnl_m))
net_capture_15 = sum(donor_net_pnl_15bp_m) / sum(abs(oracle_gross_pnl_m))
```

The denominator includes every eligible persistent-trend month and the numerator
uses those exact same months. Report all months and separately report:

- gross and 15bp-net trend capture versus absolute 1x oracle PnL;
- missed opportunity while flat;
- contribution concentration across all months and all donor episodes;
- early-exit regret at 1, 3 and 7 days.

No metric conditions on whether the donor won. Zero denominators yield `INSUFFICIENT_SAMPLE`.

Market-period stability uses the frozen point-in-time classifier already present
in the research export, collapsed before registration into three buckets:
`NORMAL_TRADABLE`, `TRANSITION_NOISY`, and `HOSTILE_STRESS`. A bucket is
sufficiently sampled only with at least 60 elapsed calendar days and 10 closed
donor episodes; all other buckets remain `INSUFFICIENT_SAMPLE` and cannot pass
or fail a bucket-level gate.

## Perturbations and placebo

Source integrity runs first. The five deterministic paths then apply:

- zero-mean Gaussian price noise with `0.10%` standard deviation, seeded per path, to copied daily closes and copied perpetual execution prices;
- independent `2%` dropout only to copied 4h/5m execution events, never to the required daily source grid;
- all injected rows retain provenance flags and never alter the frozen corpus.

The donor placebo uses six frozen non-wrapping signal shifts:
`-84, -56, -28, +28, +56, +84` daily rows. Canonical and all placebo paths are
compared only on their common intersection after trimming 84 daily rows from
both ends. Every path starts flat with the same capital at that intersection
and uses its own reconciled fill ledger. The canonical hysteresis path must
exceed the maximum placebo `net_pnl_15bp`; no shift or interval may be selected
after results are known.

## Gates

Historical/reused data can only return `REJECT`, `CONTINUE_FORWARD` or `INSUFFICIENT_DATA`. Forward evidence may return `ELIGIBLE_FOR_SEPARATE_ACTIVATION_REVIEW`; it never activates trading.

Layer 2 sample sufficiency requires at least 348 elapsed forward days. After
the placebo trim, the common intersection itself must retain at least 180
elapsed days, 30 closed canonical episodes, and at least 10 closed long and 10
closed short episodes. The full window must also contain all three sufficiently
sampled regime buckets and one confirmed market-state transition. If any item
is unmet, observation continues without changing the rule.

After sample sufficiency, hysteresis must:

- have positive canonical unperturbed `net_pnl_15bp`;
- have canonical paired `net_pnl_15bp` delta of at least `0.50%` of fixed
  initial capital over the confirmation window, with a one-sided 95% paired
  block-bootstrap lower bound above zero using UTC calendar-month blocks;
- have positive `net_pnl_15bp` in at least 4/5 perturbation paths;
- have positive paired `net_pnl_15bp` delta versus sign in at least 4/5 paths;
- retain persistent-trend 15bp net capture within 10 percentage points of sign in every sufficiently sampled regime bucket;
- reduce switches at least 20% versus sign;
- have maximum drawdown below 20% in every path;
- have closed-episode PF at least 1.0 in every sufficiently sampled path;
- beat every frozen circular-shift placebo on `net_pnl_15bp`;
- have non-negative paired `net_pnl_15bp` delta in every sufficiently sampled regime bucket.

Switch cadence is inherited safety, not Alpha evidence: `<=1/day` no flag, `(1,2]/day` soft flag, `(2,5]/day` hard rejection, and `>5/day` independent absolute rejection. A slow daily donor ordinarily satisfies it mechanically.

Perturbation seeds measure path stability, not independent samples. A path without sufficient episodes is `INSUFFICIENT_SAMPLE`, not a pass.
Episode 95% tail loss remains diagnostic because 30 episodes do not estimate a
stable 5% tail; it is not an activation gate.

## Modules and verification

New research-only modules:

- `zenquant/research/time_space_shadow.py`;
- `zenquant/research/trend_participation.py`;
- `scripts/run_time_space_shadow.py`;
- `scripts/run_trend_participation.py`;
- independent configs/manifests under `config/research/`;
- focused tests for each layer.

Required verification:

- future rows cannot alter earlier state;
- source and perturbation gaps remain distinguishable;
- daily availability and first eligible 4h cutoff are exact;
- state transitions are mutually exclusive and golden-tested;
- invalid input fails flat and requires full warmup;
- completed direct flips reconcile 2x target turnover while partial fills
  reconcile exact realized turnover and zero-crossing episode splits;
- episode, fee, funding, implementation-shortfall diagnostics and stress costs
  reconcile without double-counting slippage;
- forward ledgers start flat at the registered boundary;
- receipts cannot be synthesized from local timestamps;
- evidence roles cannot aggregate or relabel reused history;
- research imports cannot flow into strategy, risk, execution or live adapters;
- shadow/donor OFF leaves existing production material byte-identical;
- existing golden, parity, A1-A5 and OptionsState tests remain unchanged;
- complete `zenquant-check` runs before merge.

## Rejection conditions

Reject this exact donor rather than tune it when:

- 15bp net PnL or paired economics fail;
- lower switching is obtained by inactivity and lost trend capture;
- performance requires selecting a favorable episode, month, age bucket, seed or regime;
- any frozen placebo performs as well or better;
- edge comes primarily from leverage, one seed or one period;
- results require mixed source fallback;
- paired forward `net_pnl_15bp` delta is negative in any sufficiently sampled regime bucket.

Any changed lookback, threshold, feature authority, exit, fallback or gate is a newly registered donor, not a retry.

## References

- Moskowitz, Ooi and Pedersen, *Time Series Momentum*.
- Kim, Tse and Wald, *Time Series Momentum and Volatility Scaling*.
- *Momentum Turning Points* (SSRN 3489539).
- Kang and Ryu (2026), Bitcoin trend-following speed comparison.
- Sepp and Lucic, `TrendFollowingSystems` replication repository.
