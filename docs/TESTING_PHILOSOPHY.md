# Testing Philosophy — GAN Discriminator Principles

> **Context**: 当同一个 agent 写代码和测试时,容易产生 self-confirmation bias。本文档定义独立评估原则,防止 "generator evaluating itself"。

---

## Core Problem: Generator-Discriminator Alignment

**Classic GAN**:
- Generator: 创建样本骗过 discriminator
- Discriminator: 学习检测假样本
- **关键**: 对抗性训练 → discriminator 越来越严格

**Development Context**:
- Generator (开发 agent): 写代码 + 测试
- Discriminator (评估 agent): 审查测试质量
- **风险**: 同样的 AI model → 相同偏见 → self-confirmation

**解决方案**: 机制化独立评估,不依赖 agent 自律。

---

## Three-Layer Test Independence

| Layer | Type | Can be Gamed? | Status |
|-------|------|---------------|--------|
| **L1: Mechanical** | Walk-forward, anti-snooping | ❌ NO (历史数据固定) | ✅ STRONG |
| **L2: Property-based** | Invariant tests, boundary tests | ⚠️ HARD (需要理解业务逻辑) | 🟡 MEDIUM |
| **L3: Snapshot** | Golden output, regression | ✅ YES (regenerate = define away bugs) | ⚠️ WEAK |

**原则**: L1 > L2 > L3 的信任优先级。当 L3 失败时,不能简单 regenerate — 需要 L1/L2 确认是否 intentional。

---

## Anti-Bias Pattern 1: Boundary Tests

### The Problem: Happy Path Bias

**Example** (from actual codebase):
```python
# Implementation
long_dd_threshold = 0.15  # 15% DD triggers risk reduction

# Test (WRONG — threshold-avoidant)
assert not triggered(dd=0.10)  # Safely below threshold

# Test (CORRECT — boundary-focused)
assert not triggered(dd=0.15)      # Exact boundary
assert not triggered(dd=0.14999)   # Just below
assert triggered(dd=0.15001)       # Just above
assert not triggered(dd=0.0)       # Zero edge case
assert handles_gracefully(dd=None) # Invalid input
```

### Template: Parametrized Boundary Tests

```python
import pytest

@pytest.mark.parametrize("dd,expected_triggered", [
    (0.15,       False),  # Exact threshold (logic uses >)
    (0.14999,    False),  # Float epsilon below
    (0.15001,    True),   # Epsilon above
    (0.0,        False),  # Zero edge case
    (-0.01,      False),  # Negative (should not crash)
    (1.0,        True),   # 100% DD (extreme)
    (float('inf'), True), # Infinity (should handle)
])
def test_dd_threshold_boundaries(dd, expected_triggered):
    """Test drawdown threshold at exact boundaries."""
    result = check_drawdown_risk(dd)
    assert result.triggered == expected_triggered
```

### Checklist: Every Threshold Needs

- [ ] Exact boundary test
- [ ] ±1 ULP (unit in last place) test
- [ ] Zero test (if applicable)
- [ ] None/null test (if data can be missing)
- [ ] Negative test (if logically invalid)
- [ ] Extreme value test (inf, very large)

---

## Anti-Bias Pattern 2: Dual-Layer Validation

### The Problem: Circular Reasoning

**Golden output anti-pattern**:
1. 实现新功能 → backtest 输出改变
2. Golden test 失败
3. **Regenerate golden file** → test passes
4. Bug 被编码为 "expected behavior"

### Solution: Golden + Invariants

```python
# Layer 1: Golden (for reproducibility, not correctness)
def test_golden_reproducibility():
    """Ensures code changes are intentional (regression detector)."""
    result = run_backtest()
    expected = load_golden("backtest.json")
    assert result == expected  # Exact match

# Layer 2: Invariants (for correctness, cannot be defined away)
def test_backtest_invariants():
    """Validates CORRECTNESS independent of golden output."""
    result = run_backtest()
    
    # Domain invariants (cannot be gamed by regenerating files)
    assert result.metrics.sharpe > 0.0, "Must be profitable"
    assert result.metrics.max_drawdown < 0.50, "DD must be <50%"
    assert result.metrics.trade_count > 10, "Must have trades"
    assert not math.isnan(result.metrics.sharpe), "No NaN metrics"
    assert result.final_equity > result.starting_equity, "Must end higher"
```

### When to Regenerate Golden Files

From `tests/golden/POLICY.md`:

| Scenario | Action | Approval |
|----------|--------|----------|
| **Refactoring** (structure change, no behavior change) | ✅ Regenerate | Self-approve |
| **Bugfix** (invariant tests fail) | ❌ Fix bug first | Never regenerate |
| **Feature** (intentional behavior change) | ⚠️ Regenerate + explain | Requires before/after metrics |

**Red flag**: Regenerating golden file when invariant tests fail = encoding bug as expected.

---

## Anti-Bias Pattern 3: Mutation Testing

### The Problem: Tests that Don't Actually Test

**Example**:
```python
# Implementation
if drawdown > threshold:  # BUG: Should be >=
    trigger_risk_reduction()

# Test (FALSE POSITIVE — passes even with bug)
def test_drawdown_trigger():
    result = check_dd(0.20, threshold=0.15)
    assert result.triggered  # Passes because 0.20 > 0.15 (despite >= bug)
```

### Solution: Mutation Test Baseline

