# zenQuant Harness Engineering Architecture

> Human steers, agent executes. The repo is the system of record.

## 1. High-Level Harness Topology

```
                         zenQuant Harness Architecture
    =====================================================================

    HUMAN (Product Owner)
      |
      |  intent / constraints / taste
      v
    +---------------------------------------------------------------+
    |                    REPO = SYSTEM OF RECORD                    |
    |                                                               |
    |  AGENTS.md ──> docs/ ──> zenquant/ ──> config/ ──> tests/    |
    |  (map)        (deep     (code)       (behavior)  (evidence)  |
    |               docs)                                           |
    +---------------------------------------------------------------+
      |                    |                      |
      v                    v                      v
    GATE CHAIN          STRATEGY ENGINE        AUTORESEARCH
    (mechanical)        (decision chain)       (campaign loop)
      |                    |                      |
      v                    v                      v
    +--------+    +----------------+    +-------------------+
    | Gate A |    | IndicatorPack  |    | Sweep (parallel)  |
    | ruff   |    |     |          |    |     |              |
    +--------+    |  MarketState   |    | Walk-Forward      |
    +--------+    |     |          |    |     |              |
    | Gate B |    |  Playbook      |    | Robustness (5x)   |
    | import |    |     |          |    |     |              |
    | bounds |    |  Sizing        |    | Promotion Gate    |
    +--------+    |     |          |    |     |              |
    +--------+    |  Exit          |    | PROMOTE / REJECT  |
    | Gate C |    |     |          |    +-------------------+
    | golden |    |  Risk Overlay  |
    | output |    |     |          |
    +--------+    | TargetPosition |
    +--------+    +----------------+
    | Gate D |         |    |
    | parity |    legacy    plugin
    | test   |    path      path
    +--------+    (must agree)
    +--------+
    | Gate E |
    | pytest |
    | 223+   |
    +--------+
         |
         v
    ALL GATES PASS? ──no──> BLOCK (agent fixes or human reviews)
         |
        yes
         v
    MERGE / DEPLOY
```

## 2. Gate Chain Detail

