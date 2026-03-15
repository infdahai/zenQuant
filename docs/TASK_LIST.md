# ZenQuant 开发任务列表

**更新日期**: 2026-03-16  
**当前阶段**: Task 0 已完成本机 smoke 验证，Phase 6 核心已完成，Deribit Stage 1 观察层已接入，剩余 PostgreSQL live 验证与长期观测

---

## 当前状态

✅ **Phase 3**: State Engine（状态引擎）- 已完成  
✅ **Phase 4**: Playbook Engine（策略手册引擎）- 已完成  
✅ **Phase 5**: Sizing Engine（仓位引擎）- 已完成  
⏳ **Task 0**: 模拟盘启动验证 - 本机 smoke 已完成，PostgreSQL 部分待补  
✅ **Phase 6**: Exit Engine（退出引擎）- 核心完成  
✅ **Phase 6 后可选**: Deribit Options 风险温度计 Stage 1 - 已实现为 observation-only

---

## Task 0: 模拟盘启动验证

**负责人**: [待分配]  
**预估**: 0.5 天  
**优先级**: P0（最高）

**目标**: 验证 live 模式下完整链路可用性，并为长期后台运行做第一次真实检查。

### 执行步骤

- [ ] 设置 PostgreSQL 数据库
  - [x] `createdb zenquant_live`
  - [x] 设置 `ZENQUANT_PG_DSN`
  - [x] 设置 `ZENQUANT_RUNTIME_MODE=live`
- [x] 启动 live 模式
  - [x] `uv run zenquant-web --mode live --host 127.0.0.1 --port 8090`
- [ ] 验证基础设施检查清单
  - [x] Binance WebSocket 连接成功
  - [ ] 4H/1D bar-close 事件正常触发
  - [x] Indicator Engine 输出正常
  - [x] State Engine 发布 `MarketState`
  - [x] Playbook Engine 发布 `PlaybookSelection`
  - [x] Sizing Engine 发布 `SizingDecision`
  - [x] Strategy Engine 发布 `TargetPosition`
  - [x] PostgreSQL 写入成功
  - [x] Dashboard 显示实时数据
  - [x] Bootstrap 历史数据加载成功
- [ ] 监控关键指标
  - [x] `database.writes > 0`
  - [x] `database.dropped = 0`
  - [x] `bootstrap_phase = "ready"`
  - [x] `market_connected = true`
  - [x] `last_market_event < 5 分钟`
  - [x] 已提供 `zenquant-live-validate` 命令用于 smoke / soak / bar-close 观测

### 验收标准

- [ ] Live 模式连续运行 24 小时无崩溃
- [ ] 至少捕获 1 个 4H bar close 和 1 个 1D bar close
- [ ] PostgreSQL 所有核心表都有数据
- [x] Dashboard 实时更新正常

### 交付物

- [ ] 运行日志截图
- [x] PostgreSQL 数据样本查询结果
- [x] Dashboard 截图（展示 state / playbook / sizing）

### 本机当前验证结果（2026-03-15）

- [x] `/healthz` 返回 `status=ok`
- [x] `/api/state?symbol=BTCUSDT_PERP&tf=4h` 返回完整 4H 决策链
  - [x] `latest_indicator`
  - [x] `market_state`
  - [x] `active_playbook`
  - [x] `latest_sizing`
  - [x] `latest_target`
- [x] `/api/state?symbol=BTCUSDT_PERP&tf=1d` 返回 1D bootstrap 上下文
  - [x] `latest_indicator`
  - [x] `market_state`
  - [x] `active_playbook`
  - [x] `directional_bias`
- [x] 连续两次采样中 `last_price` 和 `last_market_event_ns` 均前进，说明 live feed 正在更新
- [x] Dashboard 截图已生成：`.artifacts/task0-live-dashboard.png`
- [x] PostgreSQL 路径已验证
  - 使用 Homebrew 安装 `postgresql@17`
  - 本地验证库：`postgresql://zenquant@127.0.0.1:5433/zenquant_live`
  - `zenquant-live-validate --require-db` 已通过
