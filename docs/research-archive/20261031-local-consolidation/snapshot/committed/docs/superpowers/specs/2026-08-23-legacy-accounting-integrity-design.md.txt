# Legacy Accounting Integrity Repair Design

## Status

Proposed P0 correction as of 2026-08-23. This repair must be accepted and
completed before old parameter sets are compared, retrained or promoted.

## Problem

The legacy portfolio ledger permits negative cash after a leveraged purchase,
but fee application currently floors cash at zero:

```text
cash_after_fee = max(0, cash_before_fee - fee)
```

Whenever `cash_before_fee - fee < 0`, this erases some or all of the financing
liability and creates equity. The defect is path-dependent and is amplified by
leverage and compounding. Consequently, historical equity, return, Sharpe,
drawdown and equity-multiple claims produced by this ledger require explicit
validity classification before they can be used as performance evidence.

This is an accounting defect, not a strategy-policy change. In a fixed-event
ledger replay it changes only cash, equity and their derived metrics. In a full
system rerun, corrected equity, drawdown and risk state may legitimately change
subsequent targets, orders and fills; those downstream differences must be
attributed rather than suppressed.

## Accounting Contract

Cash is a signed balance. Fees are always an expense and may make cash more
negative:

```text
cash_after_fill = cash_before_fill - signed_quantity * fill_price
cash_after_fee  = cash_after_fill - fee
equity          = cash + sum(position_quantity * mark_price)
```

Events have an explicit order. With the mark held constant, a fill must satisfy:

```text
cash_after  = cash_before - delta_quantity * fill_price - fee
equity_delta = delta_quantity * (mark_price - fill_price) - fee
```

A mark event with no fill satisfies
`equity_delta = position_before * delta_mark`. External cash flow is added
exactly once as cash. Funding and financing are not integrated into legacy
equity today and are explicitly out of scope for this repair; their diagnostic
accumulators must not be represented as portfolio-equity parity.

Fees must be finite, non-negative and applied exactly once. Invalid fees fail
closed; they are never clamped, ignored or converted into an account credit.
Short-sale proceeds, realized PnL and signed positions remain governed by the
same identity.

## Repair Boundary

The minimum code change removes the zero floor from legacy fee application and
adds explicit validation. The repair also introduces invariant checks at the
portfolio-ledger boundary so the same class of defect cannot silently return.

The following are intentionally deferred:

- exchange-specific margin liquidation;
- borrowing limits and interest models;
- carry-strategy financing semantics;
- changes to Alpha, risk sizing or execution policy;
- retroactive mutation of frozen raw artifacts.

Those are separate models. Their absence cannot justify breaking cash and
equity conservation.

## Historical Result Validity

Affected artifacts are preserved for audit, not deleted or silently rewritten.
Validity is assigned at metric-family and stage granularity using these states:

```text
CONFIRMED_AFFECTED     clamp condition occurred in the event path
UNVERIFIED_LEGACY     event-level evidence is insufficient; cannot promote
PROVEN_UNAFFECTED     fixed-event audit proves the clamp never bound
SUPERSEDED_REPAIRED   a corrected frozen-input rerun exists
```

The mechanical clamp condition is `cash_before_fee - fee < 0`. Confirmed and
unverified legacy metric families cannot enter promotion. At minimum, the audit
covers return, net PnL, equity multiple, Sharpe, Sortino, drawdown and any gate
derived from them. Parameter identity and raw market data remain valid if their
hashes verify. Strategy decisions, orders and fills remain descriptions of the
old event path, but are not automatically canonical repaired evidence because
risk state may have consumed defective equity.

The August 2026 rank1 train diagnostic is explicitly invalidated. It was also
never an accepted candidate: test was not evaluated, no frozen candidate ID
was issued and handoff readiness was false.

A machine-readable validity manifest records:

- artifact identity and content hash;
- ledger implementation/formula hash;
- validity state and mechanical reason;
- affected metric families and stages;
- superseding rerun identity, when available;
- timestamp and approving decision.

