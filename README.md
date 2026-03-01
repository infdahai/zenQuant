# ZenQuant 4.0

Deterministic crypto trading kernel for **BTC/ETH perpetuals**, focused on **4H/1D bar-close decisions**.

This repository is for building one thing well first:
1. A flagship strategy that is robust and auditable.
2. An indicator dashboard that explains *why* the strategy increases or reduces risk.

It is **not** an HFT engine, and it is **not** a copy-trading platform. If copy-trading is used, it is done through exchange-native tools.

---

## 1. Who This Is For

This project is for:
- Traders/researchers who want backtest -> replay -> live behavior to stay consistent.
- Engineers who need clean event contracts and reproducible decisions.
- Team members/reviewers who want clear, plain-language system design.

---

## 2. Why This Project Exists

Most open-source quant systems break when moving from backtest to live because of:
- hidden lookahead,
- inconsistent feature definitions,
- strategy bypassing risk controls,
- execution assumptions that never hold live,
- weak auditability.

ZenQuant’s hard requirement:

> Same event stream + same indicator version + same formula config -> same decisions.

---

## 3. Phase 1 Goal (Business + Engineering)

### Business outcome
Ship a strategy + dashboard package that users can understand and trust.

### Engineering outcome
Deliver a deterministic vertical slice:
- market ingest -> bars/snapshots -> indicators -> target intent -> risk overlay -> execution reports.

### Phase 1 scope
- Symbols: `BTCUSDT_PERP`, `ETHUSDT_PERP`
- Timeframes: `4h`, `1d`
- Primary data families:
  - trades,
  - orderbook depth,
  - funding,
  - open interest.

---

## 4. Non-Negotiable Invariants

1. **Bar-close decisions only**
- Directional strategy decisions happen on `clock.bar_close.{4h|1d}`.

2. **Intent before orders**
- Strategy emits `TargetPosition`, not direct order placement.

3. **Risk can rewrite intent**
- Risk overlay can reduce leverage, force reduce-only, or cap exposure.

4. **Point-in-time indicators**
- Every indicator output carries:
  - `cutoff_time`,
  - `indicator_version`,
  - `formula_hash`,
  - `quality_flags`,
  - `coverage`.

5. **Replay parity**
- Replay must reproduce indicator stream, intents, and execution reports in deterministic order.

---

## 5. High-Level Architecture

```text
md.trade/md.book/md.funding/md.oi
  -> BarClockService (4h/1d close)
  -> SnapshotBuilder (point-in-time snapshot)
  -> IndicatorEngine (pack.v1)
  -> ARC Strategy (TargetPosition)
  -> RiskOverlay (rewrite/cap/reduce-only)
  -> ExecutionKernel (orders/reports)
```

### Core runtime modules
- `zenquant/market/bar_clock.py`
- `zenquant/market/snapshot.py`
- `zenquant/indicator/engine.py`
- `zenquant/strategy/engine.py`
- `zenquant/risk/guard.py`
- `zenquant/execution/router.py`
- `zenquant/app/server.py`

---

## 6. Event Subjects (Current)

### Market + clock
- `md.trade.{venue}.{symbol}`
- `md.book.{venue}.{symbol}`
- `md.funding.{venue}.{symbol}`
- `md.oi.{venue}.{symbol}`
- `clock.bar_close.4h`
- `clock.bar_close.1d`
- `md.candle.4h.{symbol}`
- `md.candle.1d.{symbol}`
- `md.snapshot.4h`
- `md.snapshot.1d`

### Indicators
- `indicator.pack.v1.4h.{symbol}`
- `indicator.pack.v1.1d.{symbol}`

### Intents + risk + execution
- `intent.target_position.{portfolio}.{symbol}`
- `intent.target_position.final.{symbol}`
- `intent.reason.{portfolio}`
- `risk.overlay.decision`
- `risk.snapshot`
- `exec.order.submit`
- `exec.order.report`

---

## 7. Indicator Pack v1 (Phase 1)

Current output includes:
- `realized_vol`
- `atr`
- `ob_liq_z`
- `overall_ob_liq_z`
- `cvd_slope`
- `funding_oi_z`
- `funding_oi_weighted`
- `zen_pulse`

### Quality and coverage fields
- `quality_flags` examples:
  - `OK`,
  - `BOOK_STALE`,
  - `VENUE_DOWN`,
  - `MISSING_OI`,
  - `LOW_OI_COVERAGE`,
  - `DATA_STALE`,
  - `DATA_FUTURE`.