```
    zenquant-check  (one command, all gates)
    ════════════════════════════════════════

    Gate 0: FACTOR HEALTH (Verification Agent pattern)
    ┌─────────────────────────────────────────────────┐
    │  IC stability: no sign flip between data halves │
    │  Sprint contracts: min 2 active signals         │
    │  Walk-forward stability on synthetic data       │
    │  Data split config: temporal ordering valid      │
    │  Disabled signals: no high-weight dead weight    │
    └────────────────────┬────────────────────────────┘
              PASS       │       FAIL → flag signal for review
              v

    Gate A: FORMAT + LINT
    ┌─────────────────────────────────────────────────┐
    │  ruff check zenquant/ tests/ scripts/           │
    │  ruff format --check zenquant/ tests/ scripts/  │
    │                                                 │
    │  WHY: Documents rot. Lint rules don't.          │
    │  COST: <2 seconds                               │
    └─────────────────────────────────────────────────┘
              │ pass
              v
    Gate B: IMPORT BOUNDARIES
    ┌─────────────────────────────────────────────────┐
    │  zenquant/plugins/**  must NOT import from:     │
    │    zenquant.domain.*  (only protocols.py)       │
    │    zenquant.strategy.*                          │
    │    zenquant.infra.*                             │
    │                                                 │
    │  zenquant/strategy/** must NOT import from:     │
    │    zenquant.app.*                               │
    │    zenquant.execution.*                         │
    │                                                 │
    │  WHY: Architectural decay is entropy.           │
    │  COST: <1 second (AST scan)                     │
    └─────────────────────────────────────────────────┘
              │ pass
              v
    Gate C: GOLDEN OUTPUT
    ┌─────────────────────────────────────────────────┐
    │  Synthetic 90-day dataset + default params      │
    │      → backtest                                 │
    │      → metrics JSON                             │
    │      → diff vs tests/golden/backtest.json       │
    │                                                 │
    │  ANY delta = explicit approval required.        │
    │  Formula hash tracks indicator changes.         │
    │                                                 │
    │  WHY: "It ran once" is not validation.          │
    │  COST: ~3 seconds                               │
    └─────────────────────────────────────────────────┘
              │ pass
              v
    Gate D: PLUGIN / LEGACY PARITY
    ┌─────────────────────────────────────────────────┐
    │  Same IndicatorPack + MarketState               │
    │      → legacy path  → TargetPosition A          │
    │      → plugin path  → TargetPosition B          │
    │      → assert consistent (or declared delta)    │
    │                                                 │
    │  WHY: Two paths must not silently diverge.      │
    │  COST: <1 second                                │
    └─────────────────────────────────────────────────┘
              │ pass
              v
    Gate E: FULL TEST SUITE
    ┌─────────────────────────────────────────────────┐
    │  uv run pytest --tb=short -q                    │
    │  259+ tests, 5 skipped (external deps)           │
    │                                                 │
    │  Covers: e2e chain, exits, playbooks, sizing,   │
    │  state, plugins, perturbation, risk guard,      │
    │  execution pyramid, basis store, 1H execution   │
    │                                                 │
    │  WHY: Evidence, not vibes.                      │
    │  COST: ~5 seconds                               │
    └────────────────────┬────────────────────────────┘
              PASS       │       FAIL → block merge
              v

    Gate F: WALK-FORWARD OVERFIT DETECTION
    ┌─────────────────────────────────────────────────┐
    │  8 rolling windows (2yr train → 6mo test)       │
    │  Checks: all windows positive Sharpe            │
    │  Detects: OVERFIT (train>1.0, test<0)           │
    │                                                 │
    │  WHY: Independent OOS evaluator (GAN principle) │
    │  COST: ~30 seconds                              │
    └─────────────────────────────────────────────────┘

    + ADVERSARIAL STRESS (3 GAN-style scenarios)
    + SIGNAL ABLATION (periodic load-bearing analysis)
    + SECRET BENCHMARK (quarterly 2026+ OOS, GitHub Action)
    + ANTI-SNOOPING VALIDATOR (static code check)
```

## 3. AutoResearch Campaign Loop (Harness for Optimization)

```
    ┌──────────────────────────────────────────────────────────┐
    │              AutoResearch Campaign                        │
    │                                                          │
    │   SearchSpace                                            │
    │   ┌─────────────────────────────────────┐                │
    │   │ strategy_params:                    │                │
    │   │   bull_leverage: [2.5, 3.0, 3.5]    │                │
    │   │   preserve_capital: [0.55..0.70]    │                │
    │   │   exit_transition: [0.80..0.90]     │                │
    │   │ plugin_params:                      │                │
    │   │   momentum_threshold: [0.2..0.4]    │                │
    │   └─────────────────────────────────────┘                │
    │          │                                                │
    │          v                                                │
    │   Stage 1: SWEEP  ──────────────────────────────────┐    │
    │   │  N param combos × parallel backtests            │    │
    │   │  ProcessPoolExecutor (cpu_count - 2)            │    │
    │   │  ~3s per 539-day replay                         │    │
    │   │  Record to ExperimentTracker                    │    │
    │   │  Rank by primary_metric (Sharpe)                │    │
    │   └─────────────────────────────────────────────────┘    │
    │          │ top-N                                          │
    │          v                                                │
    │   Stage 2: WALK-FORWARD  ───────────────────────────┐    │
    │   │  3 splits × 90-day train + 90-day OOS           │    │
    │   │  Validate on unseen data                        │    │
    │   │  Collect: avg_val_sharpe, avg_val_drawdown       │    │
    │   └─────────────────────────────────────────────────┘    │
    │          │ candidates                                     │
    │          v                                                │
    │   Stage 3: ROBUSTNESS  ─────────────────────────────┐    │
    │   │  5 seeds × domain randomization                 │    │
    │   │  price_noise ±0.1%, volume_noise ±5%            │    │
    │   │  bar_dropout 2%                                 │    │
    │   │  Must pass 4/5 seeds                            │    │
    │   └─────────────────────────────────────────────────┘    │
    │          │ survivors                                      │
    │          v                                                │
    │   Stage 4: PROMOTION GATE  ─────────────────────────┐    │
    │   │                                                 │    │
    │   │  ┌─ Walk-Forward Gate ────────────────────┐     │    │
    │   │  │  avg_val_sharpe > 0.5                  │     │    │
    │   │  │  sharpe_delta > -0.5 vs baseline       │     │    │
    │   │  │  avg_val_drawdown < 20%                │     │    │
    │   │  │  trade_count >= 5                      │     │    │
    │   │  └────────────────────────────────────────┘     │    │
    │   │  ┌─ Robustness Gate ──────────────────────┐     │    │
    │   │  │  4/5 seeds with sharpe > floor         │     │    │
    │   │  │  max degradation < 0.50                │     │    │
    │   │  └────────────────────────────────────────┘     │    │
    │   │  ┌─ Drawdown Regression Gate ─────────────┐     │    │
    │   │  │  regression < 5pp vs baseline          │     │    │
    │   │  └────────────────────────────────────────┘     │    │
    │   │                                                 │    │
    │   │  ALL pass → PROMOTE                             │    │
    │   │  WF fail  → REJECT                              │    │
    │   │  Other    → REVIEW (human decides)              │    │
    │   └─────────────────────────────────────────────────┘    │
    │          │                                                │
    │          v                                                │
    │   Output: campaign_report.json + experiments.json        │
    └──────────────────────────────────────────────────────────┘
```

