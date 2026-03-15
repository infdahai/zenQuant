# ZenQuant 开发任务列表

**更新日期**: 2026-03-16
**当前阶段**: Phase 3-6 代码实现完成，剩余测试补全和前端修复

---

## 当前状态

✅ **Phase 3**: State Engine（状态引擎）- 已完成
✅ **Phase 4**: Playbook Engine（策略手册引擎）- 已完成
✅ **Phase 5**: Sizing Engine（仓位引擎）- 已完成
✅ **Phase 6**: Exit Engine（退出引擎）- 已完成
✅ **Deribit Options**: 观测层已实现 (observation-only)

**测试状态**: 59个测试通过，4个跳过

---

## 剩余任务概览

### 高优先级 (P0-P1)
1. **测试补全**: E2E测试、Live模式测试、PostgreSQL测试
2. **前端修复**: Exit决策面板、Position可视化
3. **Deribit验证**: API稳定性验证、长期观测

### 中优先级 (P2-P3)
4. **Dashboard增强**: Time decay倒计时、数据流测试
5. **Coinbase Premium**: 可选观测层（待评估）

---

## Task T1: E2E决策链路测试

**负责人**: [待分配]
**预估**: 2天
**优先级**: P0

**目标**: 验证完整决策链路的确定性和正确性

**实现文件**: `tests/test_e2e_decision_chain.py`

**测试场景**:
- [ ] 完整决策链路 (State → Playbook → Sizing → Exit → Target)
- [ ] 1:1对应关系验证 (每个indicator产生一个完整决策链)
- [ ] Cutoff time一致性验证
- [ ] Exit触发reduce-only target验证
- [ ] 多symbol并发测试

**验收标准**:
- [ ] 所有E2E测试通过
- [ ] 决策链路完整性验证通过
- [ ] 时间一致性验证通过

---

## Task T2: Live模式集成测试

**负责人**: [待分配]
**预估**: 1.5天
**优先级**: P0

**目标**: 验证live模式下的事件流和bootstrap正确性

**实现文件**: `tests/test_live_mode_integration.py`

**测试场景**:
- [ ] Bootstrap历史数据加载
- [ ] Prime signal正确发布
- [ ] 1D bias正确生成
- [ ] WebSocket reconnect处理
- [ ] 异常场景处理 (网络断开、数据缺失)

**验收标准**:
- [ ] Bootstrap测试通过
- [ ] Prime signal测试通过
- [ ] Reconnect测试通过

---

## Task T3: PostgreSQL持久化测试

**负责人**: [待分配]
**预估**: 1天
**优先级**: P1

**目标**: 验证PostgreSQL审计追踪完整性

**实现文件**: `tests/test_postgresql_persistence.py`

**测试场景**:
- [ ] 所有决策层正确写入 (State/Playbook/Sizing/Exit)
- [ ] Cutoff time一致性
- [ ] 数据完整性验证
- [ ] 历史查询功能

**前置条件**:
```bash
export ZENQUANT_TEST_PG_DSN="postgresql://localhost/zenquant_test"
createdb zenquant_test
```

**验收标准**:
- [ ] 所有表正确写入
- [ ] 数据完整性验证通过
- [ ] 历史查询测试通过

---

## Task F1: Exit决策面板

**负责人**: [待分配]
**预估**: 1天
**优先级**: P0

**目标**: 在Dashboard显示Exit决策和Position状态

**修改文件**: `zenquant/web/app/pages/DashboardPage.js`

**实现内容**:
- [ ] Position状态显示 (方向、数量、入场价、持仓时间)
- [ ] Unrealized PnL显示
- [ ] Trailing stop显示
- [ ] Exit决策显示 (类型、紧急度、原因)
- [ ] Exit reason codes显示

**验收标准**:
- [ ] Position状态正确显示
- [ ] PnL实时更新
- [ ] Exit决策正确显示
- [ ] UI清晰易读

---

## Task F2: Position可视化增强

**负责人**: [待分配]
**预估**: 1天
**优先级**: P1

**目标**: 在Price Curve上叠加Position和Trailing Stop

**修改文件**: `zenquant/web/app/components/CandleCanvas.js`

