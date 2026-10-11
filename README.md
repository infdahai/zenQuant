# ZenQuant 4.0

**Deterministic crypto trading infrastructure for building auditable, explainable strategies on major crypto perpetuals.**

ZenQuant delivers:
1. A flagship strategy engine with explainable, replayable, and inspectable decisions
2. A real-time dashboard showing why the system increases risk, reduces risk, or refuses to trade

This is **not** an HFT engine, black-box AI bot, copy-trading platform, or "support every idea" framework. It's a trading product that is trustworthy before flashy, and modular before overcomplicated.

## Quant V2 Research Status

The V2 architecture experiment is implemented behind explicit, default-off
research modes. Its frozen 30-run acceptance result is **blocked**: engineering
purity gates pass, but late-dev remains negative under the additional 15bp cost
stress on all five seeds. Do not promote it or start another tuning round.

- Architecture review: [docs/QUANT_V2_ARCHITECTURE_EXPERT_REVIEW.md](docs/QUANT_V2_ARCHITECTURE_EXPERT_REVIEW.md)
- Reproducible result/runbook: [docs/QUANT_V2_ACCEPTANCE_HANDOFF.md](docs/QUANT_V2_ACCEPTANCE_HANDOFF.md)
- Tester/operator authority: [docs/LIVE_VALIDATION_HANDOFF_SPEC.md](docs/LIVE_VALIDATION_HANDOFF_SPEC.md)

Current implementation, Gate v2 evidence, and the proposed Quant System v2 boundaries are documented in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Rust Options Workbench

The offline options workbench provides a native Rust CLI and a pure
`options-core` library for deterministic input admission, displayed execution
costs, terminal option scenarios and same-venue vertical certificates.

- Architecture, request/report interfaces and CLI usage: [Rust workbench guide](docs/OPTIONS_WORKBENCH_RUST_GUIDE.md)
- Independent quote costs without a View or indicator: [versioned quote diagnostic input](docs/OPTIONS_WORKBENCH_RUST_GUIDE.md#独立报价成本诊断)
- Local integration results and historical input requirements: [gate repair report](docs/reports/options-workbench-gate-repair-20261010/README.md)
- Build with the pinned toolchain: `cd rust && cargo build --workspace --locked`

The local Python harness and native Rust checks pass on the recorded inputs.
The workbench remains an offline diagnostic tool; these checks do not establish
option strategy profitability or authorize live trading.

---

## Quick Start

```bash
# Install dependencies
uv sync

# Run deterministic backtest
uv run zenquant-backtest --root data/raw/binance --symbol BTCUSDT

# Start live paper-trading runtime (default port 8090)
export ZENQUANT_PG_DSN='postgresql://user:pass@host:5432/zenQuant'
uv run zenquant-web --host 127.0.0.1 --port 8090 --mode live

# Run a train/dev framework smoke campaign
uv run python scripts/run_framework_campaign.py --budget smoke --optuna-trials 12 --rl-episodes 0 --search-space-mode intent_churn_ablation_train_only --development-dev-gate --output-dir results/framework_optimization_smoke

# Export a labeled research dataset from live/paper PostgreSQL audit logs
uv run zenquant-live-export --dsn "$ZENQUANT_PG_DSN" --symbol BTCUSDT_PERP --label paper-live

# Run one forward data collection cycle for Deribit + Hyperliquid + merge + readiness
uv run zenquant-forward-collect

# View dashboard
open http://127.0.0.1:8090
```

## Claude CLI Usage Discipline

This repo is large enough that careless Claude CLI usage can waste a lot of tokens without improving output quality.

- Use the repo `.claudesignore` to keep `data/`, `logs/`, `results/`, dependency folders, and compiled assets out of default context.
- Ask Claude about a specific file or subsystem first. Prefer `zenquant/app/check.py` over "scan the whole repo".
- Do not use Claude as a background process monitor. Run long jobs in the terminal, then paste back a short failure snippet if analysis is needed.
- Do not repeatedly ask Claude to poll whether a script has finished. That pattern burns cache reads on large contexts.
- Keep one model per task when possible so cache reads stay reusable instead of forcing expensive rewrites.
- For large outputs, summarize first: share a short `rg`, `git diff --stat`, or trimmed traceback instead of full generated logs.

## RL Research Tuning

`RL` is supported as a research-only parameter tuner. It may tune weights, risk thresholds, indicator windows, and execution speeds, but it must not rewrite strategy architecture, regime logic, or risk precedence.

The current research decision is to freeze the leading candidate and perform
architecture attribution before starting another Optuna or RL campaign. See
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the implemented research
boundary and the A0-A5 target experiment.

## Dataset Split Discipline

ZenQuant now treats temporal split discipline as a hard anti-overfit contract, not a research preference.

- `train`: `2020-01-01` to `2023-12-31`
- `dev`: `2024-01-01` to `2025-12-31`
- `test`: `2026-01-01` to latest available

What each layer is allowed to do:

- `train` learns parameters and produces candidates
- `dev` ranks candidates, runs weak-window checks, and runs robustness selection
- `test` is final historical acceptance only
- `paper-live` starts only after `test` passes and `framework_optimization.json.final_verdict` marks the candidate eligible

Reference: [`docs/DATASET_SPLIT_POLICY.md`](/Users/disaster/workspace/code/zenQuant/docs/DATASET_SPLIT_POLICY.md)

```bash
# Supported RL tuner entrypoint
uv run zenquant-rl-tune --episodes 200 --output results/rl_tuner.json
```

Execution defaults follow the PaulWei pattern:
- long-only DCA on adverse moves
- profitable pyramiding
- gradual exits by default
- ATR distance gating on DCA
- risk overlay always has final authority

## Historical Validation Snapshot

The current BTC strategy remains research-only. The latest bounded Gate v2
rescore evaluated seven frozen candidates across five deterministic perturbation
seeds without opening the frozen test split. No candidate passed the complete
Gate policy; the terminal recommendation is to redesign the risk and execution
boundaries before more training.

- Architecture and interpretation: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)
- Operator contract: [`docs/LIVE_VALIDATION_HANDOFF_SPEC.md`](docs/LIVE_VALIDATION_HANDOFF_SPEC.md)
- Gate v2 summary: [`results/remediation_20260821/gate_v2_rescore/gate_v2_rescore.md`](results/remediation_20260821/gate_v2_rescore/gate_v2_rescore.md)
- Final promotion still requires the strict `train/dev/test` contract and a valid positive promotion verdict.

