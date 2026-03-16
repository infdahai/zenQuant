# Quick Start: Parameter Optimization

**Goal**: Optimize strategy parameters and start paper-live validation in 1 day.

---

## Prerequisites

```bash
# 1. Verify installation
uv sync
uv run pytest -q  # Should show 79 passed, 4 skipped

# 2. Check current branch
git branch --show-current  # Should be 'main'
```

---

## Option A: Start with Historical Data (Recommended)

### Step 1: Download Binance Historical Data

Visit: https://data.binance.vision/?prefix=data/futures/um/daily/klines/BTCUSDT/

Download last 3 months of:
- `4h` klines (candlestick data)
- `1d` klines (candlestick data)
- `funding` (funding rate data)
- `open_interest/4h` (open interest data)

Organize as:
```
data/raw/binance/
├── klines/
│   └── BTCUSDT/
│       ├── 4h/*.parquet
│       └── 1d/*.parquet
├── funding/
│   └── BTCUSDT/*.parquet
└── open_interest/
    └── BTCUSDT/
        └── 4h/*.parquet
```

### Step 2: Quick Parameter Sweep (~5 minutes)

```bash
# Run quick sweep (3 parameter combinations)
uv run python scripts/param_sweep.py --quick

# View results
uv run python scripts/compare_results.py results/param_sweep.json
```

**Expected Output**:
```
Top 10 by SHARPE:
Rank   Sharpe   PnL          MaxDD    PF     WinRate  Trades   Params
1      5.82     245,123      0.68%    6.2    68.5%    42       preserve_capital_risk=0.65, exit_transition_risk=0.85, bull_leverage=3.50, ...
2      5.45     221,696      0.76%    5.7    65.6%    38       preserve_capital_risk=0.65, exit_transition_risk=0.85, bull_leverage=3.00, ...
3      5.12     198,432      0.82%    5.2    62.3%    35       preserve_capital_risk=0.65, exit_transition_risk=0.85, bull_leverage=2.50, ...
```

### Step 3: Walk-Forward Validation (~10 minutes)

```bash
# Run walk-forward tuning (prevents overfitting)
uv run python scripts/walk_forward_tune.py --quick

# Check promotion decision
cat results/walk_forward_tuning.json | jq '.promotion_decision'
# Should output: "PROMOTE" or "REVIEW"
```

### Step 4: Update Config (if PROMOTE)

If promotion decision is "PROMOTE", update default parameters:

```bash
# Edit zenquant/strategy/playbook_engine.py
# Update PlaybookEngineConfig with recommended parameters

# Commit changes
git add zenquant/strategy/
git commit -m "tune: update parameters based on walk-forward validation

Evidence: results/walk_forward_tuning.json
Sharpe improvement: X.X → Y.Y
Max drawdown: A.A% → B.B%"
```

### Step 5: Start Paper-Live

```bash
# Set up PostgreSQL
createdb zenquant_live
export ZENQUANT_PG_DSN="postgresql://localhost/zenquant_live"

# Start server
uv run python -m zenquant.app.server --mode live

# Open dashboard (in browser)
open http://localhost:8765

# Run validation (in separate terminal)
uv run zenquant-live-validate --symbol BTCUSDT_PERP --watch 300
```

---

## Option B: Start with Paper-Live Immediately

If you don't have historical data yet, start paper-live with default parameters:

### Step 1: Start Paper-Live

```bash
# Set up PostgreSQL
createdb zenquant_live
export ZENQUANT_PG_DSN="postgresql://localhost/zenquant_live"

# Start server
uv run python -m zenquant.app.server --mode live

# Open dashboard
open http://localhost:8765
```

### Step 2: Collect Data (1 week)

Let the system run for 1 week to collect real market data.

### Step 3: Export and Optimize

```bash
# Export paper-live data
uv run python scripts/export_live_training_data.py \
    --dsn "$ZENQUANT_PG_DSN" \
    --symbol BTCUSDT_PERP \
    --label "week1"

# Run parameter sweep on live data
# (Use exported data as training set)
uv run python scripts/run_research_pipeline.py \
    --root results/live_training_runs/week1 \
    --symbol BTCUSDT_PERP \
    --label "week1_analysis"

# Review report
open results/research_runs/week1_analysis/report.html
```

