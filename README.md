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
python3 -m unittest discover -s tests -v
```

---

## 12. Current Status vs Future Work

### In current PR scope
- Deterministic Phase 1 vertical slice is implemented.
- Core contracts are versioned and auditable.
- Replay parity tests exist for indicators/intents/execution.

### Deliberately deferred (later phases)
- HMM/change-point regime engine,
- options/stablecoin macro features,
- advanced execution slicing,
- multi-portfolio allocator,
- full production persistence adapters.

---

## 13. Practical Notes for Reviewers

If you are reviewing this project for correctness, start here:
1. Event ordering around bar close and snapshot cutoff.
2. Indicator point-in-time metadata and quality flags.
3. Intent rewrite path (`target_position -> final`).
4. Execution delta logic and fee/equity interactions.
5. Replay test parity.

If these pass, the core kernel is behaving as designed.

---

## 14. Disclaimer

This repository is infrastructure/research code for quantitative trading.
It is not investment advice. Crypto markets are highly volatile, and misuse can lead to large losses.
