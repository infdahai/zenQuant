# Task 6 Report: Point-in-time OptionsState shadow

## Result

Implemented an immutable, BTC-only, observation-only `OptionsState` sidecar.
The calculation path performs no network I/O and is not attached to
`DecisionSnapshot`, alpha, targets, orders, or positions.

## Commits

- `80e806d feat(data): add immutable point-in-time options state`
- `eb2b19a feat(data): add options state asof store`
- `5eae41c feat(data): report Deribit snapshot completeness`
- `5ca222c fix(data): distinguish options skew from risk reversal`
- `bdc6f41 fix(data): name Deribit moneyness skew proxy explicitly`
- `b3d08f2 fix(data): harden options state data-quality fallbacks`
- `3c36612 fix(data): remove active 25d label from skew proxy`
- `7c04f70 fix(data): validate Deribit snapshot readiness`
- `7548452 test(data): use valid Deribit readiness fixture`
- `b963eef fix(data): bump options state calculation version`
- `7a63a99 test(data): cover proxy runtime boundaries`
- `859e225 fix(data): quarantine malformed options store rows`
- `2a98f65 test(data): use valid forward health snapshots`
- `2942661 fix(data): describe options IV unit normalization`

## Contracts implemented

- Confirmed 4h cutoff, BTC-only, current source `<= cutoff`, and realized-vol
  source `<= cutoff` are mechanically enforced.
- Stable identity binds the complete selected current chain, the accepted 24h
  ASOF history chain, cutoff, formulas, inputs, outputs, and quality state.
- Input canonicalization uses a total ordering over full normalized rows.
  Exact duplicate instruments are handled deterministically, explicitly
  flagged, and produce a neutral `UNKNOWN` state independent of input order.
- `OptionsState` is frozen and has deterministic JSON-compatible round-trip
  serialization.
- Deribit percentage `mark_iv` is explicitly normalized to annualized decimal
  volatility.
- ATM IV is calculated for 7d, 30d, and 90d using deterministic nearest-expiry
  selection and OI-weighted nearest-forward strikes.
- 30d true 25-delta put and call IV use Black-76 forward delta with the
  snapshot index/underlying price as the forward proxy and interpolation in
  delta space.
- Selected 7d/30d/90d expiries, actual per-leg 25-delta methods
  (`exact_delta`, `linear_delta_interpolation`, or `nearest_delta`), and delta
  brackets are serialized into the state and formula identity.
- The calculation-method string explicitly records percent-or-decimal IV input
  normalization to annualized decimal units rather than claiming a percent-only
  path.
- Conventional 25d risk reversal is `call IV - put IV`; bearish put-call skew
  is separately recorded as `put IV - call IV`.
- The old 90%-put minus 110%-call field is explicitly named
  `moneyness_skew_proxy`; deprecated `put_call_skew_25d` properties exist only
  on legacy `OptionsSnapshot` / `DeribitRiskPack` boundaries.
- Indicator values, persistence SQL/schema, migration, and factor-audit output
  use the explicit `put_call_moneyness_skew_proxy` name. No active indicator
  key or database column presents the proxy as true 25-delta skew.
- Includes 30d IV-RV, 7d-30d and 30d-90d slopes, 24h spot/skew ASOF changes,
  deterministic joint label, strike/expiry OI concentration, normalized net
  GEX, gamma-wall/flip levels and signed spot-relative distances.
- Missing, stale, malformed, incomplete tenor/25d/history/OI/GEX data is
  explicit in quality flags. Missing/stale/incomplete states are `UNKNOWN`
  with neutral `shadow_risk_multiplier=1.0`.
- Malformed numeric IV/OI/strike/expiry/greeks, unknown IV units, and
  mixed/missing source timestamps are data-quality failures: both direct build
  and public store lookup return deterministic neutral states rather than
  aborting. Future-source and BTC-symbol mismatches remain hard contract
  errors.
- GEX consumes the same normalized decimal-volatility contract as ATM/25d;
  percent and decimal representations produce identical calculated metrics.
  Gamma flip exists only when cumulative GEX actually crosses zero and is
  interpolated at the crossing; one-sided profiles return no flip.
- `CONFIRMING | NEUTRAL | FRAGILE | STRESS | UNKNOWN` is deterministic and
  research-only. No multiplier is consumed outside this module.
- `OptionsDataStore.lookup_options_state` is an ASOF join. Adding and reloading
  a future snapshot leaves an earlier state byte-identical.
- Collector status validates readable, non-empty, schema-complete snapshots,
  one embedded source timestamp matching the filename, call/put presence, and
  7d/30d/90d tenor coverage. Invalid and duplicate snapshots are reported and
  excluded from readiness.
- Readiness consistently uses the four-hour cadence: 6 expected snapshots per
  24h and 42 per 7d. A complete 42-snapshot window can become research-ready;
  corrupt or schema-incomplete files cannot satisfy readiness.
- New chain rows persist `mark_iv_unit="percent"` without changing the
  collection process.

## Verification

- TDD RED evidence observed for the original Task 6 contracts and each
  independent-review regression: malformed chain/store fallback, mixed source
  timestamps, IV-unit-equivalent GEX, true gamma crossing, duplicate-order
  identity, per-leg calculation audit fields, explicit proxy boundaries, and
  validated four-hour readiness.
- Independent-review reproductions: `11 passed, 5 subtests passed`.
- Focused options/data suite: `81 passed, 4 skipped, 5 subtests passed`
  (network tests skipped).
- Full suite (outside the filesystem sandbox because ProcessPool tests need
  macOS semaphore-limit access): `1273 passed, 10 skipped, 10 subtests passed`.
- Gate B import boundaries: pass.
- Gate C golden output: pass.
- Gate D legacy/plugin parity: pass.
- Changed-file Ruff lint and format: pass.
- `git diff --check`: pass; worktree clean.

## Known repository-level blockers

- Full Gate A remains blocked by three unrelated pre-existing format drifts:
  `scripts/analyze_intent_churn_attribution.py`,
  `zenquant/data/event_calendar.py`, and
  `zenquant/data/liquidation_pipeline.py`. Ruff lint passes.
- Gate F remains blocked because this isolated worktree has no historical
  Parquet under `data/raw/binance`; the failure is
  `no historical events available for replay`.

## Self-review

- No collector was started, stopped, or restarted.
- No network call was added to the state calculation or lookup path.
- No DecisionSnapshot attachment or order/target influence was added; that
  isolation/join belongs to Task 7.
- Deprecated `put_call_skew_25d` access is limited to clearly documented
  properties at the two legacy object boundaries and a migration-only old
  column rename; runtime indicator and persistence consumers use the canonical
  proxy name.