---

## Daily Monitoring

### Morning Checklist

```bash
# 1. Check validation status
uv run zenquant-live-validate --symbol BTCUSDT_PERP --watch 60

# 2. Check key metrics
psql $ZENQUANT_PG_DSN -c "
SELECT
    COUNT(*) as total_decisions,
    AVG(final_leverage) as avg_leverage,
    COUNT(CASE WHEN exit_type != 'NONE' THEN 1 END) as exit_count
FROM sizing_decisions s
JOIN exit_decisions e ON s.cutoff_time_ns = e.cutoff_time_ns
WHERE s.cutoff_time_ns > EXTRACT(EPOCH FROM NOW() - INTERVAL '24 hours') * 1e9;
"

# 3. Check dashboard
open http://localhost:8765
```

### What to Look For

✅ **Good Signs**:
- Dashboard updates every 4h bar close
- PostgreSQL writes accumulating steadily
- Quality flags mostly "OK"
- Trailing stops updating correctly
- No crashes or reconnect loops

⚠️ **Warning Signs**:
- Quality flags stuck in "UNSAFE"
- No PostgreSQL writes for >4h
- Dashboard not updating
- Server crashes or restarts
- Excessive reconnect attempts

---

## Weekly Optimization

### Every Monday

```bash
# 1. Export last week's data
uv run python scripts/export_live_training_data.py \
    --dsn "$ZENQUANT_PG_DSN" \
    --symbol BTCUSDT_PERP \
    --label "week_$(date +%U)"

# 2. Generate diagnostics
uv run python scripts/run_research_pipeline.py \
    --root results/live_training_runs/week_$(date +%U) \
    --symbol BTCUSDT_PERP \
    --label "week_$(date +%U)_diagnostics"

# 3. Review report
open results/research_runs/week_$(date +%U)_diagnostics/report.html
```

### If Optimization Needed

```bash
# Run parameter sweep
uv run python scripts/param_sweep.py --full

# Run walk-forward validation
uv run python scripts/walk_forward_tune.py --full

# If promotion decision is PROMOTE:
# 1. Update config
# 2. Commit changes
# 3. Restart server
```

---

## Troubleshooting

### Server Won't Start

```bash
# Check if port is in use
lsof -i :8765

# Check PostgreSQL connection
psql $ZENQUANT_PG_DSN -c "SELECT 1;"

# Check logs
tail -f zenquant.log
```

### Dashboard Not Updating

```bash
# Check WebSocket connection (browser console)
ws = new WebSocket('ws://localhost:8765/ws');
ws.onmessage = (e) => console.log(JSON.parse(e.data));

# Check if bar-close events are firing
psql $ZENQUANT_PG_DSN -c "
SELECT MAX(cutoff_time_ns) as latest_decision
FROM market_states;
"
```

### PostgreSQL Not Writing

```bash
# Check database connection
psql $ZENQUANT_PG_DSN -c "SELECT COUNT(*) FROM market_states;"

# Check server logs for errors
grep -i "postgresql\|database" zenquant.log

# Verify DSN is set
echo $ZENQUANT_PG_DSN
```

---

## Next Steps

After 1-2 weeks of stable paper-live operation:

1. **Review Performance**
   - Sharpe > 2.0?
   - Max drawdown < 5%?
   - No major incidents?

2. **Consider Real Capital** (if performance is good)
   - Start with tiny capital ($1000)
   - Monitor closely for 1 week
   - Gradually expand if stable

3. **Continue Optimization**
   - Weekly parameter tuning
   - Monthly architecture review
   - Continuous improvement

---

## Resources

- **Full Workflow**: `docs/ITERATION_WORKFLOW.md`
- **Architecture**: `README.md`
- **Live Readiness**: `docs/LIVE_READINESS_ASSESSMENT.md`
- **Backtest Validation**: `docs/BACKTEST_VALIDATION.md`

---

**Questions?** Check `docs/` or review test files in `tests/` for examples.

**Last Updated**: 2026-03-17