- [x] 已提供运行期验证命令：
  - `uv run zenquant-live-validate --base-url http://127.0.0.1:8090`
  - `uv run zenquant-live-validate --base-url http://127.0.0.1:8090 --require-db --watch-seconds 86400 --output .artifacts/live-24h-summary.json`
- [ ] 24 小时稳定性与真实下一根 4H / 1D live close 仍待继续观测
  - 已启动：`uv run zenquant-live-validate --base-url http://127.0.0.1:8093 --require-db --watch-seconds 86400 --poll-interval-s 60 --output .artifacts/live-24h-summary.json`

---

## Phase 5: Sizing Engine 完成记录

### P5.1: 设计 `SizingDecision` 域对象

**负责人**: 顶级工程师  
**状态**: 已完成

- [x] 在 `zenquant/domain/models.py` 定义 `SizingDecision` dataclass
- [x] 添加 `base_size`, `confidence_multiplier`, `stress_discount`, `execution_haircut`, `final_leverage`
- [x] 添加 `reason_codes`
- [x] 添加 `breakdown()` 方法
- [x] 更新 `zenquant/domain/__init__.py` 导出
- [x] 类型注解完整
- [x] Docstring 完整

### P5.2: 实现 `SizingEngine`

**负责人**: 顶级工程师  
**状态**: 已完成

- [x] 创建 `zenquant/strategy/sizing_engine.py`
- [x] 实现 `_base_size(equity, realized_vol, target_vol)`
- [x] 实现 `_confidence_multiplier(state, playbook)`
- [x] 实现 `_stress_discount(state, indicators)`
- [x] 实现 `_execution_haircut(state)`
- [x] 实现 `compute(state, playbook, equity, indicators)`
- [x] 更新 `zenquant/strategy/__init__.py` 导出
- [x] 代码编译通过
- [x] 类型注解完整
- [x] Docstring 完整

### P5.3: 集成到策略引擎

**负责人**: 顶级工程师  
**状态**: 已完成

- [x] `ARCStrategyEngine.__init__()` 实例化 `SizingEngine`
- [x] `_build_target()` 调用 `sizing_engine.compute()`
- [x] 移除内联 sizing 逻辑
- [x] 发布 `strategy.sizing.{tf}.{symbol}` 事件
- [x] `TargetPosition.reason_codes` 包含 sizing reason codes
- [x] 保持回测 / replay 合同不变

### P5.4: 测试

**负责人**: 顶级工程师  
**状态**: 已完成

- [x] 创建 `tests/test_sizing_engine.py`
- [x] 覆盖 `_base_size()` 场景
- [x] 覆盖 `_confidence_multiplier()` 场景
- [x] 覆盖 `_stress_discount()` 场景
- [x] 覆盖 `_execution_haircut()` 场景
- [x] 覆盖 `compute()` 主流程
- [x] 增加策略集成测试，验证 sizing 事件发布
- [x] 增加 dashboard state 测试，验证 sizing snapshot 暴露
- [x] `uv run pytest -q` 通过

### P5.5: 回测验证

**负责人**: 顶级工程师  
**状态**: 已完成

- [x] 保留 Phase 4 / Phase 5 固定历史数据集基线
- [x] Returns 差异 < 0.5%
- [x] 其他指标一致
- [x] 无明显性能退化

**当前固定基线**:

- [x] `net_pnl = 236412.8`
- [x] `final_equity = 336412.80249300046`
- [x] `max_drawdown = 0.008591`
- [x] `sharpe = 4.7556`
- [x] `profit_factor = 4.719191`
- [x] `trade_count = 420`

### P5.6: Dashboard 与 PostgreSQL 集成

**负责人**: 顶级工程师  
**状态**: 已完成

- [x] `zenquant/app/live_state.py` 订阅 sizing 事件
- [x] dashboard snapshot 暴露 `latest_sizing`
- [x] 前端显示 sizing breakdown
- [x] 显示 Base size
- [x] 显示 Confidence multiplier
- [x] 显示 Stress discount
- [x] 显示 Execution haircut
- [x] 显示 Final leverage
- [x] PostgreSQL 新增 `sizing_decisions` 表并写入数据

### P5.7: 模拟盘前集成验收

