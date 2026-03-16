# ZenQuant 4.0

**Deterministic crypto trading infrastructure for building auditable, explainable strategies on major crypto perpetuals.**

ZenQuant delivers:
1. A flagship strategy engine with explainable, replayable, and inspectable decisions
2. A real-time dashboard showing why the system increases risk, reduces risk, or refuses to trade

This is **not** an HFT engine, black-box AI bot, copy-trading platform, or "support every idea" framework. It's a trading product that is trustworthy before flashy, and modular before overcomplicated.

---

## Quick Start

```bash
# Install dependencies
uv sync

# Run deterministic backtest
uv run zenquant-backtest --root data/raw/binance --symbol BTCUSDT

# Start live paper-trading runtime (default port 8090)
export ZENQUANT_PG_DSN='postgresql://user:pass@host:5432/zenquant'
uv run zenquant-web --host 127.0.0.1 --port 8090 --mode live

# View dashboard
open http://127.0.0.1:8090
```

---

## Table of Contents

1. [Who This Is For](#who-this-is-for)
2. [Why This Exists](#why-this-exists)
3. [Architecture Overview](#architecture-overview)
4. [Core Design Principles](#core-design-principles)
5. [Current Implementation Status](#current-implementation-status)
6. [Getting Started](#getting-started)
7. [Development Roadmap](#development-roadmap)
8. [For External Reviewers](#for-external-reviewers)
9. [For Internal Developers](#for-internal-developers)

---

## Who This Is For

**External users:**
- Traders who want backtest → replay → live behavior to remain coherent
- Researchers who need reproducible state transitions and deterministic event contracts
- Reviewers who want system design explained in plain language

**Internal developers:**
- Engineers building on a strong base architecture with clear module boundaries
- Contributors who prefer explicit design decisions over implicit magic
- Teams who value auditability and explainability over black-box complexity

If you care about **explainability, auditability, and realistic system boundaries**, this is for you.

---

## Why This Exists

Many open-source trading systems look fine in research but collapse in live operation due to:
- Hidden lookahead bias
- Unstable factor definitions
- Inconsistent time alignment
- Fragile execution assumptions
- Opaque decision logic

ZenQuant addresses these by enforcing:
- **Point-in-time correctness**: No future data leakage
- **Deterministic replay**: Same inputs → same outputs, always
- **Explicit state transitions**: Every decision is auditable
- **Clear failure modes**: System degrades gracefully, never silently

---

## Architecture Overview

### System Layers (ASCII Diagram)

```
┌─────────────────────────────────────────────────────────────────┐
│  Market Data Layer                                              │
│  (Binance WebSocket, Historical CSV, Synthetic Replay)          │
└────────────────────────┬────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│  Bar Clock & Snapshot Builder                                   │
│  • Deterministic bar-close timing (4h, 1d)                      │
│  • Point-in-time cutoff enforcement                             │
│  • Market snapshot aggregation                                  │
└────────────────────────┬────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│  Indicator Engine                                               │
│  • Phase 1: vol, cvd, funding, zen_pulse                        │
│  • Phase 2: swing points, momentum ROC                          │
│  • Quality flags & formula hashing                              │
└────────────────────────┬────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│  Strategy Engine (6-Layer Decision Chain)                       │
│                                                                 │
│  1. State Engine      → 4D market classification                │
│  2. Playbook Engine   → Strategy selection (4 playbooks)        │
│  3. Sizing Engine     → Position sizing with confidence         │
│  4. Exit Engine       → Stop/decay/regime exit logic            │
│  5. Risk Overlay      → Drawdown/volatility constraints         │
│  6. Execution Kernel  → Order routing & fill tracking           │
└────────────────────────┬────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│  Persistence & Observability                                    │
│  • PostgreSQL audit trail (all decision layers)                 │
│  • Real-time dashboard (Vue.js + WebSocket)                     │
│  • Deribit options observation layer (optional)                 │
└─────────────────────────────────────────────────────────────────┘
```

### Decision Chain Flow (ASCII Diagram)

```
Indicator Pack (4h bar close)
         │
         ▼
┌────────────────────────┐
│  State Engine          │  Classifies market into 4 dimensions:
│                        │  • Directional (bull/bear/neutral)
│  Input: Indicators     │  • Volatility (low/normal/high)
│  Output: MarketState   │  • Positioning (light/normal/crowded)
└────────┬───────────────┘  • Quality (clean/degraded/invalid)
         │
         ▼
┌────────────────────────┐
│  Playbook Engine       │  Selects strategy based on regime:
│                        │  • Bull Continuation (trend-following)
│  Input: MarketState    │  • Bear Defense (counter-trend)
│  Output: Playbook      │  • Range Light (mean-reversion)
└────────┬───────────────┘  • Capital Preservation (risk-off)
         │
         ▼
┌────────────────────────┐
│  Sizing Engine         │  Computes position size:
│                        │  base_size × confidence × stress_discount
│  Input: Playbook       │  × execution_haircut = final_leverage
│  Output: SizingDecision│
└────────┬───────────────┘
         │
         ▼
┌────────────────────────┐
│  Exit Engine           │  Priority-based exit logic:
│                        │  1. Invalidation (immediate)
│  Input: Position       │  2. Trailing stop (price-based)
│  Output: ExitDecision  │  3. Time decay (age-based)
└────────┬───────────────┘  4. Regime change (state-based)
         │
         ▼
┌────────────────────────┐
│  Risk Overlay          │  Portfolio-level constraints:
│                        │  • Drawdown limits (3-tier)
│  Input: Target         │  • Volatility shock throttle
│  Output: Final Target  │  • Reduce-only mode enforcement
└────────┬───────────────┘
         │
         ▼
┌────────────────────────┐
│  Execution Kernel      │  Order routing & tracking:
│                        │  • Delta-based rebalancing
│  Input: Final Target   │  • Fill confirmation
│  Output: ExecutionReport│ • Position state updates
└────────────────────────┘
```

### Multi-Timeframe Coordination

```
1d Timeframe                    4h Timeframe
─────────────                   ─────────────

Indicator Pack (1d)             Indicator Pack (4h)
       │                               │
       ▼                               ▼
Directional Bias ──────────────> State Engine (4h)
(Long/Short/Flat)                     │
                                      ▼
                                 Playbook Engine
                                      │
                                      ▼
                                 Sizing Engine
                                      │
                                      ▼
                                 Exit Engine
                                      │
                                      ▼
                                 Target Position

Rule: 4h strategy can only trade in the direction of 1d bias
      (or flat when 1d is neutral)
```

---

## Core Design Principles

### 1. Deterministic Replay
- Same input data → same decisions, always
- No hidden randomness, no system clock dependencies
- Backtest = replay = live (modulo execution slippage)

### 2. Point-in-Time Correctness
- All indicators computed from data available at bar close
- No lookahead bias, no future data leakage
- Cutoff timestamps enforced throughout the pipeline

### 3. Explainable Decisions
- Every decision has reason codes
- Dashboard shows full decision chain
- PostgreSQL audit trail for post-trade analysis

### 4. Graceful Degradation
- Quality flags mark degraded data
- System reduces risk when data quality drops
- No silent failures, no undefined behavior

### 5. Modular Architecture
- Clear layer boundaries with event-driven communication
- Each engine is independently testable
- New features added as modules, not hacks

---

## Current Implementation Status

### ✅ Fully Implemented (Phase 1-6)

**Phase 1: Foundation**
- Historical replay pipeline with deterministic bar-close timing
- Indicator engine with quality flags and formula hashing
- Basic strategy logic with regime awareness
- PostgreSQL audit trail
- FastAPI + Vue.js dashboard

**Phase 2: Multi-Timeframe**
- 1d directional bias filtering
- 4h executable strategy
- Trailing stop mechanism
- Position state tracking

**Phase 3-6: 6-Layer Strategy Engine**
- State Engine: 4D market classification
- Playbook Engine: 4 regime-aware playbooks
- Sizing Engine: Multi-factor position sizing
- Exit Engine: Priority-based exit logic
- Risk Overlay: Portfolio-level constraints
- Execution Kernel: Delta-based rebalancing

**Observation Layers**
- Deribit options risk pack (ATM IV, term structure, skew, OI concentration)
- PostgreSQL persistence for all decision layers
- Real-time dashboard with exit/position panels

### Indicators

Phase 1: `realized_vol`, `atr`, `ob_liq_z`, `overall_ob_liq_z`, `cvd_slope`, `funding_oi_z`, `funding_oi_weighted`, `zen_pulse`
Phase 2: `structure_state` (swing points), `momentum_roc_z`

Quality flags: `OK`, `BOOK_STALE`, `VENUE_DOWN`, `MISSING_OI`, `LOW_OI_COVERAGE`, `DATA_STALE`, `DATA_FUTURE`

### Risk Overlay

The risk overlay can apply the following controls:
- Drawdown step throttles (3-tier)
- Volatility shock throttle
- Crowding discount
- Severe crowding reduce-only behavior
- Daily loss limit safe cap
- Hard kill-switch gating

### 📊 Test Coverage

```
74 tests passing, 4 skipped (require live external systems)

Core coverage:
✅ E2E decision chain tests
✅ Live bootstrap and bar-close tests
✅ Exit engine priority logic
✅ Playbook selection logic (including config override, crowded-long fallthrough)
✅ Sizing computation (including UNSAFE zero-leverage, config overrides)
✅ State classification
✅ Deribit options ingestion
✅ PostgreSQL persistence
✅ Transition risk handoff between playbook and exit
```

### 🔄 Under Active Review

- Full end-to-end verification of module behavior
- Confirmation that implementation matches intended architecture
- Careful review of AI-assisted code paths
- Validation of simplicity choices before adding complexity

### 📋 Next Steps (Code Quality, Not Features)

1. **Confidence blending research** - Compare arithmetic vs geometric/min-based blends
2. **Analytics/reporting** - Hit rate by exit type, profit factor by playbook
3. **Broader backtest reporting** - Extend validation from synthetic regression to real history

---

## Getting Started

### Prerequisites

```bash
# Python 3.11+
python --version

# uv package manager
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### Installation

```bash
git clone https://github.com/infdahai/zenQuant.git
cd zenQuant

# Install dependencies
uv sync

# Run tests
uv run pytest
```

### CLI Entry Points

All commands are installed via `pyproject.toml` entry points:

| Command | Description |
|---|---|
| `zenquant-backtest` | Run deterministic historical replay |
| `zenquant-web` | Start the live paper-trading server |
| `zenquant-live-validate` | Validate a running live instance |
| `zenquant-history-sync` | Download historical market data |
| `zenquant-demo` | Run demo/synthetic mode |

### Running Historical Replay

```bash
# Sync historical data (optional, for real market history)
uv run zenquant-history-sync --year 2024 --month 1 --symbol BTCUSDT

# Run backtest
uv run zenquant-backtest --root data/raw/binance --symbol BTCUSDT
```

### Running Live Paper-Trading

```bash
# 1. Start the live runtime (default port 8090)
export ZENQUANT_PG_DSN='postgresql://user:pass@host:5432/zenquant'
uv run zenquant-web --host 127.0.0.1 --port 8090 --mode live

# 2. Optional: enable Deribit options observation layer
export ZENQUANT_DERIBIT_ENABLED=1
uv run zenquant-web --host 127.0.0.1 --port 8090 --mode live

# 3. Verify runtime health from another shell
curl http://127.0.0.1:8090/healthz
curl "http://127.0.0.1:8090/api/state?symbol=BTCUSDT_PERP&tf=4h"

# 4. Use the built-in validator for repeatable smoke or soak checks
uv run zenquant-live-validate --base-url http://127.0.0.1:8090
uv run zenquant-live-validate --base-url http://127.0.0.1:8090 \
  --require-db --watch-seconds 86400 --output .artifacts/live-24h-summary.json
```

What you should see:
- `mode=live`
- `runtime_status.market_connected=true`
- `last_price` updates in real time
- `latest_indicator` exists
- `latest_target` exists
- if PostgreSQL is enabled, `database.writes` increases over time

**Important bootstrap caveats:**
- The live runtime bootstraps recent closed `4h` and `1d` bars plus recent funding/open-interest history on startup, so a fresh process does not begin from an empty indicator state.
- This bootstrap is warm-state only. It does **not** replay historical orders or historical PnL into the current live paper account.
- Restart recovery for prior simulated positions is still not implemented. A restarted process begins flat, then aligns to the current closed-bar signal.
- `zenquant-live-validate` tracks observed 4h/1d cutoff turnover during the watch window, so it is the intended tool for confirming real bar-close events and 24h runtime health.

### API Endpoints

**REST:**
- `GET /api/indicators/latest?symbol=BTCUSDT_PERP&tf=4h`
- `GET /api/indicators/history?symbol=BTCUSDT_PERP&tf=4h&from=...&to=...`
- `GET /api/state?symbol=BTCUSDT_PERP&tf=4h`
- `GET /healthz`

**WebSocket:**
- `WS /ws/indicators?symbol=BTCUSDT_PERP&tf=4h`
- `WS /ws` (state stream)

### Viewing Dashboard

The dashboard runs at `http://127.0.0.1:8090` (or whatever port you configured) and shows:
- Current market state (4D classification)
- Active playbook and confidence
- Position status and unrealized PnL
- Exit decision and trailing stop
- Sizing breakdown
- Indicator values with sparklines
- Deribit options thermometer (if enabled)

---

## Development Roadmap

### Completed Phases

- ✅ **Phase 1**: Foundation (replay, indicators, basic strategy)
- ✅ **Phase 2**: Multi-timeframe (1d bias, 4h execution, trailing stops)
- ✅ **Phase 3-6**: 6-layer strategy engine (state, playbook, sizing, exit, risk, execution)

### Current Focus (Q1 2026)

**Code Quality & Validation**
- Build analytics/reporting module
- Extend backtest validation beyond the deterministic synthetic regression
- Research confidence-blending alternatives before changing sizing semantics

### Future Phases (Post-Validation)

**Phase 7: Funding-OI Intelligence**
- Funding rate momentum and acceleration
- OI delta vs price delta divergence
- Crowding-aware positioning context
- Estimated: 2-3 weeks

**Phase 8: Options Integration (Optional)**
- Stage 1: Observation only (dashboard visibility)
- Stage 2: Risk overlay input (volatility regime enhancement)
- Stage 3: Strategy confidence modifier (regime confirmation)
- Condition: Only after Phase 7 validation

**Phase 9: Advanced Execution**
- TWAP/VWAP slicing for large orders
- Execution quality analytics
- Adaptive order routing

**Phase 10: Multi-Portfolio Allocator**
- Cross-strategy risk budgeting
- Correlation-aware allocation
- Portfolio-level optimization

---

## For External Reviewers

### What to Look For

**Architecture Health**
- Clear layer boundaries with event-driven communication
- No circular dependencies between modules
- Explicit contracts for all data structures

**Determinism Verification**
- Run replay twice with same inputs → identical outputs
- Check cutoff_time_ns consistency across decision layers
- Verify no system clock usage in strategy logic

**Quality Flags**
- Indicator quality flags propagate through decision chain
- System reduces risk when quality degrades
- Dashboard shows quality warnings

**Audit Trail**
- PostgreSQL tables capture all decision layers
- Reason codes explain every state transition
- Post-trade analysis possible from audit data

### Known Limitations

1. **Single symbol only**: Multi-symbol support deferred to Phase 10
2. **No ML models**: Rule-based baseline first, ML later
3. **Limited execution logic**: Simple delta-based rebalancing only
4. **No options trading**: Observation only, no options execution
5. **Sizing confidence blend**: Current arithmetic mean is simple and deterministic, but not yet deeply validated

### Questions to Ask

- Does the decision chain make sense for your use case?
- Are the playbook selection rules reasonable?
- Is the sizing logic too conservative or too aggressive?
- Are the exit priorities correct (invalidation > trailing > decay > regime)?
- Is the dashboard useful for monitoring live operation?

---

## For Internal Developers

### Project Structure

```
zenquant/
├── domain/              # Core data models (IndicatorPack, MarketState, etc.)
├── ingestor/            # Market data ingestion (Binance, Deribit)
├── historical/          # Replay pipeline and bar clock
├── strategy/            # 6-layer strategy engine
│   ├── state_engine.py      # 4D market classification
│   ├── playbook_engine.py   # Strategy selection
│   ├── sizing_engine.py     # Position sizing
│   ├── exit_engine.py       # Exit logic
│   └── engine.py            # Main strategy coordinator
├── risk/                # Risk overlay and portfolio constraints
├── execution/           # Order routing and fill tracking
├── infra/               # Event bus, PostgreSQL, logging
├── app/                 # FastAPI server and live runtime
└── web/                 # Vue.js dashboard

tests/                   # Test suite (74 passing, 4 skipped)
migrations/              # PostgreSQL schema
docs/                    # Additional documentation
scripts/                 # Utility scripts (Deribit observation, etc.)
```

### Key Files to Understand

**Strategy Engine**
- `zenquant/strategy/state_engine.py` - 4D market classification logic
- `zenquant/strategy/playbook_engine.py` - Playbook selection rules
- `zenquant/strategy/sizing_engine.py` - Position sizing computation
- `zenquant/strategy/exit_engine.py` - Exit priority logic

**Data Models**
- `zenquant/domain/models.py` - All core data structures
- `zenquant/domain/serde.py` - Serialization utilities

**Infrastructure**
- `zenquant/infra/event_bus.py` - Pub/sub event system
- `zenquant/infra/db.py` - PostgreSQL audit writer

**Live Runtime**
- `zenquant/app/server.py` - FastAPI server and WebSocket
- `zenquant/app/live_state.py` - Live state aggregation
- `zenquant/app/live_validate.py` - Live health monitoring

### Event Subjects (Pub/Sub)

```
Market Data:
  md.trade.{symbol}
  md.book.{symbol}
  md.funding.{symbol}
  md.oi.{symbol}

Bar Close:
  bar.close.{timeframe}.{symbol}

Indicators:
  indicator.{timeframe}.{symbol}

Strategy:
  state.market.{timeframe}.{symbol}
  strategy.playbook.{timeframe}.{symbol}
  strategy.sizing.{timeframe}.{symbol}
  strategy.exit.{timeframe}.{symbol}
  strategy.position_state.{timeframe}.{symbol}

Risk:
  risk.snapshot
  risk.deribit.{symbol}

Execution:
  intent.target_position.{stage}.{timeframe}.{symbol}
  exec.order.report
```

### Adding a New Indicator

1. Add computation logic to `zenquant/historical/indicator_engine.py`
2. Update `IndicatorValues` in `zenquant/domain/models.py`
3. Add quality flag checks if needed
4. Write unit tests in `tests/test_indicator_engine.py`
5. Update dashboard display in `zenquant/web/app/pages/DashboardPage.js`

### Adding a New Playbook

1. Define playbook in `zenquant/strategy/playbook_engine.py`
2. Add selection logic in `PlaybookEngine.select()`
3. Write tests in `tests/test_playbook_engine.py`
4. Update dashboard playbook display

### Adding a New Exit Rule

1. Add check method in `zenquant/strategy/exit_engine.py`
2. Update priority order in `ExitEngine.check()`
3. Add reason codes to `ExitDecision`
4. Write tests in `tests/test_exit_engine.py`
5. Update dashboard exit display

### Running Tests

```bash
# All tests
uv run pytest

# Specific module
uv run pytest tests/test_exit_engine.py -v

# With coverage
uv run pytest --cov=zenquant --cov-report=html

# Run skipped tests (require external systems)
export ZENQUANT_RUN_DERIBIT_NETWORK_TESTS=1
export ZENQUANT_TEST_PG_DSN="postgresql://localhost/zenquant_test"
uv run pytest

# Frontend tests
npm run test:store
npm run test:e2e
npm run test:frontend
```

### Debugging Tips

**Enable debug logging**
```bash
export ZENQUANT_LOG_LEVEL=DEBUG
uv run zenquant-web --host 127.0.0.1 --port 8090 --mode live
```

**Inspect PostgreSQL audit trail**
```sql
-- Latest market states
SELECT * FROM market_states ORDER BY cutoff_time_ns DESC LIMIT 10;

-- Latest exit decisions
SELECT * FROM exit_decisions ORDER BY cutoff_time_ns DESC LIMIT 10;

-- Position state history
SELECT * FROM position_states ORDER BY snapshot_time_ns DESC LIMIT 10;
```

**Check dashboard WebSocket**
```javascript
// Browser console
ws = new WebSocket('ws://localhost:8090/ws/dashboard/BTCUSDT_PERP');
ws.onmessage = (e) => console.log(JSON.parse(e.data));
```

### Code Style

- Use `ruff` for linting: `uv run ruff check .`
- Use `ruff format` for formatting: `uv run ruff format .`
- Type hints required for all public functions
- Docstrings required for all modules and classes
- Keep functions under 50 lines when possible
- Prefer explicit over implicit

### Git Workflow

```bash
# Create feature branch
git checkout -b feature/your-feature-name

# Make changes and test
uv run pytest

# Commit with descriptive message
git commit -m "Add new exit rule for volatility spike"

# Push and create PR
git push origin feature/your-feature-name
```

---

## Design Philosophy

**Build a trading product that is trustworthy before flashy, and modular before overcomplicated.**

Core values:
- Explainability over black-box complexity
- Determinism over stochastic magic
- Auditability over opaque decisions
- Simplicity over premature optimization
- Validation over speculation

---

## Disclaimer

This software is provided for educational and research purposes only. It is not financial advice. Trading cryptocurrencies involves substantial risk of loss. Past performance does not guarantee future results. Use at your own risk.

---

**Last Updated**: 2026-03-16
**Test Status**: 74 passing, 4 skipped
**Documentation**: See `docs/` for additional details