## 4. Decision Chain (Strategy Harness)

```
    4h Bar Close Event
    ═══════════════════

         ┌─────────────┐
         │  Candle OHLCV │
         │  Funding Rate │
         │  Open Interest│
         └──────┬────────┘
                │
                v
    ┌───────────────────────┐
    │   Indicator Engine    │  realized_vol, atr, cvd_slope,
    │   (deterministic)     │  ob_liq_z, funding_oi_z, zen_pulse,
    │                       │  momentum_roc_z, structure_state,
    │   formula_hash: abc12 │  multi_momentum_z
    └───────────┬───────────┘
                │ IndicatorPack
                v
    ┌───────────────────────┐
    │   State Engine        │  4D classification:
    │   (hysteresis)        │  directional × volatility ×
    │                       │  positioning × quality
    │                       │  → regime + transition_risk
    └───────────┬───────────┘
                │ MarketState
                v
    ┌───────────────────────┐
    │   Playbook Engine     │  BULL_CONTINUATION
    │   (regime → playbook) │  BEAR_DEFENSE
    │                       │  RANGE_LIGHT
    │                       │  CAPITAL_PRESERVATION
    └───────────┬───────────┘
                │ PlaybookSelection
                │
        ┌───────┴────────┐
        │                │
    LEGACY PATH     PLUGIN PATH
        │                │
        v                v
    ┌─────────┐   ┌──────────────┐
    │_direction│   │compose_signals│  weighted sum
    │(hardcode)│   │compose_filters│  AND chain
    └────┬────┘   └──────┬───────┘
         │               │
         v               v
    ┌─────────┐   ┌──────────────┐
    │ Sizing  │   │compose_sizers │  multiplicative
    │ Engine  │   │+ exec_haircut │
    └────┬────┘   └──────┬───────┘
         │               │
         v               v
    ┌─────────┐   ┌──────────────┐
    │  Exit   │   │compose_exits  │  priority-ordered
    │ Engine  │   │+ trail ratchet│  (engine handles
    │(ratchet)│   │  (engine)     │   state mutation)
    └────┬────┘   └──────┬───────┘
         │               │
         └───────┬───────┘
                 │
                 v
    ┌───────────────────────┐
    │    Risk Overlay       │  drawdown throttle
    │    (can rewrite)      │  vol shock throttle
    │                       │  crowding discount
    │                       │  kill switch
    └───────────┬───────────┘
                │
                v
    ┌───────────────────────┐
    │   TargetPosition      │  intent, not order
    │   (auditable)         │  → Execution Kernel
    └───────────────────────┘
```

