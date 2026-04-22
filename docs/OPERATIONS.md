# Operations Guide

How to optimize, deploy, monitor, and iterate on the strategy.

---

## 1. Historical Optimization

### Clean Tuning Campaign

```bash
# Download data (540 days default)
uv run python scripts/download_binance_data.py

# Convert to Parquet
uv run python scripts/convert_csv_to_parquet.py

# Check data readiness first
uv run python scripts/data_readiness_status.py

# Start the authoritative clean tuning campaign
uv run python scripts/run_framework_campaign.py \
  --root data/raw/binance \
  --symbol BTCUSDT \
  --liquidation-dir data/raw/merged_liquidations/aggregated \
  --output-dir results/framework_optimization \
  --budget fast
```

Authority rules:
- `scripts/run_framework_campaign.py` is the only authoritative clean tuning entrypoint.
- `scripts/run_auto_research.py` is internal/dev-only and may help generate train/dev research artifacts, but it is not a final promote path.
- `test` is reserved for the framework campaign's final verdict stage and must not participate in search or ranking.

### Campaign Outputs

```bash
# Running truth
cat results/framework_optimization/campaign_status.json

# Final report (only after stage3_complete)
cat results/framework_optimization/framework_optimization.json
```

Important:
- if a prior campaign was interrupted or invalidated, fresh runs archive old checkpoints/results before restarting
- do not treat a stale `campaign_status.json` with `status=running` as evidence of an active job unless a live process actually exists
- only `framework_optimization.json/.md` count as final truth, and only after `campaign_status.json` reaches `stage3_complete`

### Domain Randomization (Robustness Test)

```python
from zenquant.historical import HistoricalBacktest, HistoricalBacktestConfig, PerturbationConfig

config = HistoricalBacktestConfig(
    perturbation=PerturbationConfig(
        seed=42,
        price_noise_pct=0.001,   # ±0.1% price noise
        volume_noise_pct=0.05,   # ±5% volume noise
        bar_dropout_pct=0.02,    # 2% bars dropped
    )
)
result = HistoricalBacktest(config).run()
```

Strategy must pass validation on **5 different seeds** to be considered robust.

---

## 2. Paper-Live Deployment

### Setup

```bash
# Create database
createdb zenquant_live

# Start paper-live runtime
export ZENQUANT_PG_DSN="postgresql://localhost/zenquant_live"
uv run zenquant-web --host 127.0.0.1 --port 8090 --mode live

# Verify health
curl http://127.0.0.1:8090/healthz
uv run zenquant-live-validate --base-url http://127.0.0.1:8090 --require-db
```

Important:
- running the service once does **not** mean paper trading is complete
- paper trading is complete only after a sustained `2 weeks` run and at least one formal `paper-live` export/review
- current repo evidence should be treated as partial progress, not final validation
- tester/operator handoff is defined only by [`LIVE_VALIDATION_HANDOFF_SPEC.md`](/Users/disaster/workspace/code/zenQuant/docs/LIVE_VALIDATION_HANDOFF_SPEC.md)
- do not treat `campaign_status=completed` as paper-live eligibility
- only [`results/framework_optimization/framework_optimization.json`](/Users/disaster/workspace/code/zenQuant/results/framework_optimization/framework_optimization.json) `final_verdict` can mark a candidate `eligible_for_paper_live`

### Dashboard

Open `http://127.0.0.1:8090`. Key panels:
- **Market State**: 4D classification (directional, volatility, positioning, quality)
- **Playbook**: Active strategy and confidence level
- **Position**: Direction, leverage, trailing stop
- **Exit Decision**: Current exit type and urgency

### Daily Monitoring Checklist

| Check | How | Healthy |
|-------|-----|---------|
| System running | `curl /healthz` | `status: ok` |
| Data fresh | Dashboard last_price timestamp | < 5 min old |
| Bar close firing | Check indicator timestamp | Updates every 4h |
| PostgreSQL writing | `SELECT count(*) FROM market_states` | Grows each 4h |
| No quality degradation | Dashboard quality flags | Shows `OK` |

### Weekly Review

```sql
-- Decision quality: playbook distribution
SELECT playbook_id, count(*) FROM playbook_selections
WHERE cutoff_time_ns > extract(epoch from now() - interval '7 days') * 1e9
GROUP BY playbook_id;

-- Exit effectiveness
SELECT exit_type, count(*) FROM exit_decisions
WHERE exit_type != 'NONE'
AND cutoff_time_ns > extract(epoch from now() - interval '7 days') * 1e9
GROUP BY exit_type;

-- Risk events
SELECT flags, count(*) FROM risk_snapshots
WHERE ts_ns > extract(epoch from now() - interval '7 days') * 1e9
AND array_length(flags, 1) > 0
GROUP BY flags;
```

Completion evidence should include:
- continuous runtime window
- successful `paper-live` export
- parity / risk / execution review
- health and persistence evidence

---

## 3. Weekly Optimization Cycle

```
Monday:    Export live audit data → research dataset
Tuesday:   Run walk-forward on expanded dataset
Wednesday: Compare live vs backtest performance
Thursday:  If improved → update TOML profile (config/profiles/)
Friday:    Deploy updated profile, monitor first bar close
```

### Export Live Data for Research

```bash
uv run zenquant-live-export \
  --dsn "$ZENQUANT_PG_DSN" \
  --symbol BTCUSDT_PERP \
  --label weekly-review
```

---

## 4. Plugin Profile Management

Strategy behavior is controlled via TOML profiles in `config/profiles/`:

```bash
# Default balanced strategy
config/profiles/default.toml

# Switch to aggressive (higher momentum weight, wider stops)
# by modifying the TOML or creating a new profile
```

Key tunable parameters in the TOML:
- `signals.*.weight` — signal combination weights
- `sizers.*.boost_max` — conviction boost ceiling
- `exits.trailing_stop_exit.atr_mult` — stop distance
- `exits.regime_change_exit.transition_risk_threshold` — exit sensitivity

---

## 5. Rollback Procedure

If live performance degrades:

1. **Immediate**: Switch to conservative profile or disable plugin path
2. **Investigate**: Compare live audit trail vs historical backtest
3. **Revert**: `git checkout` previous known-good TOML profile
4. **Re-validate**: Run walk-forward on latest data before re-deploying

Trigger conditions for rollback:
- Max drawdown > 15% in any 7-day window
- Sharpe drops below 1.0 over 30 days
- Quality flags consistently non-OK for > 24 hours

---

## 6. Scaling to Real Capital

| Phase | Capital | Duration | Gate |
|-------|---------|----------|------|
| Paper | $0 | 2 weeks | Stable operation, no crashes |
| Micro | $1,000 | 2 weeks | Live Sharpe > 2.0 |
| Small | $5,000 | 4 weeks | Live drawdown < 10% |
| Standard | $25,000 | Ongoing | Consistent positive PnL |
