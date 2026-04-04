# Anti-Snooping Infrastructure

**Problem**: Data snooping — 在同一组test数据上反复实验，statistical budget耗尽导致overfit

**Solution**: 3层防护 + 1个自动化

---

## 1. Data Split Enforcement (`config/data_splits.toml`)

```
Train (2020-2023) → 自由实验，unlimited
Dev   (2024-2025) → 验证用，track in SNOOPING_LOG
Test  (2026+)     → Secret benchmark，quarterly blind test ONLY
```

**规则**: 任何基于Test结果的参数调整 = OVERFIT

---

## 2. Experiment Tracking (`experiments/SNOOPING_LOG.md`)

记录所有Dev set实验:
- 实验名称 + Sharpe delta
- 采取的action (参数改动)
- Statistical budget cost (HIGH/MEDIUM/LOW)

**当前状态**: ~30 runs on Dev → HIGH risk
**缓解措施**: 未来调参只用Train，Dev仅做validation

---

## 3. Anti-Snooping Validator (`scripts/validate_no_snooping.py`)

静态分析检查hardcoded artifacts:
- Dev/Test set dates (2024-2026)
- 过于精确的thresholds (0.467 → 疑似从ablation来的magic number)
- Symbol-specific hardcoding

**False positive escape hatch**:
```python
threshold = 0.0001  # SNOOPING_OK: exchange min order size
```

**运行**: 
```bash
uv run python scripts/validate_no_snooping.py
uv run python scripts/validate_no_snooping.py --strict  # Fail CI on suspicion
```

---

## 4. Quarterly Secret Benchmark (`scripts/secret_benchmark.py`)

2026+ OOS data的blind test:
- **Output**: PASS/FAIL only (不显示详细指标防止snooping)
- **Pass criteria**: Sharpe > 0.5, DD < 30%, Trades > 10, PnL > 0
- **Frequency**: Quarterly (GitHub Action自动运行)
- **Debug mode**: `--debug` 显示详细指标 (ONLY for infra debugging)

**手动运行**:
```bash
uv run python scripts/secret_benchmark.py
```

**自动运行**: `.github/workflows/secret_benchmark.yml`
- Cron: 每季度1号 (Jan 1, Apr 1, Jul 1, Oct 1)
- On failure: 自动创建GitHub Issue alert
- Auto-commit SNOOPING_LOG update

---

## Workflow

### 日常开发 (Train set)
```bash
# 1. 在Train set (2020-2023) 上自由调参
uv run python scripts/run_auto_research.py --split train

# 2. 用Dev set (2024-2025) 验证 — 记录到SNOOPING_LOG
uv run python -m zenquant.app.backtest  # 默认跑全期
# 检查 experiments/SNOOPING_LOG.md 并手动添加记录

# 3. 检查snooping artifacts
uv run python scripts/validate_no_snooping.py
```

### 季度评估 (Test set)
```bash
# GitHub Action自动运行，或手动触发:
uv run python scripts/secret_benchmark.py

# 如果FAIL:
#   - 不要基于结果调参 (那就是overfit)
#   - 回到Train/Dev分析root cause
#   - 如果是market regime change → 预期行为
#   - 如果是infra bug → 修复后用 --debug 验证
```

---

## Files

| File | Purpose |
|------|---------|
| `config/data_splits.toml` | Temporal split定义 (Train/Dev/Test) |
| `experiments/SNOOPING_LOG.md` | Statistical budget tracker |
| `scripts/validate_no_snooping.py` | 静态代码检查 (detect hardcoded artifacts) |
| `scripts/secret_benchmark.py` | Quarterly blind test (2026+ OOS) |
| `.github/workflows/secret_benchmark.yml` | 自动化quarterly run + alert |

---

## Sprint Contracts

**Gate 0 (新增)**: Anti-snooping validation
```bash
uv run python scripts/validate_no_snooping.py --strict
```

**Quarterly (自动)**: Secret benchmark on 2026+ data
- Pass → 策略仍robust
- Fail → Issue alert, investigate on Train/Dev

---

## Philosophy

> "The test set is not a treasure map. It's a landmine detector."  
> — 用来验证你**没有**overfit，不是用来**寻找**alpha

statistical budget是有限的。每次看Test结果，都在消耗budget。
解决方案：机制化防护 > 人的纪律。