Minimal re-check commands:

```bash
uv run pytest tests/test_variant_comparison.py tests/test_202308_bearish_grind.py tests/test_historical_pipeline.py -q --tb=short
uv run pytest tests/test_golden_output.py tests/test_backtest_invariants.py tests/test_parity.py tests/test_execution_router.py tests/test_execution_sensitivity.py tests/test_scenario_risk.py -q --tb=short
uv run python scripts/compare_variants.py --root data/raw/binance --symbol BTCUSDT --split dev --output-dir /tmp/zenQuant-variant-compare
uv run python scripts/walk_forward_eval.py
uv run python scripts/adversarial_stress.py --historical --split dev
```

## Forward Data Collection

Forward-looking research inputs are collected by an hourly cron-friendly chain, not by the research loop itself.

- `uv run zenquant-forward-collect`
  - runs Deribit collection, Deribit block flow collection, Hyperliquid collection, liquidation merge, and readiness refresh in order
  - updates `results/data_readiness/forward_collection_latest.json`
- `uv run zenquant-forward-collect --health-only`
  - checks freshness of `_collector_status.json`, Deribit block flow status, merged liquidation output, and `results/data_readiness/data_readiness.json`
  - updates `results/data_readiness/forward_collection_health.json`

Use the collector command on a fixed hourly schedule. Do not treat `zenquant-web --mode live` as the only forward-data collector.

Reference docs:

