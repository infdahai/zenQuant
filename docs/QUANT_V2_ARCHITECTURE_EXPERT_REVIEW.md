# Quant V2 Architecture Expert Review

## Review verdict

The V2 boundary design is technically acceptable for continued research: it
creates one immutable risk-final 4h decision, constrains execution to target
tracking, accounts by position episode, and keeps options data observation-only.
It is not approved for promotion or another parameter-training round. The
frozen OOS experiment fails the 15bp cost gate on every seed and does not yet
contain a frozen hostile-window acceptance definition.

This document is the architecture review. Operational commands and measured
results live in [QUANT_V2_ACCEPTANCE_HANDOFF.md](QUANT_V2_ACCEPTANCE_HANDOFF.md).

## Authority flow

```text
confirmed BTC 4h close
        |
        v
point-in-time alpha + MarketState + vol/risk inputs
        |
        v
risk-final immutable DecisionSnapshot
        |
        +---------------------------> OptionsState sidecar
        |                              research subject only
        |                              no target/risk/order authority
        v
target_tracking_v2 execution kernel
        |
        v
fills -> position episode ledger -> Gate v3 / attribution
```

The legacy strategy and execution path remains the default. V2 is reachable
only through explicit architecture/execution modes.

## Contract map

| Layer | Authoritative output | Allowed authority | Explicitly forbidden |
| --- | --- | --- | --- |
| 4h decision | `DecisionSnapshot` | Freeze risk-final signed target and identities | Lower-timeframe re-score or target rewrite |
| Risk | signed original/revised target hashes | Reduce, cap, reduce-only, kill | Unsigned target mutation |
| Execution V2 | delta toward approved target | Slice, delay, cancel, retry, safety reduction | DCA, autonomous flip, profit-protect sizing, target overshoot |
| Episode ledger | closed/open `PositionEpisode` | Attribute fills, fees, funding, MFE/MAE | Count fill fragments as independent trades |
| A5 cost shadow | cost observations and 1/3/6-bar labels | Measurement only | Blocking or changing an order |
| OptionsState shadow | PIT state ID and counterfactual labels | Research publication only | Reading by strategy/risk/execution or activating multiplier |

## Frozen experiment stages

- `A0`: original August rank1 replay under its original BTC+ETH quorum.
- `A1_SIGN`: 4h `sign(raw_score)`, fixed 1x notional, no compounding.
- `A1_SCORE`: A1 sign multiplied by frozen confidence.
- `A2`: 20% annualized volatility target, maximum 1.5x; invalid volatility is flat.
- `A3`: frozen regime multipliers `1.00 / 0.50 / 0.10 / 0.00`.
- `A4`: the only target-position band, 4% with exchange-minimum floor and safety bypass.
- `A5_SHADOW`: A4 target unchanged; fees, slippage, shortfall, breakeven and forward returns only.

The causal A1-A5 donor is BTC-only so each confirmed 4h decision has zero alpha
availability lag. Its identity and results are separate from A0; donor metrics
must never be relabeled as the original August baseline.

## Time semantics

Live 4h decisions use the exact bar boundary. Historical Binance files encode
the same confirmed close as `boundary - 1ms`. OptionsState uses a strict adapter
that accepts only those two encodings and maps the historical value to the exact
boundary. It does not round arbitrary timestamps. A source later than that
boundary remains future data and is rejected.

A0/A1 economic timing and historical decision IDs retain the source bar-close
timestamp. Only the OptionsState point-in-time join uses the canonical boundary,
and the artifact stores both values.

## OptionsState isolation

OptionsState covers ATM IV, Black-76 25-delta IV/RR, IV-RV, term slopes, spot/skew
change, OI concentration and normalized GEX diagnostics. Missing, stale or
invalid data yields `UNKNOWN` with neutral multiplier `1.0`.

The sidecar attaches only after the risk-final snapshot and republishes on a
research-only subject. OFF/ON contract tests prove identical approved target,
order, fill and equity material. The current real chain begins in 2026, so it
cannot explain the frozen 2025 OOS window; activation remains prohibited.

## Review risks and next approval boundary

- Economic edge is not robust to the frozen late-dev 15bp stress.
- The manifest has no explicit hostile window or quantitative hostile threshold;
  that hard gate is currently not evaluable and must be frozen before a future run.
- The 952MB integrated artifact is intentionally ignored by Git. Its SHA-256 and
  compact verdict are recorded in the handoff.
- Any real OptionsState multiplier, new parameter search, new asset, or change to
  the frozen windows/seeds/costs requires a separate product-owner approval.

The next legitimate research step is attribution of the late-dev cost failure,
not another optimizer run.
