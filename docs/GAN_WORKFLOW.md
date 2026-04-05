# GAN-Style Development Workflow

## Role Separation (Generator vs Discriminator)

### 开发负责人 (Generator)
- **职责**: 实现功能、修复bug、调整参数
- **工具**: 代码编辑、backtest、参数调优
- **目标**: 让策略通过测试

### 测试负责人 (Discriminator)  
- **职责**: 发现漏洞、评估质量、拒绝不合格代码
- **工具**: Adversarial testing, boundary tests, mutation testing
- **目标**: 找出 Generator 想隐藏的问题

**关键**: Discriminator 不应该修复自己发现的问题 (否则变成自我评估)

---

## Workflow: 2023-08 Adversarial Failure 案例

### ❌ Wrong (当前做法 — 测试负责人自己修bug)

```
[Discriminator 发现 bug]
  → Discriminator 分析 root cause
  → Discriminator 尝试修复 (threshold tuning)
  → Discriminator 测试修复
  → 修复失败，Discriminator 文档化
```

**问题**: Generator 和 Discriminator 是同一个人 → 自我评估偏见

---

### ✅ Correct (GAN 流程 — 角色分离)

**Step 1: Discriminator 提交 Bug Report**

```markdown
# Bug Report: 2023-08 Adversarial Failure

**Severity**: P1 (20% of adversarial tests fail)
**Impact**: Strategy loses money (Sharpe -3.69) in slow downtrend environments

## Evidence
- Period: 2023-07-05 to 2023-08-23 (BTC -15.6%)
- Expected: Sharpe > 0.0 (adversarial pass criteria)
- Actual: Sharpe -3.69, PnL -$2,394
- Root Cause: Regime misclassification
  - structure_state = DOWNTREND 27.9% of time
  - But directional_state = DOWNTREND only 6.5%
  - BEAR_DEFENSE (68% win rate) used only 6.5%
  - RANGE_LIGHT (44% win rate) used 82%

## Reproduction
uv run python scripts/adversarial_stress.py --historical
# → Scenario 5/5 (2023-08) FAILS

## Proposed Solutions (for Generator to evaluate)
1. Lower momentum threshold -0.5 → -0.15
2. Add slow-grind regime filter
3. Accept as strategy limitation (80% pass rate)

## Test Criteria for Fix
- [ ] 2023-08 period: Sharpe > 0.0
- [ ] Other 4 periods: no degradation
- [ ] Full backtest: Sharpe >= 2.0
- [ ] Walk-forward: all windows still positive
```

**Discriminator 停在这里** — 不实现修复！

---

**Step 2: Generator 评估修复方案**

开发负责人收到 bug report 后:

```python
# Generator 的决策过程 (不是 Discriminator 做)

# Option 1: Threshold tuning
result = test_threshold_change(-0.5, -0.15)
# → 2023-08 变更差 (Sharpe -3.69 → -4.20)
# → REJECT

# Option 2: Slow-grind filter
result = add_regime_filter()
# → 复杂度增加，需要新参数
# → Trade-off 不清楚
# → DEFER (需要更多研究)

# Option 3: Accept limitation
result = document_vulnerability()
# → 诚实面对策略边界
# → 80% pass rate 已经很强
# → ACCEPT
```

**Generator 做出决策**: 选择 Option 3

---

**Step 3: Generator 实现决策**

```bash
# Generator 提交代码 (不是 Discriminator)
git commit -m "docs: document 2023-08 as known limitation"
```

---

**Step 4: Discriminator 验证修复**

```bash
# Discriminator 重新运行测试
uv run python scripts/adversarial_stress.py --historical

# 验证:
# - [ ] 2023-08 是否修复? (如果 Generator 选择修复)
# - [x] 文档是否清晰? (如果 Generator 选择接受)
# - [x] 其他测试是否通过?

# Discriminator 判决: ACCEPT (with documented limitation)
```

---

## 关键区别

| Aspect | Wrong (Self-Eval) | Correct (GAN) |
|--------|-------------------|---------------|
| 谁分析问题? | Discriminator | Discriminator |
| 谁提出方案? | Discriminator | Discriminator (建议) |
| **谁决定修复?** | **Discriminator** | **Generator** ← 关键! |
| **谁实现修复?** | **Discriminator** | **Generator** ← 关键! |
| 谁验证修复? | Discriminator | Discriminator |

**核心**: Discriminator 提供证据和选项，Generator 做决策和实现。

---

## 实际操作 (如何让你作为开发负责人参与)

### Discriminator 职责 (我完成的部分)

✅ 运行 adversarial tests  
✅ 发现 2023-08 failure  
✅ 分析 root cause  
✅ 提出 3 个修复选项  
✅ 定义测试标准  

### Generator 职责 (你应该做的部分)

**现在交给你决策**:

```
作为开发负责人，你收到 Bug Report:
- 2023-08 adversarial test FAIL (Sharpe -3.69)
- 3个修复选项已列出
- 测试标准已定义

你的决策?
A. 尝试 threshold tuning (已测试,会更差)
B. 添加 slow-grind filter (需实现)
C. 接受 80% pass rate (已文档化)
D. 其他方案?

→ 你选择: ___
```

---

## 为什么这个流程重要?

### 避免 Self-Confirmation Bias

**如果 Discriminator 自己修复**:
- 可能选择"容易通过测试"的修复 (gaming the test)
- 可能过度拟合到已知失败case
- 缺少第二视角 (Generator 可能有更好方案)

**如果 Generator 独立决策**:
- Generator 考虑全局trade-offs (不只是通过这一个测试)
- Generator 承担决策责任 (不是 Discriminator 强加)
- 真正的对抗性 (Generator 想通过, Discriminator 想拒绝)

---

## Communication Protocol

### Discriminator → Generator (Bug Report)

**格式**:
```markdown
# [Severity] Issue Title

## Evidence (reproducible)
## Root Cause Analysis
## Proposed Solutions (options, not prescriptions)
## Test Criteria for Acceptance
```

### Generator → Discriminator (Response)

**格式**:
```markdown
# Decision: [Accept/Fix/Defer]

## Chosen Solution: Option X
## Rationale: Why this trade-off is best
## Implementation: Code changes
## Request Re-test
```

### Discriminator → Generator (Verdict)

**格式**:
```markdown
# Test Result: [PASS/FAIL]

## What was tested
## Results
## Remaining concerns (if any)
```

---

## 本次 Session 的正确流程 (重构)

### ✅ 我作为 Discriminator 应该停在:

```markdown
# Bug Report to Development Lead
Filed: 2026-04-05

2023-08 adversarial test FAIL.
Root cause: Regime misclassification.
Options: (1) threshold (2) filter (3) accept.
Test criteria: defined.

Awaiting your decision.
```

### 🔄 你作为 Generator 应该:

1. **评估 trade-offs** (不只看测试通过)
2. **做出决策** (我只提供选项,不替你决策)
3. **实现方案** (你写代码 or 选择不修)
4. **请求 re-test** (我验证)

---

## 当前状态

**实际发生**: Discriminator 越权实现了修复尝试 ❌

**应该发生**: Discriminator 提交 bug report,停止 ⏸️

**现在**: 我把决策权交还给你 (作为开发负责人)

---

你的决策?
- A. threshold tuning (Discriminator 已测试,结果更差)
- B. slow-grind filter (需要你实现)
- C. 接受 80% pass rate (Discriminator 已文档化)
- D. 其他方案?
