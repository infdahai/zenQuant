# Live Validation Handoff Spec

本文件是当前版本唯一的 tester / operator 交付规格。

目标只有一个：

- 开发负责人完成当前功能、验证与运行闭环
- 测试负责人按本文件执行并记录结果
- 测试通过后，系统可直接进入 `paper-live`

本文件是测试负责人与 `paper-live` 操作人的唯一入口。研究型文档仅作内部参考，不作为交付依据。

---

## 1. 当前交付状态

### 1.1 当前主策略

- 资产：`BTC`
- 交易标的：`BTCUSDT_PERP`
- 决策周期：`4h` bar close
- 大周期偏置：`1d`
- 执行哲学：PaulWei 风格 execution/risk baseline

### 1.2 当前已完成能力

- `current` vs `paulwei_baseline` 历史对照框架已稳定可跑
- `2023-08` bearish-grind 修复已落地并有回归测试
- `cycle_context` 已进入历史研究输出
- `liq_state_overlay` 与 `liq_risk_guard` 已接入，但当前保持 `research-gated`
- `paper-live` 所需的运行、验证、导出入口均已存在

### 1.3 当前边界

- `current` 与 `paulwei_baseline` 在当前真实历史路径上仍可能一致
- 这意味着 comparison framework 已验证，但尚未证明收益层面已稳定优于 PaulWei
- liquidation 当前不是 promoted alpha
- 若无可用历史 liquidation 数据，研究结果必须输出 `insufficient_data`

### 1.4 当前历史结论

权威历史记录：[`HISTORICAL_VALIDATION_2026_04_07.md`](/Users/disaster/workspace/code/zenQuant/docs/HISTORICAL_VALIDATION_2026_04_07.md)

当前用于交付判断的历史结论：

- `2023-08` 不再是已知 failure
- walk-forward：`8/8` 窗口无 overfit warning，Avg OOS ratio `1.25`
- historical adversarial：`5/5` worst periods 通过
- comparison framework 已稳定输出 `comparison.json` / `comparison.md`
- liquidation research 当前结论必须是：
  - `data_ready=false` 时为 `insufficient_data`
  - 不得假阳性 promote

### 1.5 当前是否可进入 `paper-live`

结论：**可以进入 `paper-live`，但不等于已经完成 paper-live validation。**

进入依据：

- 历史主策略门禁已具备可接受稳定性
- 风险、回放一致性、审计链路已存在
- liquidation 研究不作为本轮 blocker

---

## 2. 测试负责人执行说明

### 2.1 必跑测试

```bash
uv run pytest tests/test_variant_comparison.py tests/test_202308_bearish_grind.py tests/test_historical_pipeline.py -q --tb=short
uv run pytest tests/test_golden_output.py tests/test_backtest_invariants.py tests/test_parity.py tests/test_execution_router.py tests/test_execution_sensitivity.py tests/test_scenario_risk.py -q --tb=short
uv run pytest tests/test_live_validate.py tests/test_live_export.py tests/test_live_export_cli.py tests/test_live_bootstrap.py -q --tb=short
uv run pytest tests/test_liquidation_store.py tests/test_liquidation_signal_integration.py tests/test_liquidation_research_variants.py tests/test_liquidation_pipeline.py -q --tb=short
```

通过标准：

- 全部通过
- 不允许 golden / parity / invariant / execution-risk 回退
- `2023-08` 修复相关测试不得回退
- liquidation 测试若通过，其含义是 `research-gated + fail-closed` 正常，不代表 alpha 已验证

### 2.2 必跑脚本级验证

```bash
uv run python scripts/compare_variants.py --root data/raw/binance --symbol BTCUSDT --output-dir /tmp/zenquant-variant-compare
uv run python scripts/walk_forward_eval.py
uv run python scripts/adversarial_stress.py --historical
uv run python scripts/research_liquidation.py --root data/raw/binance --symbol BTCUSDT --output-dir /tmp/zenquant-liquidation-research --skip-walk-forward --include-worst-periods
```

通过标准：

- `compare_variants.py` 生成 `comparison.json` 和 `comparison.md`
- `walk_forward_eval.py` 无明显 overfit warning
- `adversarial_stress.py --historical` 不出现新增灾难性失败
- `research_liquidation.py`：
  - 若有数据，必须输出 readiness 与 verdict
  - 若缺数据，必须显式输出 `insufficient_data`
  - 不允许输出假 `promotion_candidate=true`

### 2.3 Stop-Ship 条件

出现以下任一情况，测试负责人应停止交付，不进入 `paper-live`：

- `test_backtest_invariants.py` 失败
- `test_parity.py` 失败
- `test_execution_router.py` / `test_execution_sensitivity.py` / `test_scenario_risk.py` 失败
- `2023-08` 修复回退
- walk-forward 出现明显 train/test 崩塌
- historical adversarial 出现新增重大失败
- live validation / live export CLI 入口不可用

### 2.4 结果记录要求

测试负责人至少记录：

- 提交哈希
- 测试命令与通过结果
- 脚本级验证结果
- 是否满足进入 `paper-live` 条件
- 若 blocked，必须写明 blocker 名称

---

## 3. 输出契约与结论口径

### 3.1 `comparison.json`

关键必须字段：

- `variants`
- `window_reports`
- `validation_reports`

最低断言：

