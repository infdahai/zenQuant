# ZenQuant 开发任务列表

**更新日期**: 2026-03-15
**当前阶段**: Phase 4 已完成，准备 Phase 5

---

## 当前状态

✅ **Phase 3**: State Engine（状态引擎）- 已完成
✅ **Phase 4**: Playbook Engine（策略手册引擎）- 已完成
⏳ **Phase 5**: Sizing Engine（仓位引擎）- 待开始
⏳ **Phase 6**: Exit Engine（退出引擎）- 待开始

---

## Phase 5: Sizing Engine 任务清单

### P5.1: 设计 SizingDecision 域对象
**负责人**: [待分配]
**预估**: 0.5 天

- [ ] 在 `zenquant/domain/models.py` 定义 `SizingDecision` dataclass
- [ ] 添加字段：`base_size`, `confidence_multiplier`, `stress_discount`, `execution_haircut`, `final_leverage`
- [ ] 添加字段：`reason_codes` (list[str])
- [ ] 添加方法：`breakdown()` 返回分解明细字典
- [ ] 更新 `zenquant/domain/__init__.py` 导出

**验收标准**:
- [ ] 代码编译通过
- [ ] 类型注解完整
- [ ] Docstring 完整

---

### P5.2: 实现 SizingEngine 模块
**负责人**: [待分配]
**预估**: 2 天

- [ ] 创建 `zenquant/strategy/sizing_engine.py`
- [ ] 实现 `_base_size(equity, realized_vol, target_vol)` 方法
  - 基于 vol targeting 计算基础仓位
  - 公式：`target_vol / max(realized_vol, target_vol * 0.25)`
- [ ] 实现 `_confidence_multiplier(state, playbook)` 方法
  - 基于 state.overall_confidence() 和 playbook.playbook_confidence
  - 公式：`(state_conf + playbook_conf) / 2`
- [ ] 实现 `_stress_discount(state, indicators)` 方法
  - 基于 positioning stress (funding_oi_z)
  - 基于 volatility stress (realized_vol)
  - 基于 quality state
  - 公式：`positioning_discount * volatility_discount * quality_discount`
- [ ] 实现 `_execution_haircut(state)` 方法
  - 基于 quality_state
  - TRADABLE: 1.0, NOISY: 0.9, DEGRADED: 0.7, UNSAFE: 0.0
- [ ] 实现 `compute(state, playbook, equity, indicators)` 主方法
  - 调用上述 4 个方法
  - 计算 final_leverage = base * confidence * stress * haircut
  - 限制在 playbook.max_leverage 以内
  - 返回 SizingDecision 对象
- [ ] 更新 `zenquant/strategy/__init__.py` 导出

**验收标准**:
- [ ] 所有方法实现完整
- [ ] 代码编译通过
- [ ] Docstring 完整
- [ ] 类型注解完整

---

### P5.3: 集成到策略引擎
**负责人**: [待分配]
**预估**: 1 天

- [ ] 在 `ARCStrategyEngine.__init__()` 实例化 `SizingEngine`
  ```python
  self._sizing_engine = SizingEngine()
  ```
- [ ] 在 `_build_target()` 调用 sizing engine
  ```python
  sizing = self._sizing_engine.compute(
      state=state,
      playbook=playbook,
      equity=self._equity,
      indicators={
          "realized_vol": vals.realized_vol,
          "funding_oi_z": vals.funding_oi_z,
      },
  )
  target_leverage = sizing.final_leverage
  ```
- [ ] 移除内联的 sizing 逻辑（原 249-260 行）
- [ ] 发布 sizing 事件
  ```python
  self._bus.publish(f"strategy.sizing.{pack.timeframe.value}.{pack.symbol}", sizing)
  ```
- [ ] 在 `TargetPosition.reason_codes` 添加 sizing reason codes

**验收标准**:
- [ ] 代码编译通过
- [ ] 所有测试通过
- [ ] Sizing 事件正确发布

---

### P5.4: 测试
**负责人**: [待分配]
**预估**: 1.5 天

- [ ] 创建 `tests/test_sizing_engine.py`
- [ ] 测试 `_base_size()` 计算
  - 正常 vol
  - 极低 vol（触发 floor）
  - 极高 vol
- [ ] 测试 `_confidence_multiplier()` 计算
  - 高置信度
  - 低置信度
  - 边界情况
- [ ] 测试 `_stress_discount()` 计算
  - 无 stress
  - positioning stress (crowding)
  - volatility stress (distorted)
  - quality stress (degraded)
  - 多重 stress 叠加
- [ ] 测试 `_execution_haircut()` 计算
  - TRADABLE: 1.0
  - NOISY: 0.9
  - DEGRADED: 0.7
  - UNSAFE: 0.0
