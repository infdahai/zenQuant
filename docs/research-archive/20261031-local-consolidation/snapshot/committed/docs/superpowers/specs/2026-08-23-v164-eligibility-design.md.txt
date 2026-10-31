# v164 Eligibility and Forward Validation Design

## Objective

Determine whether v164 contains directionally distinct information worth a
single forward validation. This is an eligibility study, not a declaration that
v164 is an independent donor or a request to tune it.

## Existing evidence

v164 was the top train diagnostic from a 12-trial, 152-parameter smoke campaign
whose formal winner remained `paulwei_baseline`. It was never frozen for
handoff. Gate v2 later observed:

- late-dev baseline positive on 5/5 seeds;
- late-dev extra-15bp positive on 4/5 seeds;
- complete Gate passed on 0/5 seeds;
- unstable 2021/2022 behavior and train/capacity blockers.

It belongs to the same signal family as August: the indicator configuration and
signal weights match, while multi-momentum direction-plugin overrides and many
strategy, risk and execution parameters differ. Independence must be measured
rather than assumed.

## Identity freeze

`v164_donor_v1` receives an immutable manifest containing:

- exact source result and selector;
- parameter, profile, formula and source-code hashes;
- data cutoff and quality manifest;
- fixed seeds, noise, dropout and costs;
- explicit train, retrospective, quasi-OOS and forward roles;
- no tuning knobs.

The existing August manifest and golden artifacts remain unchanged. The A0-A5
loader must support a declared manifest schema without replacing the August
default identity.

## Evaluation phases

### Phase 1: retrospective mechanism attribution

Run train and 2025 dev through the same fixed-notional A1 and V2 A2-A5
contracts. These windows establish episode, execution and cost mechanics only;
they are not new OOS evidence.

Compare v164 against August using:

- A1 direction-decision disagreement rate;
- fixed-notional return correlation;
- overlapping flip and episode timestamps;
- incremental return after projecting out August fixed-notional returns.

Independent-donor eligibility requires all of the following retrospective
thresholds on fixed-notional A1 returns and decisions:

- absolute Pearson return correlation with August no greater than `0.75`;
- direction-decision disagreement at least `20%` on common valid cutoffs;
- overlapping same-side episode time no greater than `75%` of v164 exposure.

Failure of any threshold classifies v164 as `same_family_sleeve_only`. Positive
residual return after projecting out August remains a reported diagnostic and
cannot override these thresholds.

### Phase 2: one-time historical quasi-OOS

The 2026 history predating manifest registration was inspected globally by
other strategies. It may be evaluated once for v164 under the label
`QUASI_OOS_ALREADY_OPENED_GLOBALLY`. Results cannot feed back into parameters,
thresholds, manifests or the forward Gate.

### Phase 3: authoritative forward evidence

Promotion evidence begins only after manifest registration. It accumulates
confirmed bars and closed episodes without changing the frozen contract.

## Eligibility gates

The v164 protocol intentionally uses a `4/5` economics rule instead of changing
the frozen August all-seed rule. Safety and purity rules remain all-seed. Final
eligibility requires at least `180` forward calendar days, `12` closed episodes,
four non-overlapping 30-day forward windows containing exposure, and both a
normal state and one confirmed state transition. Until then the result is
`INSUFFICIENT_SAMPLE`.

The donor must satisfy:

- baseline forward OOS positive on at least 4/5 perturbation seeds;
- extra-15bp forward OOS positive on at least 4/5 seeds;
- closed-episode PF after all observed costs at least `1.15` on 4/5 seeds, with
  no seed below `1.0`;
- maximum drawdown below `20%` in every seed and declared forward window;
- more than `2` switches/day blocks; `5` is an absolute ceiling;
- executor-created exposure, reversal and overshoot all zero;
- hostile/stress bars are defined by the frozen A3 state; their cumulative loss
  may not exceed `5%` of initial capital and their segment MDD may not exceed
  `8%` on any seed; absence of hostile/stress observations is insufficient;
- OptionsState execution influence zero;
- no one of the four or more 30-day forward windows contributes more than 50%
  of positive gross window profit, where the denominator is the sum of positive
  forward-window profits.

`NO_LOSS_EPISODES` requires at least two closed episodes. Insufficient episode
or market-state coverage fails closed.

Capital gates such as 10-12% drawdown, PF target 1.25, Calmar, paper-live episode
count and realized slippage are later promotion requirements, not a reason to
relax this research Gate.

## Terminal outcomes

- `reject_v164`;
- `same_family_sleeve_only`;
- `continue_forward_observation`;
- `eligible_for_separate_paper_live_review`.

No result authorizes a third tuning round.