Use `mutmut` to verify tests catch bugs:

```bash
# Install
uv pip install mutmut

# Run mutation testing on critical modules
mutmut run --paths-to-mutate zenquant/risk/overlay.py

# Check results
mutmut results
# Expect: >80% mutants killed (caught by tests)
# If <50% killed → tests are too weak
```

**Mutation examples**:
- `>` → `>=` → If tests still pass, boundary logic untested
- `and` → `or` → If tests still pass, boolean logic untested
- `+` → `-` → If tests still pass, arithmetic untested
- `0.15` → `0.14` → If tests still pass, threshold magic number

**Baseline** (from `memory/feedback_testing_discipline.md`):
```
Module: zenquant/risk/overlay.py
Mutants: 47
Killed: 39 (83%)  ← GOOD
Survived: 8 (17%) ← Investigate these
```

---

## Anti-Bias Pattern 4: Adversarial Validation

### The Problem: Synthetic Theater

**Weak adversarial test**:
```python
def test_strategy_survives_whipsaw():
    """Test invented worst-case scenario."""
    # Synthetic sawtooth pattern (mechanically generated)
    result = backtest_with_whipsaw(period=20)
    assert result.sharpe > -1.0  # Very soft threshold (can lose money)
```

**Problems**:
- Synthetic scenarios don't match real failure modes
- Soft pass criteria (Sharpe > -1.0 allows losses)
- Not derived from actual strategy weaknesses

### Solution: Historical Replay

```python
def test_strategy_survives_worst_historical_periods():
    """Test against ACTUAL worst periods from backtest history."""
    
    # Extract top 5 worst DD windows from full backtest
    worst_periods = extract_worst_periods(
        backtest_history="data/backtest_2020_2025.parquet",
        top_n=5,
        min_dd=0.05,
    )
    
    for period in worst_periods:
        # Replay exact historical conditions
        result = backtest_period(
            start_ns=period['start_ns'],
            end_ns=period['end_ns'],
        )
        
        # Strict pass criteria: must remain profitable even in worst period
        assert result.metrics.sharpe > 0.0, (
            f"Failed on {period['description']}: "
            f"Sharpe {result.metrics.sharpe:.2f} (must be >0)"
        )
        assert result.metrics.max_drawdown < 0.25, (
            f"DD {result.metrics.max_drawdown:.1%} exceeds 25%"
        )
```

**Why this works**:
- Uses actual historical data (cannot invent easier scenarios)
- Tests real failure modes (not synthetic theater)
- Strict criteria (Sharpe > 0, not -1)
- Cannot be gamed (historical data is fixed)

---

## Testing Pyramid for Trading Strategies

```
        /\
       /  \
      / L1 \    Walk-Forward OOS (mechanical, temporal split)
     /______\   Anti-Snooping Validator (static analysis)
    /        \
   /   L2    \  Adversarial Historical Replay
  /          \  Boundary + Invalid Input Tests
 /____________\ Property-Based Invariants
/              \
/      L3       \ Golden Output (reproducibility only)
/________________\ Unit Tests (happy path OK here)
```

**Resource allocation**:
- 60% effort → L1 (mechanical, cannot be gamed)
- 30% effort → L2 (property-based, hard to game)
- 10% effort → L3 (snapshot, easy to game but cheap)

---

## Red Flags: Thought Patterns that Signal Bias

| Thought | Reality | Action |
|---------|---------|--------|
| "Test value 0.10 when threshold is 0.15" | Threshold-avoidant | Use 0.15, 0.14999, 0.15001 |
| "Golden file changed, I'll regenerate" | Circular reasoning | Check invariant tests first |
| "This synthetic scenario is hard enough" | Theater adversarialism | Use historical worst periods |
| "Tests pass, code is correct" | False confidence | Run mutation testing |
| "I'll add happy path tests first" | Happy path bias | Add boundary tests first |

---

## Implementation Checklist

When adding a new feature:

### Before Writing Code
- [ ] Define invariants that must hold (Sharpe > 0, DD < X%, etc.)
- [ ] Identify all thresholds that will need boundary tests
- [ ] Plan adversarial scenarios (historical or synthetic)

### After Writing Code
- [ ] Write invariant tests (cannot be defined away)
- [ ] Write boundary tests for every threshold
- [ ] Add invalid input tests (None, empty, negative, NaN)
- [ ] Update golden files ONLY if invariants still pass
- [ ] Run mutation testing on critical paths

### Before Committing
- [ ] All tests pass (including invariants)
- [ ] Mutation testing >70% kill rate
- [ ] No new snooping artifacts (validate_no_snooping.py)
- [ ] Golden changes documented in commit message

---

## References

- **Plan**: `/Users/disaster/.claude/plans/enchanted-questing-breeze.md` — GAN discriminator audit
- **Memory**: `memory/feedback_testing_discipline.md` — Testing discipline across sessions
- **Golden Policy**: `tests/golden/POLICY.md` — When to regenerate snapshots
- **Adversarial Guide**: `docs/ADVERSARIAL_HARNESS.md` — Historical replay implementation

---

**Philosophy**: 

> "测试的价值在于它能捕获的 bug,不在于它能通过的次数。"
> 
> "如果 golden file 可以被 regenerate,它就不是 correctness oracle,只是 reproducibility detector。"
> 
> "真正的独立评估来自机制化约束,不是 agent 自律。"