- [ ] 测试完整 `compute()` 流程
  - 正常场景
  - 边界场景
  - max_leverage 限制
- [ ] 集成测试：验证策略引擎使用 sizing engine
- [ ] 运行所有测试：`uv run pytest -v`

**验收标准**:
- [ ] 所有测试通过
- [ ] 测试覆盖率 > 80%
- [ ] 边界情况覆盖

---

### P5.5: 回测验证
**负责人**: [待分配]
**预估**: 1 天

- [ ] 运行 Phase 4 回测，记录 baseline metrics
  - Total return
  - Sharpe ratio
  - Max drawdown
  - Trade count
- [ ] 运行 Phase 5 回测，记录 new metrics
- [ ] 对比验证：
  - Returns 差异应 < 0.5%（sizing 逻辑未变，只是重构）
  - 如果差异 > 0.5%，调查原因
- [ ] 如果验证失败，回滚并修复

**验收标准**:
- [ ] Returns 差异 < 0.5%
- [ ] 其他 metrics 基本一致
- [ ] 无明显性能退化

---

### P5.6: 仪表板集成（可选）
**负责人**: [待分配]
**预估**: 0.5 天

- [ ] 在 `zenquant/app/live_state.py` 订阅 sizing 事件
- [ ] 在 dashboard snapshot 添加 sizing breakdown
- [ ] 在前端显示：
  - Base size
  - Confidence multiplier
  - Stress discount
  - Execution haircut
  - Final leverage
  - 公式：base × confidence × stress × haircut = final

**验收标准**:
- [ ] 仪表板正确显示 sizing breakdown
- [ ] 数值计算正确
- [ ] UI 清晰易读

---

## Phase 6: Exit Engine 任务清单

### P6.1: 设计 ExitDecision 域对象
**负责人**: [待分配]
**预估**: 0.5 天

- [ ] 在 `zenquant/domain/models.py` 定义 `ExitDecision` dataclass
- [ ] 定义 exit_type: `NONE | TRAILING | INVALIDATION | TIME_DECAY | REGIME_CHANGE`
- [ ] 定义 exit_urgency: `NONE | GRADUAL | IMMEDIATE`
- [ ] 添加字段：`target_reduction_pct` (0.0-1.0)
- [ ] 添加字段：`reason_code`, `reason_detail`
- [ ] 更新 `zenquant/domain/__init__.py` 导出

**验收标准**:
- [ ] 代码编译通过
- [ ] 类型注解完整
- [ ] Docstring 完整

---

### P6.2: 实现 ExitEngine 模块
**负责人**: [待分配]
**预估**: 2.5 天

- [ ] 创建 `zenquant/strategy/exit_engine.py`
- [ ] 实现 `_check_invalidation(position, state, playbook)` 方法
  - 检查原假设是否失效
  - 例如：Bull playbook 但 directional_state → DOWNTREND
  - 返回 IMMEDIATE exit
- [ ] 实现 `_check_trailing_stop(position, indicators, playbook)` 方法
  - 移植现有 `_check_trailing_stop()` 逻辑
  - 使用 playbook.trailing_stop_atr_mult
  - 返回 IMMEDIATE exit
- [ ] 实现 `_check_time_decay(position, playbook)` 方法
  - 检查 position.bars_held > playbook.time_decay_bars
  - 检查是否无盈利（unrealized_pnl < 0）
  - 返回 GRADUAL exit (50% reduction)
- [ ] 实现 `_check_regime_change(position, state, playbook)` 方法
  - 检查 state 是否不再匹配 playbook
  - 例如：Bull playbook 但 volatility_state → DISTORTED
  - 返回 GRADUAL exit
- [ ] 实现 `check(position, state, playbook, indicators)` 主方法
  - 按优先级调用上述 4 个方法
  - 优先级：invalidation > trailing > time_decay > regime_change
  - 返回第一个非 NONE 的 ExitDecision
- [ ] 更新 `zenquant/strategy/__init__.py` 导出

**验收标准**:
- [ ] 所有方法实现完整
- [ ] 代码编译通过
- [ ] Docstring 完整
- [ ] 优先级逻辑正确

---

### P6.3: 集成到策略引擎
**负责人**: [待分配]
**预估**: 1.5 天

- [ ] 在 `ARCStrategyEngine.__init__()` 实例化 `ExitEngine`
  ```python
  self._exit_engine = ExitEngine()
  ```
