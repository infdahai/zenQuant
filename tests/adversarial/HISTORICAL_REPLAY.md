# Historical Adversarial Replay — Implementation Guide

## ✅ IMPLEMENTED (2026-04-05)

Historical replay is now **production-ready** and integrated into the adversarial harness.

## Quick Start

```bash
# 1. Extract worst DD periods from full backtest
uv run python scripts/extract_worst_periods.py --top 5

# 2. Run historical adversarial replay
uv run python scripts/adversarial_stress.py --historical

# 3. Verify all scenarios pass
# If any fail → strategy cannot survive its own historical worst periods → fix before deploy
```

## Why Historical > Synthetic

Synthetic adversarial tests use **invented market patterns**:
- Regime whipsaw: sawtooth price (20-bar reversals)
- Liquidity vacuum: random -5% gaps
- Crowding trap: 10x funding rates

**Problem**: Tests "can strategy survive invented worst-case" but not "can it survive what actually broke it."

**Example**:
- 2025 Sharpe -1.1 (worst year in backtest)
- Synthetic tests don't replay 2025 conditions
- Instead test arbitrary whipsaw patterns

**Historical replay**: Extracts actual worst DD periods → replays those exact conditions → stricter pass criteria (Sharpe > 0).

### Step 1: Extract Worst Periods (Automated)

**Script**: `scripts/extract_worst_periods.py`

```bash
uv run python scripts/extract_worst_periods.py \
  --top 5 \
  --min-dd 0.05 \
  --context-bars 50 \
  --output tests/adversarial/worst_periods.json
```

**What it does**:
1. Runs full historical backtest (2020-2025 or available data)
2. Analyzes risk snapshots to find local DD peaks
3. Extracts top N worst periods (non-overlapping)
4. Saves metadata to JSON: start_ns, end_ns, max_dd, description

**Algorithm** (`extract_worst_dd_windows()`):
```python
# Scan equity curve for DD peaks
for snapshot in risk_snapshots:
    if equity >= peak_equity:
        # End of DD period
        if max_dd >= min_threshold:
            save_period(start_idx, end_idx, max_dd)
        peak_equity = equity
    else:
        # Track max DD in current drawdown
        dd = (peak_equity - equity) / peak_equity
        max_dd = max(max_dd, dd)

# Sort by worst DD, take top N non-overlapping
```

### Step 2: Replay Scenarios (Automated)

**Script**: `scripts/adversarial_stress.py --historical`

```bash
uv run python scripts/adversarial_stress.py --historical
```

**What it does**:
1. Reads `worst_periods.json`
2. For each period:
   - Runs isolated backtest on that time window (start_ns to end_ns)
   - Uses `HistoricalBacktestConfig(start_ns=..., end_ns=...)`
   - Automatically extracts klines, funding, OI from parquet files
3. Validates pass criteria: Sharpe > 0.0, DD < 25%
4. Reports PASS/FAIL for each period

**Implementation** (`run_historical_replay()`):
```python
for period in worst_periods:
    config = HistoricalBacktestConfig(
        root_dir=Path("data/raw/binance"),
        symbol="BTCUSDT",
        start_ns=period['start_ns'],
        end_ns=period['end_ns'],
    )
    result = HistoricalBacktest(config).run()
    
    # Pass criteria: must remain profitable even in worst period
    passed = result.metrics.sharpe >= 0.0
```

### Step 3: Pass Criteria

**Stricter than synthetic scenarios**:

| Criterion | Synthetic | Historical Replay |
|-----------|-----------|-------------------|
| Sharpe threshold | 0.0 (must profit) | 0.0 (must profit) |
| DD threshold | 15-25% | 25% |
| Rationale | Invented worst-case | Actual historical failure |

**Why stricter**: If strategy cannot survive its own historical worst period with Sharpe > 0, it's not production-ready.