## 5. Module Boundary Map (Import Rules)

```
    Module Dependency Rules (Gate B enforcement)
    ══════════════════════════════════════════════

    zenquant/
    ├── domain/          ← FOUNDATION (no internal imports)
    │   └── *.py            pure dataclasses, enums, types
    │
    ├── infra/           ← FOUNDATION (imports domain only)
    │   └── event_bus.py    InMemoryJetStream, StreamConfig
    │
    ├── indicator/       ← imports: domain, infra
    │
    ├── state/           ← imports: domain, infra
    │
    ├── plugins/         ← imports: protocols.py ONLY
    │   ├── protocols.py    PluginMeta, *Result types
    │   ├── registry.py     imports: protocols (+ importlib)
    │   ├── orchestrator.py imports: protocols, registry
    │   ├── signals/        imports: protocols ONLY
    │   ├── filters/        imports: protocols ONLY
    │   ├── sizers/         imports: protocols ONLY
    │   └── exits/          imports: protocols ONLY
    │
    ├── strategy/        ← imports: domain, infra, plugins
    │   │                   MUST NOT import: app, execution
    │   ├── engine.py
    │   ├── playbook_engine.py
    │   ├── sizing_engine.py
    │   └── exit_engine.py
    │
    ├── risk/            ← imports: domain, infra
    │
    ├── execution/       ← imports: domain, infra, risk
    │
    ├── research/        ← imports: domain, historical, strategy
    │   ├── auto_research.py
    │   ├── experiment.py
    │   ├── promoter.py
    │   └── pipeline.py
    │
    ├── historical/      ← imports: domain, infra, strategy, ...
    │
    └── app/             ← TOP LEVEL (imports everything)
        ├── server.py
        └── backtest.py

    FORBIDDEN EDGES (Gate B checks these):
    ✗  plugins/signals/* → domain.*
    ✗  plugins/filters/* → domain.*
    ✗  plugins/sizers/*  → domain.*
    ✗  plugins/exits/*   → domain.*
    ✗  strategy/*        → app.*
    ✗  strategy/*        → execution.*
    ✗  indicator/*       → strategy.*
    ✗  state/*           → strategy.*
```

## 6. Harness Engineering Principles Applied

```
    OpenAI Principle             │ zenQuant Implementation
    ─────────────────────────────┼───────────────────────────────────
    Repo = system of record      │ AGENTS.md (557 lines) + docs/
                                 │ config/profiles/*.toml
                                 │ No hidden knowledge in chat
    ─────────────────────────────┼───────────────────────────────────
    AGENTS.md = map not manual   │ ~100-line entry point
                                 │ links to docs/OPERATIONS.md,
                                 │ docs/VALIDATION.md, etc.
    ─────────────────────────────┼───────────────────────────────────
    Mechanical enforcement       │ Gate A-E chain
                                 │ zenquant-check runs all
                                 │ CI blocks on failure
    ─────────────────────────────┼───────────────────────────────────
    Agent readability            │ Frozen dataclasses (immutable)
                                 │ Pure plugin functions
                                 │ No ORM, no framework magic
                                 │ Explicit event bus (not DI)
    ─────────────────────────────┼───────────────────────────────────
    Entropy management           │ formula_hash detects drift
                                 │ Golden output catches regression
                                 │ Plugin/legacy parity prevents
                                 │   silent behavior divergence
    ─────────────────────────────┼───────────────────────────────────
    Throughput-first merging      │ ~3s backtest enables rapid
                                 │   iteration. AutoResearch runs
                                 │   27-243 combos in minutes.
    ─────────────────────────────┼───────────────────────────────────
    GAN-style eval (Anthropic)   │ AutoResearch: sweep = generator
                                 │ PromotionGate = evaluator
                                 │ Explicit criteria, not vibes
    ─────────────────────────────┼───────────────────────────────────
    Verification loop (LangChain)│ Walk-forward OOS + robustness
                                 │   seeds = mandatory verification
                                 │   before promotion
```
