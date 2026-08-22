# Time-Space Shadow Attribution Design

## Objective

Test whether early reversals after a confirmed structural breakout destroy
trend value because the system treats price state without sufficient elapsed
time context. The first version is observation-only and has no authority over
targets, risk, orders or positions.

## Isolation boundary

Time-space state is an immutable research sidecar keyed by
`(alpha_id, cutoff_time_ns)`. Stage-specific decision IDs are diagnostic links;
A1 does not manufacture a `DecisionSnapshot`. The sidecar is not added to
`DecisionSnapshot`, `TargetPosition`, episode ledger state or the A1-A5 exposure
formula. Shadow OFF and ON must leave snapshots, targets, orders, fills, equity
and episodes byte-identical.

```text
confirmed 4h OHLC + PIT structure indicators + alpha snapshot
                              |
                              v
                    TimeSpaceStateBuilder
                              |
                   alpha / approved / filled flips
                              |
                              v
                   TimeSpaceFlipObservation
                              |
                   1/2/3/6-bar labels
                              |
                              v
                  research-only attribution
```

## Point-in-time state

`TimeSpaceState` records:

- `state_id`, `alpha_id`, `cutoff_time_ns` and `decision_id_by_stage`;
- `feature_available_at_ns`, equal to the confirmed cutoff;
- formula, config and source-data identities;
- market structure side;
- market trend age as both retained 4h bars and elapsed nanoseconds;
- long and short breakout age as retained bars and elapsed time;
- UTC week identity and weekly completion fraction;
- long-side and short-side structure integrity;
- source gap bars and quality flags;
- constant `blocks_order=false`.

Market trend age is the age of the current confirmed 4h structure state.
Breakout age begins when price first crosses a swing boundary visible at the
previous cutoff. A pivot confirmed by the current bar cannot define a breakout
on that same bar. The breakout associated with a flip is the old side's most
recent still-valid same-direction breakout. A later opposite breakout,
structure reset or source-data gap invalidates the association and produces
`UNKNOWN`.

The calendar week is the half-open interval `[Monday 00:00 UTC, next Monday
00:00 UTC)`. A bar is assigned by its covered interval rather than only its
close timestamp: the bar ending Monday 00:00 closes the prior week at `1.0`;
the next bar belongs to the new week.

Structure integrity for the old side is:

- `INTACT`: confirmed structure still agrees and its PIT invalidation swing has
  not been crossed;
- `BROKEN`: confirmed structure reversed or the invalidation swing was crossed;
- `UNKNOWN`: missing boundary, neutral structure, source gap or insufficient evidence.

`UNKNOWN` is never treated as `INTACT`.

## Flip populations

- `alpha_flip`: A1_SIGN raw-score sign changes; this is the primary causal population.
- `approved_flip`: A5 approved target crosses to the opposite side.
- `filled_flip`: the actual filled position crosses zero; execution diagnostic only.

Flat transitions, same-side reductions and reduce-only safety actions are not
flips. A partial fill is a `filled_flip` if and only if actual filled position
sign crosses zero.

## Counterfactual labels

For horizons `1, 2, 3, 6` confirmed 4h bars, record:

```text
flip_unit_return_h     = new_side * return(cutoff, cutoff+h)
hold_old_unit_return_h = old_side * return(cutoff, cutoff+h)
hold_minus_flip_h      = hold_old_unit_return_h - flip_unit_return_h
```

The primary comparison uses fixed 1x unit direction returns so sizing cannot
contaminate the directional hypothesis. Actual fees, slippage, approved weight
and execution outcome remain separate diagnostics. Each label records its own
availability timestamp; unavailable tail labels are right-censored.

## Pre-registered primary condition

The primary population is defined without searching an hour threshold:

```text
flip and corresponding breakout occur in the same UTC week
AND structure_integrity_for_old_side == INTACT
AND source_gap_bars == 0
```

Continuous ages and weekly progress remain descriptive variables in V1. Long
decline and long base are not V1 conditions; adding either is a new hypothesis.

## Evidence roles

- 2025 late-dev: `DISCOVERY_ONLY`; may test code and reproduce motivating events.
- data available before registration: retrospective falsification only.
- confirmation: begins at the first confirmed 4h cutoff after the committed
  manifest registration timestamp.

An unperturbed canonical path assigns every primary event a stable
`base_flip_id`. A perturbed seed may map to that event only when old side, new
side and alpha identity match and its cutoff is within one retained 4h bar;
otherwise that seed-event is censored. Five perturbation seeds measure path
stability but are not independent statistical samples.

## Acceptance and rejection

Before forward observation begins, the manifest freezes:

- registration cutoff and formula hash;
- `6` bars as the only primary horizon; `1/2/3` are descriptive and cannot
  promote the hypothesis;
- arithmetic mean canonical-path `hold_minus_flip_6` as the primary statistic;
- minimum effect of `15bp` and a one-sided 95% block-bootstrap lower bound above
  zero, with blocks formed by UTC calendar month;
- at least `30` unique post-registration primary flips, `180` elapsed days and
  one confirmed market-state transition;
- primary gross fixed-1x direction returns; cost-adjusted results are secondary;
- minimum 4/5 perturbation paths with positive mean `hold_minus_flip_6`;
- rejection when the sign exists only after selecting favorable age buckets.

Pre-registration history may initialize PIT state, but only flips occurring
after the registration cutoff and labels maturing afterward enter acceptance.
`source_gap_bars` counts missing source 4h intervals between planned cutoffs;
perturbation dropout is recorded separately. When no valid prior breakout
exists, breakout age is `null` and association quality is `UNKNOWN`, never zero.

V1 can recommend only `continue_observation`, `reject_hypothesis` or
`eligible_for_separate_activation_review`. It cannot emit a risk multiplier,
target or order recommendation.

## Verification

- future OHLC changes cannot alter cutoff features;
- breakout boundaries are visible at `t-1`;
- bar-count age and elapsed time diverge correctly under dropout;
- UTC week semantics are timezone and DST independent;
- flip populations do not collapse into one another;
- label availability and right-censoring are exact;
- canonical replay hashes are deterministic;
- shadow OFF/ON execution material is byte-identical;
- 2025 can never produce `CONFIRMED` or `PASS`.