**负责人**: [待分配]  
**状态**: 待执行  
**优先级**: P1

- [x] 代码审查确认 sizing 已完整接入 live 路径
- [ ] 运行 demo 模式，观察 sizing 事件发布
- [x] 运行 live 模式，观察 sizing 事件发布
- [x] Dashboard 正确显示 `base_size / confidence_multiplier / stress_discount / execution_haircut`
- [ ] PostgreSQL `sizing_decisions` 表有真实数据

### Phase 5 当前结论

- [x] Phase 5 已在代码层完成
- [ ] Phase 5 仍需通过 Task 0 的真实 live 运行验收

---

## Phase 6: Exit Engine 实施任务清单

### P6.1: 设计 `ExitDecision` 域对象

**负责人**: 顶级工程师  
**预估**: 0.5 天  
**优先级**: P1

- [x] 在 `zenquant/domain/models.py` 定义 `ExitDecision` dataclass
- [x] 字段包含 `exit_type`
  - `NONE | TRAILING | INVALIDATION | TIME_DECAY | REGIME_CHANGE`
- [x] 字段包含 `exit_urgency`
  - `NONE | GRADUAL | IMMEDIATE`
- [x] 添加 `target_reduction_pct`
- [x] 添加 `stop_price`
- [x] 添加 `reason_code`, `reason_detail`, `reason_codes`
- [x] 添加 `should_exit()` 方法
- [x] 添加 `is_immediate()` 方法
- [x] 更新 `zenquant/domain/__init__.py` 导出
- [x] 类型注解完整
- [x] Docstring 完整

### P6.2: 实现 `ExitEngine` 模块

**负责人**: 顶级工程师  
**预估**: 2.5 天  
**优先级**: P1

- [x] 创建 `zenquant/strategy/exit_engine.py`
- [x] 实现 `ExitEngineConfig`
- [x] 实现 `check(position, state, playbook, indicators)`
- [x] 实现 `_check_invalidation(position, state, playbook)`
- [x] 实现 `_check_trailing_stop(position, indicators, playbook)`
- [x] 实现 `_check_time_decay(position, playbook, indicators)`
- [x] 实现 `_check_regime_change(position, state, playbook)`
- [x] 实现 `_no_exit(position)` helper
- [x] 按优先级执行
  - [x] invalidation
  - [x] trailing
  - [x] time decay
  - [x] regime change
- [x] 更新 `zenquant/strategy/__init__.py` 导出
- [x] 代码编译通过
- [x] Docstring 完整

### P6.3: 集成 `ExitEngine` 到策略引擎

**负责人**: 顶级工程师  
**预估**: 1.5 天  
**优先级**: P1

- [x] 在 `ARCStrategyEngine.__init__()` 实例化 `ExitEngine`
- [x] 在 `_build_target()` 中优先调用 exit engine
- [x] 发布 `strategy.exit.{tf}.{symbol}` 事件
- [x] 构建 reduce-only exit target
- [x] 移除内联 trailing-stop exit 逻辑
- [x] 确保 `TargetPosition.reason_codes` 透传 exit reason codes
- [x] Demo 模式下可观察到 exit 决策

### P6.4: `ExitEngine` 测试

**负责人**: 顶级工程师  
**预估**: 2 天  
**优先级**: P1

- [x] 创建 `tests/test_exit_engine.py`
- [x] 覆盖 bull -> downtrend invalidation
- [x] 覆盖 bear -> uptrend invalidation
- [x] 覆盖 long trailing stop breach
- [x] 覆盖 short trailing stop breach
- [x] 覆盖 time decay unprofitable
- [x] 覆盖 time decay profitable no-exit
- [x] 覆盖 volatility distorted regime change
- [x] 覆盖 quality unsafe regime change
- [x] 覆盖优先级测试
  - [x] invalidation 优先于 trailing
- [x] 覆盖 healthy path no-exit
- [x] 测试覆盖率 > 80%

### P6.5: 回测验证

**负责人**: 顶级工程师  
**预估**: 1 天  
**优先级**: P2

- [x] 记录 Phase 5 baseline metrics
- [x] 运行 Phase 6 回测
- [x] 对比 Profit factor
- [ ] 对比 Avg loss
- [x] 对比 Max drawdown
- [x] 对比 Sharpe ratio
- [x] 确认无明显 returns 下降

