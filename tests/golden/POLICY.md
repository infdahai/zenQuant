# Golden Output Regeneration Policy

## Purpose

Golden output files (`tests/golden/backtest.json`) validate **reproducibility**, not **correctness**:
- They freeze the current behavior of the system
- They catch **unintended regressions** (accidental changes)
- They do NOT validate that behavior is correct

## The Circular Reasoning Problem

**Anti-pattern**: Code change → golden test fails → regenerate golden file → test passes

This creates circular reasoning:
1. Developer changes strategy
2. Backtest output changes
3. Golden test fails
4. Developer regenerates golden file
5. **New output becomes "correct" by definition**

If a bug was introduced in step 1, it's now encoded as expected behavior.

## Policy: When to Regenerate Golden Files

### ✅ ALLOWED (refactoring)
Regenerate golden files when:
- **Code structure changed** but behavior should be identical (e.g., refactoring, renaming)
- **Data format changed** but results should be equivalent (e.g., serialization updates)
- **Test infrastructure changed** (e.g., adding new diagnostic fields)

**Required**:
1. Verify `test_backtest_invariants.py` still passes (validates correctness)
2. Commit message explains: "Golden regen: refactoring X, behavior unchanged"
3. Diff the golden file to confirm changes are mechanical (e.g., field rename, not value change)

### ⚠️ REQUIRES APPROVAL (strategy changes)
Regenerate golden files when:
- **Strategy logic changed** intentionally (new signal, new sizing rule)
- **Parameters tuned** (threshold adjustments, weight changes)
- **New features added** that alter trading behavior

**Required**:
1. Verify `test_backtest_invariants.py` still passes (new behavior is sane)
2. Document WHY behavior changed in commit message
3. Include before/after metrics (Sharpe, DD, trade count) in PR description
4. Get approval from project owner before merging

### ❌ FORBIDDEN (correctness failures)
DO NOT regenerate golden files when:
- **`test_backtest_invariants.py` fails** (fundamental correctness violation)
- **Bug suspected** (unexpected metric changes without code explanation)
- **Metrics degraded** without understanding why (e.g., Sharpe dropped, DD increased)

**Action**: Fix the bug in the code, don't encode it in the golden file.

---

## Two-Layer Validation

The test suite has two complementary layers:

| Test | Validates | Can Be "Defined Away"? |
|------|-----------|----------------------|
| `test_golden_output.py` | Reproducibility (output matches frozen baseline) | YES — by regenerating golden file |
| `test_backtest_invariants.py` | Correctness (output satisfies fundamental properties) | NO — invariants are fixed |

**Both must pass**:
- Golden test → behavior is stable
- Invariant test → behavior is sane

**Golden fails, invariant passes**:
- Intentional change → OK to regen (with approval)

**Invariant fails**:
- Fundamental bug → DO NOT regen, fix the code

---

## Historical Context

**Problem discovered**: 2026-04-04 GAN discriminator evaluation found golden file regenerated in 13/13 feature commits:
- `73dcd16`: PaulWei DD → net_pnl -962→+3.7M, sharpe -10.25→+6.89
- `bc9893b`: asymmetric → net_pnl +25k→-962, sharpe +3.23→-10.25
- `441a372`: rv_hf → sharpe +1.74→+1.83

Each regen encoded "current behavior" as "correct behavior" without independent validation.

**Solution**: Dual-layer testing (golden + invariants) + strict regen policy.

---

## Checklist: Before Regenerating Golden File

- [ ] `test_backtest_invariants.py` passes (correctness validated)
- [ ] Change is intentional (refactoring or approved strategy change)
- [ ] Commit message explains WHY golden changed
- [ ] Diff reviewed (metrics change makes sense)
- [ ] If strategy change: before/after metrics documented in PR

**When in doubt**: Ask project owner before regenerating.
