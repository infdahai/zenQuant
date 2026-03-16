# Strategy Optimization Pipeline Architecture

**Date**: 2026-03-17
**Status**: Design Document (V1 tools implemented, V2 pipeline planned)

---

## Current State (V1 - Implemented)

### Tools Available Now

1. **Parameter Sweep** (`scripts/param_sweep.py`)
   - Cartesian product of parameter ranges
   - Runs multiple backtests with different configs
   - Saves results to JSON

2. **Result Comparison** (`scripts/compare_results.py`)
   - Ranks by different metrics (Sharpe, PnL, drawdown)
   - Parameter sensitivity analysis
   - CSV export for further analysis

3. **Historical Replay** (`zenquant/historical/replay.py`)
   - Now supports `strategy_config` override
   - Deterministic backtest with production code
   - Full audit trail (indicators, states, playbooks, sizing, exits)

### Usage Example

```bash
# 1. Download Binance historical data (3 months)
# https://data.binance.vision/?prefix=data/futures/um/daily/klines/BTCUSDT/

# 2. Run quick parameter sweep (3-5 combinations, ~5 minutes)
uv run python scripts/param_sweep.py --quick

# 3. Compare results
uv run python scripts/compare_results.py results/param_sweep.json

# 4. Run full sweep (20-50 combinations, ~30 minutes)
uv run python scripts/param_sweep.py --full --output results/full_sweep.json

# 5. Analyze sensitivity
uv run python scripts/compare_results.py results/full_sweep.json --sensitivity

# 6. Export for further analysis
uv run python scripts/compare_results.py results/full_sweep.json --export results/analysis.csv
```

---

## Future Pipeline (V2 - Planned)

### Design Principles

1. **Observe → Label → Diagnose → Tune → Validate → Promote**
   - Not "train model → deploy model"
   - Keep strategy deterministic and explainable
   - Use ML for narrow tasks only (confidence calibration, anomaly detection)

2. **Data-Driven, Not Black-Box**
   - All decisions backed by audit trail
   - Parameter changes require validation evidence
   - No silent optimization loops

3. **Walk-Forward Validation**
   - No single-window optimization
   - Train on period N, validate on period N+1
   - Require out-of-sample improvement

4. **Gradual Promotion**
   - Paper-live first
   - Tiny-capital shadow
   - Gradual expansion

### Architecture Layers

```
┌─────────────────────────────────────────────────────────────────┐
│  Data Ingestion Layer                                           │
│  • Live audit logs (PostgreSQL)                                 │
│  • Historical replay logs                                       │
│  • Paper-live logs                                              │
│  • Run metadata (git commit, config, start/end time)            │
└────────────────────────┬────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│  Feature Engineering Layer                                      │
│  • Normalize to (symbol, timeframe, cutoff_time_ns) granularity│
│  • Join: indicators + state + playbook + sizing + exit + risk  │
│  • Add derived features (regime persistence, vol clustering)    │
└────────────────────────┬────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│  Outcome Labeling Layer                                         │
│  • Forward returns (1/3/6 bars ahead)                           │
│  • Max adverse excursion (MAE)                                  │
│  • Max favorable excursion (MFE)                                │
│  • Whether trailing stop hit                                    │
│  • Whether time decay fired                                     │
│  • Whether target was profitable after fees                     │
└────────────────────────┬────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│  Diagnostic Layer                                               │
│  • Profit factor by playbook                                    │
│  • Hit rate by exit type                                        │
│  • Drawdown by regime                                           │
│  • Turnover / fee drag by regime                                │
│  • False-positive rates for state transitions                   │
│  • Target-to-fill slippage realism                              │
└────────────────────────┬────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│  Tuning Layer                                                   │
│  • Walk-forward parameter optimization                          │
│  • Constraint: only tune explicit parameters                    │
│  • Constraint: no worse operational complexity                  │
│  • Constraint: no worse auditability                            │
│  • Constraint: clear reason for promotion                       │
└────────────────────────┬────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│  Validation Layer                                               │
│  • Replay on holdout windows                                    │
│  • Compare against current production baseline                  │
│  • Require no major drawdown regression                         │
│  • Require operational stability                                │
└────────────────────────┬────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│  Promotion Layer                                                │
│  • Paper-live first (1-2 weeks)                                 │
│  • Tiny-capital shadow (1-2 weeks)                              │
│  • Gradual expansion (monitor closely)                          │
│  • Rollback plan if metrics degrade                             │
└─────────────────────────────────────────────────────────────────┘
```