**目标方向**:

- [x] Profit factor 提升 > 5%
- [ ] Avg loss 减小
- [x] Max drawdown 减小或持平
- [x] Sharpe ratio 提升

**当前固定基线（Phase 6）**:

- [x] `net_pnl = 236863.54`
- [x] `final_equity = 336863.5367195884`
- [x] `max_drawdown = 0.007263`
- [x] `sharpe = 4.7603`
- [x] `profit_factor = 5.701303`
- [x] `trade_count = 422`

### P6.6: Dashboard 集成（可选）

**负责人**: 顶级工程师  
**预估**: 0.5 天  
**优先级**: P3

- [x] `zenquant/infra/db.py` 订阅并持久化 exit 决策
- [x] `zenquant/app/live_state.py` 订阅 exit 事件
- [x] Dashboard 显示 active exit type
- [x] Dashboard 显示 trailing stop level
- [ ] Dashboard 显示 time decay countdown
- [x] Dashboard 显示 exit urgency
- [x] Dashboard 显示 exit reason codes

---

## Phase 6 后可选: Deribit Options 风险温度计

### P6+O.1: 设计 `DeribitRiskPack` 域对象

**负责人**: 顶级工程师  
**预估**: 0.5 天  
**优先级**: P3

- [x] 在 `zenquant/domain/models.py` 定义 `DeribitRiskPack`
- [x] 包含 `atm_iv_30d`
- [x] 包含 `term_structure_slope`
- [x] 包含 `put_call_skew_25d`
- [x] 包含 `oi_concentration`
- [x] 包含 `block_flow_imbalance`
- [x] 明确仅用于风险温度计，不参与方向信号

### P6+O.2: 实现 `DeribitOptionsIngestor`

**负责人**: 顶级工程师  
**预估**: 2 天  
**优先级**: P3

- [x] 创建 `zenquant/ingestor/deribit_options.py`
- [x] 拉取 BTC options 数据
- [x] 生成 `DeribitRiskPack`
- [x] 发布到 event bus
- [x] 仅用于 risk assessment

### P6+O.3: Dashboard / PostgreSQL / Runtime 集成

**负责人**: 顶级工程师  
**预估**: 0.5 天  
**优先级**: P3

- [x] live runtime 支持通过 `ZENQUANT_DERIBIT_ENABLED=1` 启动 observation layer
- [x] `zenquant/app/live_state.py` 暴露 `deribit_risk` 与会话历史
- [x] Dashboard 显示 Options Thermometer 面板
- [x] PostgreSQL 新增 `deribit_risk_packs` 表
- [x] `zenquant/infra/db.py` 持久化 Deribit 风险包
- [x] 保持策略 / 风控 / 执行逻辑不受 Deribit 输入影响
- [x] 增加 `tests/test_deribit_options.py`

### P6+O.4: 当前限制

- [x] Stage 1 仅为 observation-only，不改变 target / risk / execution
- [x] `block_flow_imbalance` 当前使用公开 `volume_usd` 代理，不是逐笔 block trade feed
- [ ] 24h live 稳定性验证
- [ ] PostgreSQL live 写入验收
- [ ] 决定是否推进 Stage 2 风险覆盖层集成

---

## 当前执行顺序

1. **先执行 Task 0**：启动模拟盘，验证 live 链路  
2. **确认 Phase 5 在 live 中稳定**：特别是 sizing / dashboard / PostgreSQL  
3. **再启动 Phase 6**：Exit Engine  
4. **Deribit 已作为 observation-only 接入**：在 Task 0 / Phase 6 live 验证稳定后，再决定是否推进 Stage 2

---

## 备注

- Phase 5 的代码实现已完成，但还缺少真实 live 运行验收。
- 当前最重要的不是继续加新模块，而是先把模拟盘链路跑稳定。
- Phase 6 必须保持 determinism、auditability、live/backtest parity，不允许为了“更聪明”而破坏这些约束。
- Deribit 当前只用于观测层，不参与方向信号，也不改写 risk / execution。
