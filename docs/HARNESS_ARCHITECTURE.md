# zenQuant Harness Engineering Architecture

> Human steers, agent executes. The repo is the system of record.

**Related Documentation**:
- [Validation Results](VALIDATION.md) — Current performance, IC analysis, overfitting assessment
- [Operations](OPERATIONS.md) — Deployment, monitoring, iteration

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

## 3. Clean Tuning Campaign Loop (Authoritative Path)

```
    ┌─────────────────────────────────────────────────────────────┐
    │                Framework Campaign (Authoritative)           │
    │                                                             │
    │   Stage 0: READINESS + SPLIT CONTRACT                       │
    │   │  data_readiness gate                                    │
    │   │  split_contract = btc_temporal_v1                       │
    │   │  train = search only / dev = ranking only               │
    │   │  test = final acceptance only                           │
    │   └───────────────────────────────────────────────────────┐ │
    │            │                                               │ │
    │            v                                               │ │
    │   Stage 1A: OPTUNA TRAIN SEARCH                            │ │
    │   │  Bayesian candidate generation on train only           │ │
    │   │  top-N candidates emitted with split metadata          │ │
    │   └───────────────────────────────────────────────────────┐ │
    │            │                                               │ │
    │            v                                               │ │
    │   Stage 1B: RL TRAIN EXPLORER                              │ │
    │   │  train-only interaction search                         │ │
    │   │  dev shortlist only, not final winner selection        │ │
    │   └───────────────────────────────────────────────────────┐ │
    │            │                                               │ │
    │            v                                               │ │
    │   Stage 1C: DEV RANKING + ROBUSTNESS                       │ │
    │   │  avg_dev_sharpe                                        │ │
    │   │  worst_dev_sharpe                                      │ │
    │   │  avg_dev_ratio                                         │ │
    │   │  drawdown / negative worst-window penalties            │ │
    │   │  AutoResearch workers may assist here                  │ │
    │   └───────────────────────────────────────────────────────┐ │
    │            │ finalists                                      │ │
    │            v                                                │ │
    │   Stage 2: FROZEN TEST VERDICT                             │ │
    │   │  overall_drawdown                                       │ │
    │   │  worst_test_sharpe                                      │ │
    │   │  negative_worst_windows                                 │ │
    │   │  promotion gate = final truth                           │ │
    │   └───────────────────────────────────────────────────────┐ │
    │            │                                               │ │
    │            v                                               │ │
    │   Stage 3: AUTHORITATIVE OUTPUT                            │ │
    │   │  campaign_status.json                                  │ │
    │   │  framework_optimization.json                           │ │
    │   │  framework_optimization.md                             │ │
    │   └───────────────────────────────────────────────────────┘ │
    └─────────────────────────────────────────────────────────────┘
```

Notes:
- `scripts/run_framework_campaign.py` is the only authoritative clean tuning entrypoint.
- `zenquant.research.framework_search` owns the scoring, finalists, and promotion truth.
- `zenquant.research.auto_research` remains available as an internal worker library for train/dev research only.
- `scripts/run_auto_research.py` is internal/dev-only and must not be treated as a final promote path.

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
                                 │   iteration. Framework campaign
                                 │   reuses AutoResearch workers
                                 │   under one authoritative path.
    ─────────────────────────────┼───────────────────────────────────
    GAN-style eval (Anthropic)   │ Candidate search = generator
                                 │ Promotion gate = evaluator
                                 │ Explicit criteria, not vibes
    ─────────────────────────────┼───────────────────────────────────
    Verification loop (LangChain)│ Walk-forward OOS + robustness
                                 │   seeds = mandatory verification
                                 │   before promotion
```