**实现内容**:
- [ ] Entry price水平线 (Long绿色/Short橙色)
- [ ] Trailing stop水平线 (红色虚线)
- [ ] 价格标签显示
- [ ] Long/Short颜色区分

**验收标准**:
- [ ] Entry line正确显示
- [ ] Trailing stop line正确显示
- [ ] 颜色编码清晰
- [ ] 标签可读

---

## Task D1: Deribit API验证

**负责人**: [待分配]
**预估**: 1天
**优先级**: P2

**目标**: 验证现有Deribit实现的API行为和数据质量

**实现文件**:
- `tests/test_deribit_api_validation.py` (API稳定性测试)
- `tests/test_deribit_postgresql.py` (持久化测试)
- `scripts/observe_deribit.py` (长期观测脚本)

**测试场景**:
- [ ] API可用性和响应结构
- [ ] 数据质量和合理范围
- [ ] Quality flags正确触发
- [ ] PostgreSQL持久化验证
- [ ] 长期观测 (1-2周后台运行)

**验收标准**:
- [ ] API稳定性测试通过
- [ ] 数据质量在合理范围
- [ ] `block_flow_imbalance`明确标记为volume proxy
- [ ] PostgreSQL持久化测试通过
- [ ] 观测脚本运行1-2周无崩溃

**观测期后决策**:
- 评估Deribit数据价值
- 决定是否集成到Sizing/Exit层

---

## Task T4: Dashboard数据流测试 (可选)

**负责人**: [待分配]
**预估**: 1天
**优先级**: P3

**目标**: 验证Dashboard WebSocket数据流正确性

**实现文件**: `tests/test_dashboard_data_flow.py`

**测试场景**:
- [ ] Dashboard snapshot包含所有决策层
- [ ] Sizing breakdown正确暴露
- [ ] Exit决策正确暴露
- [ ] WebSocket推送正常工作

---

## Task F3: Time Decay倒计时 (可选)

**负责人**: [待分配]
**预估**: 0.5天
**优先级**: P3

**目标**: 显示持仓老化倒计时

**修改文件**: `zenquant/web/app/pages/DashboardPage.js`

**实现内容**:
- [ ] 计算剩余bars (time_decay_bars - bars_held)
- [ ] 显示倒计时进度条
- [ ] 接近老化时预警

---

## Task C1-C3: Coinbase Premium观测层 (可选)

**负责人**: [待分配]
**预估**: 2天
**优先级**: P3

**前置条件**: Deribit观测层价值验证通过

**实现内容**:
- [ ] Coinbase Premium数据源验证
- [ ] CoinbasePremiumMonitor实现
- [ ] Dashboard显示

**决策点**: 仅在Deribit观测层验证通过后考虑

---

## 执行顺序建议

### 第一周 (本周)
1. **Task T1**: E2E决策链路测试 (2天)
2. **Task F1**: Exit决策面板 (1天)
3. **Task T2**: Live模式集成测试 (1.5天)

### 第二周
4. **Task T3**: PostgreSQL持久化测试 (1天)
5. **Task F2**: Position可视化增强 (1天)
6. **Task D1**: Deribit API验证 (1天)
7. 启动Deribit观测脚本 (后台运行1-2周)

### 第三周 (可选)
8. **Task T4**: Dashboard数据流测试
9. **Task F3**: Time Decay倒计时
10. 评估Deribit数据价值

### 第四周 (可选)
11. **Task C1-C3**: Coinbase Premium (如果Deribit验证通过)

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
| Deribit实现 | ✅ 完成 | 2026-03-15 | 2026-03-16 | 顶级工程师 |
| Task T1 | ⏳ 待开始 | - | - | [待分配] |
| Task T2 | ⏳ 待开始 | - | - | [待分配] |
| Task T3 | ⏳ 待开始 | - | - | [待分配] |
| Task F1 | ⏳ 待开始 | - | - | [待分配] |
| Task F2 | ⏳ 待开始 | - | - | [待分配] |
| Task D1 | ⏳ 待开始 | - | - | [待分配] |

---

## 联系人

**量化组长（AI架构师）**: 架构把关、方向把控、进度评估
**程序员大神**: 代码实现、测试编写、前端开发

---

**最后更新**: 2026-03-16