- [ ] 在 `_build_target()` 调用 exit engine
  ```python
  exit_decision = self._exit_engine.check(
      position=position,
      state=state,
      playbook=playbook,
      indicators={"price": vals.price, "atr": vals.atr},
  )
  if exit_decision.exit_type != "NONE":
      # 构建 exit target
      return TargetPosition(..., target_qty=..., reduce_only=True, ...)
  ```
- [ ] 移除内联的 exit 逻辑（原 210-236 行）
- [ ] 发布 exit 事件
  ```python
  self._bus.publish(f"strategy.exit.{pack.timeframe.value}.{pack.symbol}", exit_decision)
  ```
- [ ] 在 `TargetPosition.reason_codes` 添加 exit reason codes

**验收标准**:
- [ ] 代码编译通过
- [ ] 所有测试通过
- [ ] Exit 事件正确发布

---

### P6.4: 测试
**负责人**: [待分配]
**预估**: 2 天

- [ ] 创建 `tests/test_exit_engine.py`
- [ ] 测试 `_check_invalidation()`
  - Bull playbook + DOWNTREND → exit
  - Bear playbook + UPTREND → exit
  - 无 invalidation → no exit
- [ ] 测试 `_check_trailing_stop()`
  - Long position, price 跌破 trailing stop → exit
  - Short position, price 涨破 trailing stop → exit
  - 未触发 → no exit
- [ ] 测试 `_check_time_decay()`
  - 持仓 > time_decay_bars 且无盈利 → exit
  - 持仓 > time_decay_bars 但有盈利 → no exit
  - 持仓 < time_decay_bars → no exit
- [ ] 测试 `_check_regime_change()`
  - Playbook 不再匹配 state → exit
  - Playbook 仍匹配 → no exit
- [ ] 测试 exit 优先级
  - invalidation 优先于 trailing
  - trailing 优先于 time_decay
  - time_decay 优先于 regime_change
- [ ] 集成测试：验证策略引擎使用 exit engine
- [ ] 运行所有测试：`uv run pytest -v`

**验收标准**:
- [ ] 所有测试通过
- [ ] 测试覆盖率 > 80%
- [ ] 优先级逻辑正确

---

### P6.5: 回测验证
**负责人**: [待分配]
**预估**: 1 天

- [ ] 运行 Phase 5 回测，记录 baseline metrics
  - Profit factor
  - Win rate
  - Avg win / Avg loss
- [ ] 运行 Phase 6 回测，记录 new metrics
- [ ] 对比验证：
  - Profit factor 应提升（目标 +5-10%）
  - Win rate 可能略降（更快止损）
  - Avg loss 应减小（更快止损）
- [ ] 如果 profit factor 未提升，调整参数并重新验证

**验收标准**:
- [ ] Profit factor 提升 > 5%
- [ ] Avg loss 减小
- [ ] 无明显 returns 下降

---

### P6.6: 仪表板集成（可选）
**负责人**: [待分配]
**预估**: 0.5 天

- [ ] 在 `zenquant/app/live_state.py` 订阅 exit 事件
- [ ] 在 dashboard snapshot 添加 exit decision
- [ ] 在前端显示：
  - Active exit type
  - Trailing stop level（如果有）
  - Time decay countdown（如果有）
  - Exit urgency

**验收标准**:
- [ ] 仪表板正确显示 exit 信息
- [ ] Trailing stop level 实时更新
- [ ] UI 清晰易读

---

## 质量门禁

每个 Phase 完成后必须通过以下检查：

### 代码质量
- [ ] 所有测试通过（100% pass rate）
- [ ] 无 linting 错误
- [ ] 类型注解完整
- [ ] Docstring 完整

### 架构质量
- [ ] 无循环依赖
- [ ] 清晰的关注点分离
- [ ] 向后兼容性保持
- [ ] 事件发布/订阅正确

### 功能质量
- [ ] 回测验证通过
- [ ] 确定性验证（相同输入 → 相同输出）
- [ ] 审计追踪完整
- [ ] 仪表板可视化正确（如适用）

### 文档质量
- [ ] Phase 完成报告
- [ ] 代码注释完整
- [ ] 架构图更新（如需要）

---

## 进度追踪

| Phase | 状态 | 开始日期 | 完成日期 | 负责人 |
|-------|------|---------|---------|--------|
| Phase 3 | ✅ 完成 | 2026-03-10 | 2026-03-15 | - |
| Phase 4 | ✅ 完成 | 2026-03-15 | 2026-03-15 | - |
| Phase 5 | ⏳ 待开始 | - | - | [待分配] |
| Phase 6 | ⏳ 待开始 | - | - | [待分配] |

---

## 联系人

**量化组长（AI Agent）**: 架构把关、方向把控、进度评估
**程序员大神**: 代码实现、测试编写、回测验证

---

**最后更新**: 2026-03-15