Reports and campaign loaders must display or fail on invalidated performance
evidence instead of presenting it as comparable to repaired results.

## V2 Isolation

The frozen V2 stage ledger uses fixed notional and its own fee accounting, but
some stages can still consume legacy `RiskSnapshot` safety state. No A1-A5
stage is presumed valid. A provenance audit must rebuild the legacy risk donor
and compare, per stage, the input-row hash, safety flags, approved exposure,
filled exposure and PnL.

A stage is `PROVEN_UNAFFECTED` only when its extracted stage payload remains
field-identical after the corrected risk donor is substituted. The comparison
excludes the artifact-level validity envelope and unrelated A0 fields. Any
stage whose output changes is `CONFIRMED_AFFECTED` and must be regenerated.
A0 episode output may describe the frozen old fill path, but cannot become
canonical promotion evidence without a repaired full-system rerun.

## Replay and Runtime Parity

Backtest, replay and paper/live adapters must call the same corrected portfolio
accounting kernel for equivalent fill, fee and mark events. No adapter may
repair the number downstream or use a private cash floor. Legacy funding and
financing remain `OUT_OF_SCOPE_NOT_EQUITY_INTEGRATED` until a separately
approved accounting design integrates them.

Existing golden output is expected to change where it encoded defective
performance. The golden must not be updated as a mechanical test fix. The
review record must first show:

1. a fixed-event replay showing identical fills, marks and charged fees with
   only cash, equity and derived metrics changing;
2. corrected event-level conservation identities;
3. a full-system rerun that attributes any risk-state, target, order and fill
   divergence after the first affected fee;
4. explicit product-owner acceptance of the new golden.

## Required Tests

- leveraged long purchase leaves cash negative and a fee reduces equity by the
  exact fee amount;
- short open, cover and long/short round trips conserve equity;
- partial fills and multiple fees apply each fee exactly once;
- multi-symbol marking sums signed position values correctly;
- zero-fee behavior is unchanged;
- fees smaller than, equal to and larger than positive cash, plus fees applied
  while cash is already negative, obey the signed-cash contract;
- invalid, negative or non-finite fees fail closed atomically without mutating
  cash, equity or risk state;
- cash/equity and episode ledgers reconcile fill gross PnL plus fees, explicitly
  excluding funding and financing from the comparison;
- high-water mark and drawdown are derived from corrected equity;
- backtest, replay and paper/live fill/fee/mark accounting parity holds for one
  shared event stream;
- fixed-event ledger replay and full-system rerun have separate assertions;
- mutating legacy `RiskSnapshot` drawdown, flags or reduce-only state proves
  that every dependent V2 stage is classified as affected;
- extracted V2 stage payloads remain field-identical only when proven
  independent, while the mixed-validity artifact receives a new envelope;
- a mixed-validity loader blocks only affected or unverified metric families
  and stages;
- the historical validity manifest prevents invalidated metrics from entering
  training comparison or promotion gates.

## Rerun Order

After the repair passes focused and full gates:

1. register affected historical artifacts as invalidated;
2. rerun the canonical legacy baseline with frozen data, costs and parameters;
3. rerun only historically important candidate parameter sets, without search;
4. publish fixed-event and full-system old-versus-corrected attribution at
   risk-state, order, fill, fee and equity levels;
5. proceed to donor retirement and new-donor experiments using only repaired
   performance evidence.

The rerun is diagnosis, not an opportunity to retune parameters.

## Acceptance

- no tested event can create equity through fee application;
- cash/equity and episode fill gross PnL plus fees reconcile within a declared
  numeric tolerance; episode funding is reported separately as
  `OUT_OF_SCOPE_NOT_EQUITY_INTEGRATED` and no total-net reconciliation is
  claimed;
- all affected historical performance is discoverably invalidated;
- invalidated evidence cannot pass a campaign or promotion gate;
- August raw artifacts remain reproducible as historical event paths, but the
  historical high-profit claim is no longer treated as valid;
- `zenquant-check` passes, with any golden change separately reviewed and
  approved.