### Database Schema (V2)

#### Run Registry Table

```sql
CREATE TABLE optimization_runs (
    run_id BIGSERIAL PRIMARY KEY,
    git_commit TEXT NOT NULL,
    config_blob JSONB NOT NULL,
    start_time_ns BIGINT NOT NULL,
    end_time_ns BIGINT NOT NULL,
    mode TEXT NOT NULL,  -- 'backtest', 'paper-live', 'live'
    symbol TEXT NOT NULL,
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_optimization_runs_git_commit ON optimization_runs (git_commit);
CREATE INDEX idx_optimization_runs_start_time ON optimization_runs (start_time_ns DESC);
```

#### Feature Table

```sql
CREATE TABLE feature_snapshots (
    id BIGSERIAL PRIMARY KEY,
    run_id BIGINT REFERENCES optimization_runs(run_id),
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    cutoff_time_ns BIGINT NOT NULL,

    -- Indicators
    realized_vol DOUBLE PRECISION,
    cvd_slope DOUBLE PRECISION,
    funding_oi_z DOUBLE PRECISION,
    zen_pulse DOUBLE PRECISION,
    structure_state TEXT,
    momentum_roc_z DOUBLE PRECISION,

    -- State
    regime TEXT,
    regime_confidence DOUBLE PRECISION,
    transition_risk DOUBLE PRECISION,
    quality_state TEXT,

    -- Playbook
    playbook_id TEXT,
    playbook_confidence DOUBLE PRECISION,
    max_leverage DOUBLE PRECISION,

    -- Sizing
    final_leverage DOUBLE PRECISION,
    confidence_multiplier DOUBLE PRECISION,
    stress_discount DOUBLE PRECISION,

    -- Exit
    exit_type TEXT,
    exit_urgency TEXT,

    -- Outcome labels (filled post-hoc)
    forward_return_1bar DOUBLE PRECISION,
    forward_return_3bar DOUBLE PRECISION,
    forward_return_6bar DOUBLE PRECISION,
    max_adverse_excursion DOUBLE PRECISION,
    max_favorable_excursion DOUBLE PRECISION,
    trailing_stop_hit BOOLEAN,
    time_decay_fired BOOLEAN,
    target_profitable BOOLEAN,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_feature_snapshots_run_cutoff
    ON feature_snapshots (run_id, cutoff_time_ns DESC);
```

#### Diagnostic Reports Table

```sql
CREATE TABLE diagnostic_reports (
    id BIGSERIAL PRIMARY KEY,
    run_id BIGINT REFERENCES optimization_runs(run_id),
    report_type TEXT NOT NULL,  -- 'playbook_pf', 'exit_hit_rate', 'regime_dd', etc.
    metric_name TEXT NOT NULL,
    metric_value DOUBLE PRECISION NOT NULL,
    breakdown JSONB,  -- Detailed breakdown by category
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_diagnostic_reports_run_type
    ON diagnostic_reports (run_id, report_type);
```

### Implementation Phases

#### Phase V2.1: Data Pipeline (2-3 days)

**Goal**: Export audit logs to feature table

**Tasks**:
1. Create `optimization_runs` and `feature_snapshots` tables
2. Write SQL export job: PostgreSQL → feature table
3. Add outcome labeling logic (forward returns, MAE, MFE)
4. Test on historical replay data

**Deliverable**: `scripts/export_features.py`

#### Phase V2.2: Diagnostic Reports (2-3 days)

**Goal**: Automated performance analysis

**Tasks**:
1. Create `diagnostic_reports` table
2. Write diagnostic job: feature table → reports
3. Implement key metrics:
   - Profit factor by playbook
   - Hit rate by exit type
   - Drawdown by regime
   - Fee drag analysis
4. Generate daily HTML report

**Deliverable**: `scripts/generate_diagnostics.py`

#### Phase V2.3: Walk-Forward Tuner (3-5 days)

**Goal**: Systematic parameter optimization

**Tasks**:
1. Implement walk-forward validation framework
2. Add parameter grid search with constraints
3. Add out-of-sample validation
4. Add promotion criteria checks
5. Generate tuning report with recommendations

**Deliverable**: `scripts/walk_forward_tune.py`

#### Phase V2.4: ML Enhancements (Optional, 5-7 days)

**Goal**: Narrow ML tasks for confidence calibration

