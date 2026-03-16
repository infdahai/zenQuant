# ZenQuant 开发任务列表

**更新日期**: 2026-03-16
**当前阶段**: Phase 3-6 代码实现完成，重点转向代码质量改进和文档完善

---

## 当前状态

✅ **Phase 3**: State Engine（状态引擎）- 已完成
✅ **Phase 4**: Playbook Engine（策略手册引擎）- 已完成
✅ **Phase 5**: Sizing Engine（仓位引擎）- 已完成
✅ **Phase 6**: Exit Engine（退出引擎）- 已完成
✅ **Deribit Options**: 观测层已实现 (observation-only)
✅ **Frontend Panels**: Exit决策面板、Position可视化 - 已完成

**测试状态**: 64个测试通过，4个跳过（需要外部依赖）

---

## 剩余任务概览

这些是实际需要完成的代码质量改进和文档任务：

### 高优先级 (P0-P1)
1. **策略参数化**: 将硬编码阈值移至配置文件
2. **风险语义统一**: 文档化playbook vs exit的transition_risk差异
3. **方法论文档**: 回测数据来源、费用假设、限制说明
4. **Deribit验证**: API稳定性验证（1-2周观测）

### 中优先级 (P2-P3)
5. **置信度融合研究**: 比较不同置信度混合方法
6. **分析/报告功能**: 按出口类型命中率、按策略手册盈亏比
7. **Dashboard增强**: 时间衰减倒计时、数据流测试（可选）
8. **Coinbase Premium**: 可选观测层（待Deribit评估后）

---

## 详细任务描述

### 1. 策略参数化 (P0)
**目标**: 将硬编码的策略阈值移至配置文件，保持现有默认行为不变
**文件**: 
- zenquant/strategy/playbook_engine.py
- zenquant/strategy/sizing_engine.py  
- zenquant/strategy/exit_engine.py
**实现**: 
- 提取魔法数字到配置对象
- 保持现有默认值完全一致
- 添加测试验证行为未改变

### 2. 风险语义统一 (P0)
**目标**: 决定playbook选择和exit logic是否应共享transition_risk阈值
**文件**:
- zenquant/strategy/playbook_engine.py
- zenquant/strategy/exit_engine.py
**实现**:
- 记录当前故意的差异（如果存在）
- 或统一阈值并添加确认测试
- 添加焦点测试证明playbook去风险到exit去风险的预期交接

### 3. 方法论文档 (P0)
**目标**: 提高审计性而不改变运行时行为
**文件**: 
- README.md
- 新文档 docs/BACKTEST_METHODOLOGY.md
**内容**:
- 数据来源和窗口
- 费用假设（maker/taker，滑点）
- 重播约束和限制
- 已知限制和假设

### 4. Deribit验证 (P1)
**目标**: 验证现有Deribit实现的API行为和数据质量
**文件**:
- tests/test_deribit_api_validation.py (API稳定性测试)
- tests/test_deribit_postgresql.py (持久化测试)  
- scripts/observe_deribit.py (长期观测脚本)
**验收标准**:
- API稳定性测试通过
- 数据质量在合理范围
- block_flow_imbalance明确标记为volume proxy
- PostgreSQL持久化测试通过
- 观测脚本运行1-2周无崩溃

### 5. 置信度融合研究 (P2)
**目标**: 比较当前50/50算术混合与其他方法
**文件**: 
- zenquant/strategy/sizing_engine.py
- tests/test_sizing_engine.py
**范围**:
- 几何平均
- 最小值基础混合
- regime-aware加权
**非目标**: 不要在未经验证的情况下静默更改生产尺寸语义

### 6. 分析/报告功能 (P2)
**目标**: 从现有数据构建有用的事后指标
**位置**: 可能的新模块 zenquant/app 或 zenquant/historical
**指标**:
- 按出口类型的命中率
- 按策略手册的盈亏比
**实现**:
- 构建自现有执行报告和已发布决策
- 保持分析为只读且事后处理
**非目标**: 不要将分析状态混入执行或UI代码路径

### 7. Dashboard增强 (P3)
**目标**: 验证Dashboard WebSocket数据流正确性和添加时间衰减显示
**文件**:
- tests/test_dashboard_data_flow.py
- zenquant/web/app/pages/DashboardPage.js (时间衰减倒计时)
**场景**:
- Dashboard snapshot包含所有决策层
- Sizing breakdown正确暴露
- Exit决策正确暴露
- WebSocket推送正常工作
- 计算剩余bars (time_decay_bars - bars_held)
- 显示倒计时进度条
- 接近老化时预警

### 8. Coinbase Premium (P3)
**前置条件**: 仅在Deribit观测层价值验证通过后考虑
**目标**: 添加可选观测层
**文件**: 待定
**内容**: 
- Coinbase Premium数据源验证
- CoinbasePremiumMonitor实现
- Dashboard显示

---

## 执行顺序建议

### 即时行动 (本周)
1. **策略参数化** - 开始将硬编码阈值移至配置
2. **方法论文档** - 记录回测假设和限制

### 短期 (1-2周)
3. **风险语义统一** - 决定并记录transition_risk语义
4. **Deribit验证启动** - 开始1-2周的API观测
5. **置信度融合研究** - 开始比较不同混合方法

### 中期 (3-4周)
6. **分析/报告功能** - 构建事后指标模块
7. **Dashboard增强** - 添加时间衰减和数据流测试

### 长期 (可选)
8. **Coinbase Premium** - 仅在Deribit评估通过后考虑

---

## 质量门禁

每个任务完成后必须通过:
- [ ] 所有测试通过 (100% pass rate)
- [ ] 无linting错误
- [ ] 代码审查通过
- [ ] 文档更新完成

---

## 进度追踪

| 任务 | 状态 | 开始日期 | 完成日期 | 负责人 |
|------|------|---------|---------|--------|
| Phase 3-6 | ✅ 完成 | 2026-03-10 | 2026-03-16 | 顶级工程师 |
| Deribit观测层 | ✅ 完成 | 2026-03-15 | 2026-03-16 | 顶级工程师 |
| 前端面板 | ✅ 完成 | 2026-03-10 | 2026-03-16 | 前端工程师 |
| 策略参数化 | ⏳ 待开始 | - | - | [待分配] |
| 风险语义统一 | ⏳ 待开始 | - | - | [待分配] |
| 方法论文档 | ⏳ 待开始 | - | - | [待分配] |
| Deribit验证 | ⏳ 待开始 | - | - | [待分配] |
| 置信度融合研究 | ⏳ 待开始 | - | - | [待分配] |
| 分析/报告功能 | ⏳ 待开始 | - | - | [待分配] |
| Dashboard增强 | ⏳ 待开始 | - | - | [待分配] |
| Coinbase Premium | ⏳ 待开始 | - | - | [待分配] |

---

## 联系人

**量化组长（AI架构师）**: 架构把关、方向把控、进度评估
**程序员大神**: 代码实现、测试编写、前端开发

---

**最后更新**: 2026-03-16