- [`docs/FORWARD_COLLECTION_AUTOMATION.md`](/Users/disaster/workspace/code/zenQuant/docs/FORWARD_COLLECTION_AUTOMATION.md)
- [`docs/HISTORICAL_EXTENSION_2016.md`](/Users/disaster/workspace/code/zenQuant/docs/HISTORICAL_EXTENSION_2016.md)

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
10. [Training Data Pipelines](#training-data-pipelines)

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
│  • Macro: VIX, DXY, Treasury yields (z-scored)                  │
│  • Options: ATM IV, skew, term structure (z-scored)              │
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

---

## Training Data Pipelines

ZenQuant now supports two deterministic dataset-export paths for tuning and later model training.

### 1. Historical Replay Dataset

Use this when you have Binance Parquet history on disk and want fully reproducible offline research.

```bash
uv run python - <<'PY'
from pathlib import Path

from zenquant.historical import HistoricalBacktestConfig
from zenquant.research import ResearchRunConfig, run_research_pipeline

artifact = run_research_pipeline(
    ResearchRunConfig(
        backtest=HistoricalBacktestConfig(root_dir=Path("data/raw/binance"), symbol="BTCUSDT"),
        run_label="baseline",
    )
)
print(artifact.run_dir)
PY
```

Output directory:
- `results/research_runs/<run_id>/manifest.json`
- `results/research_runs/<run_id>/summary.json`
- `results/research_runs/<run_id>/analysis_rows.jsonl`
- `results/research_runs/<run_id>/analysis_rows.csv`
- `results/research_runs/<run_id>/report.md`

Use this path for:
- backtest-driven parameter research
- walk-forward splits
- deterministic regression baselines

### 2. Live / Paper Audit Dataset

Use this when the system is already running with PostgreSQL audit persistence and you want to learn from real monitored behavior.

```bash
export ZENQUANT_PG_DSN='postgresql://user:pass@host:5432/zenQuant'
uv run zenquant-live-export \
  --dsn "$ZENQUANT_PG_DSN" \
  --symbol BTCUSDT_PERP \
  --label paper-live
```

This path reads the live audit tables and exports one row per stored `4h` cutoff using:
- indicator packs
- market states
- playbooks
- sizing decisions
- exit decisions
- final target intents
- latest risk snapshot at or before cutoff

Forward labels are derived from subsequent stored close prices in `indicator_packs`, so the output stays point-in-time safe.

Use this path for:
- paper-live review
- live/backtest parity checks
- training on observed production behavior

Current repo status:
- paper-live can be started only after the handoff gates pass and `results/framework_optimization/framework_optimization.json.final_verdict` marks the candidate eligible
- paper-live validation is **not yet complete** until the sustained run and formal export/review finish
- use the handoff spec as the single tester/operator contract

See [LIVE_VALIDATION_HANDOFF_SPEC.md](/Users/disaster/workspace/code/zenQuant/docs/LIVE_VALIDATION_HANDOFF_SPEC.md).

### Practical Rule

Use historical replay to search safely. Use live audit exports to verify that the same strategy behavior survives real feeds and real operational noise.

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
Macro: `vix`, `vix_z`, `dxy`, `dxy_z`, `yield_10y`, `yield_10y_z` (from MacroDataStore)
Options: `atm_iv_30d`, `atm_iv_z`, `skew_z`, `term_slope_z` (from OptionsDataStore, pending data)

Quality flags: `OK`, `BOOK_STALE`, `VENUE_DOWN`, `MISSING_OI`, `LOW_OI_COVERAGE`, `DATA_STALE`, `DATA_FUTURE`, `MACRO_STALE`, `OPTIONS_STALE`

### Risk Overlay

The risk overlay can apply the following controls:
- Drawdown step throttles (3-tier)
- Volatility shock throttle
- Crowding discount
- Severe crowding reduce-only behavior
- Daily loss limit safe cap
- Hard kill-switch gating

### Plugin Architecture

Strategy decisions can be composed from modular plugins loaded via TOML profiles:

```
zenquant/plugins/
  signals/    → weighted sum → direction [-1, 1]
  filters/    → AND chain → pass/block
  sizers/     → multiplicative → leverage factor
  exits/      → priority-ordered → first match wins
```

Each plugin is one `.py` file with a `meta` descriptor and a `compute()` pure function.
Configuration in `config/profiles/default.toml`. See `zenquant/plugins/protocols.py`
for the interface contract.

**Directional Signal Inventory** (default profile):

| Signal | Weight | Source | Description |
|--------|--------|--------|-------------|
| `multi_momentum_signal` | 0.45 | Price structure | Multi-horizon momentum ROC |
| `cvd_signal` | 0.25 | CVD slope | Cumulative volume delta direction |
| `liquidity_signal` | 0.20 | Order book | OB imbalance mean-reversion |
| `crowding_signal` | 0.10 | Funding × OI | Contrarian positioning |
| `options_vol_signal` | 0.15 | Deribit surface | IV/skew/term dislocation, auto-neutral without data |
| `perp_carry_stress_signal` | 0.08 | Funding + basis + OI | Carry stress / leverage quality |
| `vol_structure_signal` | 0.08 | Realized vol structure | Vol term structure / persistence |
| `crash_reversal_signal` | 0.25 | Crash response | Extreme downside reversal |
| `funding_alpha_signal` | 0.10 | Funding z-score | Funding reflexivity |
| `macro_regime_signal` | 0.12 | VIX/DXY/10Y | Disabled by default |
| `cross_asset_flow_signal` | 0.10 | Cross-asset beta | Disabled by default |
| `vol_mean_reversion_signal` | 0.20 | Vol spike reversion | Disabled by default |

### Validation Infrastructure

- **Parallel walk-forward**: `ProcessPoolExecutor` (6-8x speedup on M4 Pro)
- **Domain randomization**: Price/volume noise + bar dropout for robustness testing
- **Funding accumulation**: Perpetual contract funding costs tracked in backtest PnL

### Test Coverage

```
223 tests passing, 5 skipped (require live external systems)

Core coverage:
✅ E2E decision chain tests
✅ Live bootstrap and bar-close tests
✅ Exit engine priority logic
✅ Playbook selection (config override, crowded-long fallthrough)
✅ Macro data store (point-in-time, z-scores, staleness)
✅ Options data store (ATM IV, skew, term structure)
✅ Signal plugins (macro regime, options vol, momentum, CVD, liquidity, crowding)
✅ Sizing computation (UNSAFE zero-leverage, config overrides)
✅ State classification
✅ Plugin infrastructure (registry, orchestrator, composition)
✅ Plugin integration (real plugin loading, engine dual-path)
✅ Domain randomization (perturbation determinism, OHLC consistency)
✅ PostgreSQL persistence
✅ Deribit options ingestion
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
git clone https://github.com/infdahai/zenquant.git
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
| `zenquant-live-scorecard` | Persist and review the paper-live operator scorecard |
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
export ZENQUANT_PG_DSN='postgresql://user:pass@host:5432/zenQuant'
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

# 5. Persist the operator scorecard used by the dashboard and weekly review
uv run zenquant-live-scorecard --base-url http://127.0.0.1:8090 --require-db
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
- `GET /api/paper-live/scorecard/latest?symbol=BTCUSDT_PERP`
- `GET /api/paper-live/scorecard/history?limit=50`
- `GET /healthz`

**WebSocket:**
- `WS /ws/indicators?symbol=BTCUSDT_PERP&tf=4h`
- `WS /ws` (state stream)

### Viewing Dashboard

The dashboard runs at `http://127.0.0.1:8090` (or whatever port you configured) and shows:
- Current market state (4D classification)
- Paper-live scorecard, sustained-run status, export/review readiness
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
│   ├── playbook_engine.py   # Strategy selection
│   ├── sizing_engine.py     # Position sizing
│   ├── exit_engine.py       # Exit logic
│   └── engine.py            # Main coordinator (legacy + plugin dual-path)
├── plugins/             # Modular strategy plugins
│   ├── protocols.py         # Plugin interface contracts
│   ├── registry.py          # Discovery, loading, config merge
│   ├── orchestrator.py      # Composition (weighted sum, AND, multiply, priority)
│   ├── signals/             # Directional signal plugins
│   ├── filters/             # Trade gate plugins
│   ├── sizers/              # Position sizing plugins
│   └── exits/               # Exit decision plugins
├── risk/                # Risk overlay and portfolio constraints
├── execution/           # Order routing and fill tracking
├── infra/               # Event bus, PostgreSQL, logging
├── app/                 # FastAPI server and live runtime
└── web/                 # Vue.js dashboard

tests/                   # Test suite (119 passing, 4 skipped)
migrations/              # PostgreSQL schema
docs/                    # Additional documentation
scripts/                 # Utility scripts (Deribit observation, etc.)
```

### Key Files to Understand

**Strategy Engine**
- `zenquant/strategy/engine.py` - Main coordinator with 4D state classification
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
uv run pytest --cov=zenQuant --cov-report=html

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
ws = new WebSocket('ws://localhost:8765/ws');
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

**Last Updated**: 2026-08-22

**Documentation**: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) (current and target architecture), [`docs/OPERATIONS.md`](docs/OPERATIONS.md) (deployment and monitoring), [`docs/LIVE_VALIDATION_HANDOFF_SPEC.md`](docs/LIVE_VALIDATION_HANDOFF_SPEC.md) (tester/operator contract)