- `coverage`:
  - `venue_count`,
  - `oi_coverage`,
  - `staleness_ms`.

### Important implementation notes
- Robust normalization uses `median + MAD`.
- Formula hash includes key configuration values.
- Pending/aggregation internal buffers are garbage-collected to avoid unbounded growth.

---

## 8. Strategy and Risk (Phase 1)

### ARC strategy (current behavior)
- Emits `TargetPosition` with:
  - `intent_id`,
  - `target_qty`, `target_leverage`,
  - `regime`, `regime_confidence`,
  - `reason_codes`.
- Regime uses threshold + hysteresis (not HMM yet).
- Dynamic sizing reads current equity from `risk.snapshot`.

### Risk overlay (current behavior)
- Drawdown step throttles.
- Volatility shock throttle.
- Crowding discount and severe crowding reduce-only.
- Daily loss limit safe cap.
- Hard kill-switch gate.
- Uses **actual filled positions** (from execution reports), not target proxy positions.

### Execution kernel
- Consumes `intent.target_position.final.*`.
- Trades deltas with per-bar quantity caps.
- Emits deterministic execution reports.

---

## 9. API and Dashboard

### REST
- `GET /api/indicators/latest?symbol=BTCUSDT_PERP&tf=4h`
- `GET /api/indicators/history?symbol=BTCUSDT_PERP&tf=4h&from=...&to=...`
- `GET /api/state?symbol=BTCUSDT_PERP&tf=4h`

### WebSocket
- `WS /ws/indicators?symbol=BTCUSDT_PERP&tf=4h`
- `WS /ws` (state stream)

### UI design intent
Keep UI simple and trustworthy:
- candle trend,
- key indicators,
- regime + leverage cap,
- alerts timeline,
- intent reason card,
- execution reports.

---

## 10. Storage Contracts (Phase 1 SQL)

`sql/clickhouse/003_factors.sql` defines:
- `indicators.pack_v1`
- `strategy.intents` (with `intent_id`)
- `risk.snapshots` (with `portfolio`)

Rule: store enough data to reproduce decisions and audit intent-to-execution linkage.

---

## 11. Getting Started

### Prerequisites
- Python 3.11+

### Install
```bash
python3 -m pip install -e .
```

### Run local simulation
```bash
python3 -m zenquant.app.demo
```

### Run web app
```bash
zenquant-web
# open http://127.0.0.1:8090
```

### Run tests
```bash
# pytest (recommended)
pip install -e ".[test]"
pytest

# or legacy unittest
python3 -m unittest discover -s tests -v
```

---

## 12. Critical Gaps (code audit → 4H+ profitability)

Phase 1 proved determinism and wiring. But several gaps must be closed before any backtest result is meaningful or the strategy can profit on real 4H+ data.

### P0 — Without these, backtest results are not trustworthy

| # | Gap | Where | Why it matters |
|---|---|---|---|
| 1 | **No exit logic** | `strategy/engine.py` recomputes `target_qty` from scratch every bar with no memory of entry price | Destroys trend-following edge; whipsaws on every bar close instead of riding winners |
| 2 | **No position state** | `ARCStrategyEngine` has no `entry_price`, `trailing_stop`, or `pyramid_count` | Cannot implement trailing stops, pyramiding, or time-decay exits |
| 3 | **Regime is CVD-only** | `_classify_regime` uses `abs(cvd_slope) * 10` and `zen_pulse` thresholds only | Cannot distinguish a genuine breakout from a single large market order; no price-structure input |
| 4 | **Vol unit ambiguity** | `target_vol_4h = 0.025` vs `realized_vol` annualized by `sqrt(2190)` | If `0.025` is per-bar and `realized_vol` is annualized, leverage is ~47x too small. Must clarify units |
| 5 | **No backtest pipeline** | No historical data loader, no walk-forward harness, no metrics calculator | Cannot evaluate or iterate on strategy parameters before going live |

### P1 — Without these, live performance will significantly lag backtest

| # | Gap | Where | Why it matters |
|---|---|---|---|
| 6 | **No multi-timeframe confluence** | 4H and 1D are independent signal paths | 4H TREND-LONG can fire while 1D is bearish — whipsaw against dominant structure |
| 7 | **No slippage model** | `gateway.py` fills at `mark_price` with zero impact | Backtest Sharpe inflated, especially when strategy "sizes up in big-vol regimes" |
| 8 | **No funding cost** | Holding perp positions across funding intervals costs real money, not modeled | Edge can be entirely eaten by funding in crowded trending markets |
| 9 | **ZenPulse degraded handling** | Degraded components get a flat penalty, weights not redistributed | Composite score biased when components are missing |