- `variants[].name` 包含 `current` 与 `paulwei_baseline`
- `window_reports[].cycle_context` 存在
- `window_reports[].metrics.sharpe` 存在
- `validation_reports[].avg_oos_ratio` 存在

### 3.2 `research_liquidation` 输出

必须包含：

- `liquidation_readiness`
- `research_verdict`

若真实历史 liquidation 数据不存在或全为零：

- `liquidation_readiness.data_ready = false`
- `research_verdict.blocked_by = "missing_liquidation_history"` 或等价字段
- `promotion_candidate = false`

### 3.3 对外口径

当前允许对外或对内部测试口径使用的表述：

- 主策略已达到可进入 `paper-live` 的历史与鲁棒性门槛
- 当前尚未证明收益层面稳定优于 `paulwei_baseline`
- liquidation 已完成研究型接入，但尚未完成有效性验证

不允许的表述：

- liquidation alpha 已被证明
- paper trading 已完成
- 当前版本已可直接进入真资金实盘

---

## 4. `paper-live` 启动说明

### 4.1 前置条件

```bash
export ZENQUANT_PG_DSN="postgresql://localhost/zenquant_live"
createdb zenquant_live
```

前置要求：

- PostgreSQL 可连接
- `ZENQUANT_PG_DSN` 已设置
- `data/raw/binance` 历史数据目录存在
- 审计输出允许写入数据库

### 4.2 启动命令

唯一推荐命令：

```bash
uv run zenquant-web --host 127.0.0.1 --port 8090 --mode live
```

### 4.3 启动后即时验证

```bash
curl http://127.0.0.1:8090/healthz
uv run zenquant-live-validate --base-url http://127.0.0.1:8090 --require-db
uv run zenquant-live-scorecard --base-url http://127.0.0.1:8090 --require-db
```

通过标准：

- `/healthz` 返回 `status: ok`
- `zenquant-live-validate` 返回 `ok: true`
- 运行状态显示：
  - `market_connected = true`
  - `indicator_4h = true`
  - `state_4h = true`
  - `playbook_4h = true`
  - `target_4h = true`
- `database_connected = true`
- `database_writes > 0`
- scorecard 已生成并落盘到 `results/paper_live_scorecards/`

### 4.4 运行中健康检查

健康运行至少要满足：

- 市场数据不断流
- bar close 持续推进
- intent / target 持续生成
- 风险快照持续写入
- 审计写入无 dropped writes
- 前端 `Paper-Live Scorecard` 面板与 CLI 输出一致

### 4.5 导出与复盘

正式导出命令：

```bash
uv run python scripts/export_live_training_data.py --dsn "$ZENQUANT_PG_DSN" --symbol BTCUSDT_PERP --label paper-live
```

或等价入口：

```bash
uv run zenquant-live-export --dsn "$ZENQUANT_PG_DSN" --symbol BTCUSDT_PERP --label paper-live
```

正式 review 完成后，记录一条 scorecard review：

```bash
uv run zenquant-live-scorecard \
  --base-url http://127.0.0.1:8090 \
  --require-db \
  --record-review PASS \
  --review-summary "weekly parity / execution / risk review passed" \
  --review-artifact results/live_training_runs/<run_id>/report.md
```

导出产物必须用于后续 review，至少覆盖：

- live/backtest parity
- playbook distribution
- risk events
- execution anomalies
- quality flags

---

## 5. Promote / Stop 规则

### 5.1 测试通过后何时允许直接启动 `paper-live`

同时满足以下条件即可直接启动：

- 第 2 节所有关键测试通过
- 第 2 节脚本级验证通过
- live runtime 与 live validation 入口可用
- 本文档所列启动命令、导出命令和停机规则均已可执行

### 5.2 运行中必须停止的情况

出现以下情况必须停止 `paper-live` 并回到研发/排障：

- `/healthz` 非 `ok`
- 市场数据断流或持续 stale
- 审计数据库掉线或持续无 writes
- `database_dropped_writes > 0`
- 连续缺失 4H state / target / sizing / playbook
- 关键质量标志异常持续不恢复

### 5.3 何时才可声称 paper trading 完成

只有同时满足以下条件：

- 连续稳定运行满 `2 weeks`
- 无崩溃
- bar close、决策、审计均持续正常
- 至少完成一次正式 `paper-live` 导出
- 至少完成一次正式 `paper-live` review

在满足这些条件前，只能表述为：

- `paper-live started`
- `paper trading in progress`

不能表述为：

- `paper trading complete`
- `live incremental validation complete`

### 5.4 真仓 promote 条件

本轮不进入真仓，但后续讨论真仓前至少要求：

- paper-live 完整通过
- parity / execution / risk review 无重大异常
- live review 证明系统行为与历史验证框架一致

---

## 6. 为什么当前版本值得进入 `paper-live`

不是因为当前已经证明收益碾压 `paulwei_baseline`，而是因为：

- 状态、风险、执行链路更可解释
- replay / backtest / live 一致性约束更完整
- 审计与导出链路已具备
- 已知历史弱窗口已有定向修复
- 防过拟合门槛已经内建到测试和验证体系中

当前防过拟合依赖的不是单一回测，而是：

- walk-forward OOS
- historical adversarial
- point-in-time correctness
- replay/live parity
- golden + invariants + parity regression
- 缺失数据 fail-closed

因此本轮交付的含义是：

- 系统已达到值得做 `paper-live` authoritative validation 的工程成熟度
- 不是说所有研究问题都已回答完毕