**Tasks**:
1. Train confidence calibration model (regime_confidence → actual forward return)
2. Train transition risk estimator (features → transition probability)
3. Train anomaly detector (flag unusual market conditions)
4. Integrate as optional enhancement (not core strategy)

**Deliverable**: `zenquant/ml/` module

---

## Iteration Workflow (V2)

### Weekly Cycle

**Monday**: Review last week's paper-live data
1. Export features: `uv run python scripts/export_features.py --run-id <last_week>`
2. Generate diagnostics: `uv run python scripts/generate_diagnostics.py --run-id <last_week>`
3. Review HTML report: `open results/diagnostics_<last_week>.html`

**Tuesday-Wednesday**: Parameter tuning
1. Run walk-forward tuner: `uv run python scripts/walk_forward_tune.py --train-start <date> --train-end <date>`
2. Review tuning report: `open results/tuning_report_<date>.html`
3. Decide on parameter changes (if any)

**Thursday**: Validation
1. Run replay with new parameters: `uv run python scripts/param_sweep.py --config <new_params>`
2. Compare against baseline: `uv run python scripts/compare_results.py <new_results> --baseline <baseline>`
3. Check promotion criteria (no worse drawdown, clear improvement)

**Friday**: Promotion decision
1. If validation passes: update default config, deploy to paper-live
2. If validation fails: document why, iterate next week
3. Update run registry with decision and notes

### Monthly Review

1. Analyze 4 weeks of paper-live data
2. Identify systematic issues (if any)
3. Consider architectural changes (if needed)
4. Update optimization pipeline (if needed)

---

## ML Usage Guidelines

### Use ML For (Narrow Tasks)

1. **Confidence Calibration**
   - Input: regime_confidence, playbook_confidence
   - Output: calibrated probability of forward return > 0
   - Why: Improve sizing multiplier accuracy

2. **Transition Risk Estimation**
   - Input: recent state history, volatility, volume
   - Output: probability of regime transition in next N bars
   - Why: Improve exit timing

3. **Anomaly Detection**
   - Input: indicator values, market microstructure
   - Output: anomaly score (0-1)
   - Why: Flag unusual conditions for quality flags

4. **Parameter Ranking**
   - Input: parameter combinations, historical performance
   - Output: ranked list of promising parameter sets
   - Why: Reduce search space for walk-forward tuning

### Do NOT Use ML For

1. **Primary Direction Signal**
   - Keep structure + momentum as core
   - ML cannot explain "why" in audit trail

2. **Playbook Selection**
   - Keep rule-based regime classification
   - Explainability is critical for operators

3. **Exit Logic**
   - Keep priority-based exit rules
   - Operators need to understand exit reasons

4. **Black-Box Strategy**
   - Never replace deterministic stack with neural net
   - Auditability and explainability are non-negotiable

---

## Success Metrics

### V1 (Current Tools)

- ✅ Can run parameter sweep in <30 minutes
- ✅ Can compare results and identify best parameters
- ✅ Can export to CSV for further analysis
- ✅ Can iterate on parameters before paper-live

### V2 (Future Pipeline)

- [ ] Can export paper-live data to feature table daily
- [ ] Can generate diagnostic reports automatically
- [ ] Can run walk-forward tuning weekly
- [ ] Can validate new parameters against baseline
- [ ] Can promote to paper-live with clear evidence
- [ ] Can rollback if metrics degrade

### V3 (ML Enhancements)

- [ ] Confidence calibration improves sizing accuracy
- [ ] Transition risk estimation improves exit timing
- [ ] Anomaly detection reduces false positives
- [ ] Parameter ranking reduces tuning time

---

## Next Steps (Immediate)

1. **Today**: Download 3 months Binance historical data
2. **Tomorrow**: Run quick sweep, identify best parameters
3. **Day 3**: Update default config, start paper-live with optimized parameters
4. **Week 1**: Collect paper-live data, prepare for V2.1 (data pipeline)

**After 1-2 weeks of paper-live**: Implement V2.1 (data pipeline) to enable continuous optimization.

---

## Architecture Review Checklist

Before implementing V2 pipeline:

- [ ] V1 tools validated on historical data
- [ ] Paper-live running stably for 1-2 weeks
- [ ] PostgreSQL audit trail complete and queryable
- [ ] Diagnostic needs identified from paper-live observation
- [ ] Walk-forward validation strategy defined
- [ ] Promotion criteria agreed upon
- [ ] Rollback plan documented

---

**Last Updated**: 2026-03-17
**Next Review**: After 1-2 weeks of paper-live validation