### P2 — Important for robustness, can follow P0/P1

| # | Gap | Where | Why it matters |
|---|---|---|---|
| 10 | **BTC/ETH correlation** | No portfolio-level leverage cap | Effective 2x concentration when both trend same direction |
| 11 | **No intra-bar risk trigger** | Risk only evaluates at bar close | Flash crash can wipe position before next 4H close |
| 12 | **Range module unguarded** | Mean-reversion fires without structure detection | Loses money in trending markets |

---

## 13. Phase 2 Roadmap (priority-ordered)

### Phase 2A — Fix foundations (before any strategy tuning)

- [ ] **[P0-1] `SwingStructure` indicator** — N-bar pivot detection, HH/HL/LH/LL tracking, `structure_break` flag
- [ ] **[P0-2] `Momentum_ROC` indicator** — rate-of-change z-score for trend confirmation
- [ ] **[P0-3] Vol unit fix** — document and enforce annualized units everywhere; adjust `target_vol` defaults
- [ ] **[P0-4] Backtest pipeline** — historical data loader (Binance klines → parquet), replay-based runner, metrics (Sharpe, max DD, win rate, profit factor, regime-conditional)
- [ ] **[P0-5] Walk-forward harness** — 12-18mo train / 3mo test, roll forward, flag OOS Sharpe < 0

### Phase 2B — Strategy with real edge

- [ ] **[P0-6] Stateful position tracking** — add `_open_positions: dict[str, OpenPositionState]` with `entry_price`, `entry_bar`, `trailing_stop`, `pyramid_count`, `direction`
- [ ] **[P0-7] Exit logic** — trailing ATR stop (2.5x initial, 2.0x trail), structure-break exit, regime-flip exit, time-decay exit (30 bars)
- [ ] **[P0-8] Regime upgrade** — require `SwingStructure` confirms (HH for uptrend) AND CVD confirms; not CVD-only
- [ ] **[P1-1] Multi-timeframe confluence** — 1D directional bias gates 4H entries; 1D STRESS forces 4H reduce-only
- [ ] **[P1-2] Pyramiding** — add to winners with decaying size (`base * 0.5^n`), max 3 layers
- [ ] **[P1-3] Disable range module** until SwingStructure is proven in walk-forward backtest

### Phase 2C — Cost realism + risk hardening

- [ ] **[P1-4] Slippage estimator** — `base_spread_bps + (notional / bar_volume) * impact_coeff`; fill at adjusted price
- [ ] **[P1-5] Participation rate cap** — `max_participation = 0.05`; carry unfilled to next bar
- [ ] **[P1-6] Funding cost model** — deduct `position_notional * funding_rate` from equity each funding interval
- [ ] **[P2-1] Correlation-aware portfolio cap** — if `corr(BTC,ETH) > 0.8`, treat as single-asset leverage
- [ ] **[P2-2] Intra-bar emergency trigger** — subscribe `md.trade.>` in RiskOverlay; if price moves > 3x ATR within bar, force reduce-only

### Phase 2D — Validation gate

- [ ] Walk-forward: out-of-sample Sharpe > 0.8 on 2+ years BTC/ETH
- [ ] Parameter sensitivity: Sharpe drop < 40% on any single param ±20% perturbation
- [ ] Paper trading: 4+ weeks matching backtest expectations
- [ ] Real exchange adapters (Binance/OKX) for live deployment

### Acceptance criteria
- Same event stream → same outputs (replay parity preserved).
- Strategy behavior explainable at bar close with stable reason codes.
- Backtest includes funding + slippage costs.
- Risk overlay caps exposure deterministically under stress.

### Non-goals for Phase 2
- Asset expansion beyond BTC/ETH perp.
- Multi-portfolio allocator.
- Ultra-low-latency exchange optimization.

---

## 14. Practical Notes for Reviewers

Start here for correctness review:
1. Event ordering around bar close and snapshot cutoff.
2. Indicator point-in-time metadata and quality flags.
3. Intent rewrite path (`target_position -> final`).
4. Execution delta logic and fee/equity interactions.
5. Replay test parity.

---

## 15. Disclaimer

This repository is infrastructure/research code for quantitative trading.
It is not investment advice. Crypto markets are highly volatile, and misuse can lead to large losses.
