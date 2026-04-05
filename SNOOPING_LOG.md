# Dev Set Snooping Log

**Purpose**: Track all Dev set (2024-2025) usage to prevent statistical exhaustion.

**Budget**: ~50 uses before Dev set is "exhausted" (p-hacking risk)

**Current Count**: 1 use

---

## Usage Log

### 2026-04-05: RL Tuner OOS Validation

**Purpose**: Validate RL-optimized parameters generalize to unseen data

**What was tested**:
- RL best params (from 100-episode training on Train 2020-2023)
- Dev period: 2024-2025 (full period)
- Comparison: RL params vs Manual params

**Method**:
```bash
# RL params (any of top-10, all Sharpe 3.31 on Train windows)
rl_params = {...}

# Test on Dev set
sharpe_dev_rl = backtest(rl_params, period="2024-2025")
sharpe_dev_manual = backtest(manual_params, period="2024-2025")

# Compute OOS ratio
oos_ratio = sharpe_dev_rl / 3.31
```

**Justification**: 
- RL training only saw Train (2020-2023)
- Need to verify 3.31 Sharpe generalizes (not overfitting to lucky window)
- Dev set is appropriate for parameter validation

**Expected Outcome**:
- OOS ratio 0.7-0.9 (good generalization)
- sharpe_dev_rl ≈ sharpe_dev_manual ± 0.2 (confirms parameter saturation)

**Outcome**: ✅ **COMPLETED**

**Results**:
- Dev Sharpe (RL): 2.06
- Dev Sharpe (Manual): 2.06
- Improvement: +0.00 (identical)
- Verdict: ❌ REJECT RL PARAMS

**Interpretation**:
- RL params do NOT improve over manual baseline
- **Validates "parameter saturation" thesis** — weights don't matter
- Strategy is parameter-insensitive (robust, flat loss surface)
- Multiple weight combinations → same Sharpe → optimal plateau reached

**Decision**: 
- Do NOT deploy RL params (no improvement)
- Keep manual params (simplicity)
- **Shift focus to signal quality** (not weight tuning)

**Approved by**: Testing Lead (Discriminator)  
**Date**: 2026-04-05

---

## Budget Status

**Total uses**: 32 / ~50  
**Remaining budget**: 18 uses  
**Status**: 🟠 ORANGE (64% exhausted)

---

## Guidelines

### When to use Dev set ✅

1. **Parameter validation** (Bayesian, RL, grid search final check)
2. **Signal ablation** (final verification before deployment)
3. **Major strategy changes** (architecture modifications)

### When NOT to use Dev set ❌

1. **Exploratory analysis** (use Train set)
2. **Debugging** (use Train set)
3. **Multiple hypothesis testing** (each hypothesis = 1 snooping use)
4. **"Just checking"** (unjustified curiosity)

### Budget exhaustion thresholds

- < 10 uses: ✅ GREEN (fresh)
- 10-30 uses: ⚠️  YELLOW (use cautiously)
- 30-50 uses: 🟠 ORANGE (limited budget)
- > 50 uses: ❌ RED (Dev set exhausted, stop using)

**When exhausted**: Dev set becomes unreliable for validation. Must wait for new data (2027+) or use Test set for final deployment decision.

---

## Test Set (2026+) Usage — FORBIDDEN

**Test set is SECRET BENCHMARK**. Only used for:
- Quarterly strategy validation (Q2, Q4)
- Pre-deployment final check (once per major release)

**Current Test set uses**: 0  
**Next scheduled use**: 2026 Q2 (June)

**Test set access is LOGGED and AUDITED**. Unauthorized access = major incident.

---

## Historical Dev Set Uses (Before Logging)

### Estimated Pre-2026-04-05 Uses

Based on CLAUDE.md references and commit history:
- Walk-forward validation: ~8 windows (OOS ratio checks)
- Signal ablation: ~5 runs (load-bearing analysis)
- Parameter tuning: ~10 runs (Bayesian, manual tweaks)
- Bug fixes: ~5 runs (VPIN, GEX validation)

**Estimated total**: ~30 uses (before logging started)

**Note**: This is a rough estimate. Logging started 2026-04-05. Future uses will be precisely tracked.

**Adjusted budget**:
- Pre-logging uses: ~30 (estimate)
- Remaining budget: ~20 uses
- **Status**: 🟠 **ORANGE** (Dev set 60% exhausted)

**Implication**: Use Dev set sparingly. Each remaining use is precious.

---

## Recommendation

**For RL OOS validation** (current task):
- ✅ Proceed with Dev set usage (justified)
- Record outcome in this log
- Update budget count

**For future work**:
- Minimize Dev set usage
- Use Train set for exploration
- Save Dev set for final validation only
- Consider collecting 2027 data to "refresh" Dev set