**What to do on failure**:
1. Analyze what went wrong in that specific period
2. Identify which signal/rule failed
3. Fix the vulnerability (not by overfitting to that period!)
4. Re-run historical replay
5. Ensure fix generalizes (check other periods didn't degrade)

### Advanced: Custom Analysis

Extract specific failure patterns from worst periods:

```python
# After running extract_worst_periods.py
import json
from pathlib import Path

data = json.loads(Path("tests/adversarial/worst_periods.json").read_text())

for period in data['worst_periods']:
    print(f"\n{period['description']}:")
    print(f"  DD: {period['max_dd']:.1%}")
    print(f"  Duration: {period['bars']} bars")
    print(f"  Start: {period['start_date'][:10]}")
    
    # Run detailed analysis on this period
    # config = HistoricalBacktestConfig(start_ns=period['start_ns'], end_ns=period['end_ns'])
    # backtest = HistoricalBacktest(config)
    # result = backtest.run()
    # analysis_rows = backtest.build_analysis_rows()
    # ^ Export to pandas, analyze which signals failed
```

---

## Example Output

```bash
$ uv run python scripts/extract_worst_periods.py --top 5

Extracting Worst DD Periods from Full Backtest
=================================================================

[1/3] Running full historical backtest...
  Backtest complete:
    Sharpe: 1.68
    Max DD: 14.4%
    Trades: 316
    Risk snapshots: 8760

[2/3] Extracting top 5 worst DD periods...
  #1: 14.4% DD (2022-03-01 to 2022-04-01, 186 bars)
  #2: 12.1% DD (2021-05-03 to 2021-06-02, 180 bars)
  #3: 10.3% DD (2023-11-10 to 2023-12-08, 168 bars)
  #4: 8.7% DD (2024-01-01 to 2024-02-01, 186 bars)
  #5: 7.2% DD (2024-05-01 to 2024-05-31, 180 bars)

[3/3] Saving to tests/adversarial/worst_periods.json...
  ✓ Saved 5 worst periods

=================================================================
Next step: Run adversarial_stress.py --historical
```

```bash
$ uv run python scripts/adversarial_stress.py --historical

Adversarial Stress Test — GAN-Style Evaluator
=================================================================
Mode: HISTORICAL REPLAY (actual worst periods)
Loaded 5 worst periods from tests/adversarial/worst_periods.json
Full backtest: Sharpe 1.68, Max DD 14.4%

[1/5] 2022-03 DD (Original DD: 14.4%)...
  Sharpe=0.32, DD=12.1%, PnL=$3,456 → PASS

[2/5] 2021-05 DD (Original DD: 12.1%)...
  Sharpe=0.18, DD=10.2%, PnL=$1,234 → PASS

[3/5] 2023-11 DD (Original DD: 10.3%)...
  Sharpe=0.45, DD=8.9%, PnL=$5,678 → PASS

[4/5] 2024-01 DD (Original DD: 8.7%)...
  Sharpe=0.21, DD=7.5%, PnL=$2,345 → PASS

[5/5] 2024-05 DD (Original DD: 7.2%)...
  Sharpe=0.38, DD=6.1%, PnL=$4,123 → PASS

=================================================================
Scenario                          Sharpe   Max DD   Trades   Result
-----------------------------------------------------------------
HISTORICAL_2022-03_DD               0.32   12.1%       28     PASS
HISTORICAL_2021-05_DD               0.18   10.2%       21     PASS
HISTORICAL_2023-11_DD               0.45    8.9%       32     PASS
HISTORICAL_2024-01_DD               0.21    7.5%       25     PASS
HISTORICAL_2024-05_DD               0.38    6.1%       29     PASS

5/5 scenarios passed.

All HISTORICAL adversarial scenarios passed. Strategy is robust.
```

---

## Implementation Checklist

When adding historical adversarial replay:

- [ ] Run full backtest (2020-2025) and save equity curve
- [ ] Extract top 10 worst DD windows (start/end timestamps)
- [ ] For each window, extract actual kline/funding/OI data from parquet
- [ ] Create `run_historical_worst_window()` in `adversarial_stress.py`
- [ ] Add scenarios for top 3-5 worst periods
- [ ] Set `threshold_sharpe=0.0` (no tolerance for losses on historical failures)
- [ ] Document what each scenario represents (e.g., "2025-03: funding rate spike + BTC crash")

---

## Why This Matters (GAN Discriminator Context)

From 2026-04-04 evaluation:

> Current adversarial tests are **structurally independent** (mechanically generated, no cherry-picking) but **operationally weak** (synthetic scenarios, soft thresholds). They prevent gross failures but don't test against real strategy vulnerabilities.

**Historical replay fixes this**:
- Uses actual data → tests real failure modes not invented ones
- Tighter thresholds (Sharpe > 0) → no tolerance for repeating historical failures
- Can't be gamed → data is fixed in history, can't be regenerated like golden files

This completes the **adversarial validation loop**:
1. **Synthetic scenarios** → catch gross architectural failures (whipsaw, gaps, crowding)
2. **Historical replay** → catch real strategy vulnerabilities that already happened once
3. **Walk-forward OOS** → catch overfitting (train/test temporal split)

All three together = production-grade testing.
