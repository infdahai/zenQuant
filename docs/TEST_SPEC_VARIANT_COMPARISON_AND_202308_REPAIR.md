# 测试规格：Variant Comparison 与 2023-08 修复

## 1. 目的

本文档是当前已完成交付的测试规格，供测试负责人独立执行封板测试与回归测试使用。

本文档只描述已经落地并应被验证的能力，不描述未来 roadmap，不要求测试负责人自行发散研究。

当前测试对象固定为：

- `current` 当前默认策略行为
- `paulwei_baseline` 固定执行/风控基线
- `2023-08` 慢跌误判修复
- 历史回放输出中的 `cycle_context`

## 2. 当前已完成能力

### 2.1 历史回放与对照框架

当前仓库已经支持在历史回放中注入执行/风控配置，用于同一路径上的 deterministic variant comparison。

涉及入口：

- [comparison.py](/Users/disaster/workspace/code/zenQuant/zenquant/research/comparison.py)
- [compare_variants.py](/Users/disaster/workspace/code/zenQuant/scripts/compare_variants.py)
- [replay.py](/Users/disaster/workspace/code/zenQuant/zenquant/historical/replay.py)

当前内建 variant 固定为：

- `current`
- `paulwei_baseline`

注意：

- `PaulWei` 在当前仓库中定义为执行/风控基线，不是独立信号策略。
- 当前阶段不要求存在第二套独立的策略引擎。

### 2.2 2023-08 慢跌修复

当前仓库已落地如下最小修复：

- bearish grind directional detection 增强
- bearish directional evidence 在特定条件下可以优先于 `RANGE_LIGHT`
- `BEAR_DEFENSE` 下允许使用状态层 bearish evidence 做最小方向兜底

该修复目标是减少 `2023-07-05 ~ 2023-08-23` 窗口内的慢跌误判，不是通过大范围参数 sweep 最大化单窗口收益。

相关测试入口：

- [test_202308_bearish_grind.py](/Users/disaster/workspace/code/zenQuant/tests/test_202308_bearish_grind.py)

### 2.3 历史输出解释能力

当前历史回放结果已包含 `cycle_context`，用于说明回测主要发生在哪个 BTC 周期/阶段，而不是只给总收益。

相关测试入口：

- [test_historical_pipeline.py](/Users/disaster/workspace/code/zenQuant/tests/test_historical_pipeline.py)

## 3. 测试入口

### 3.1 单测与集成测试

执行以下命令：

```bash
uv run pytest /Users/disaster/workspace/code/zenQuant/tests/test_variant_comparison.py
uv run pytest /Users/disaster/workspace/code/zenQuant/tests/test_202308_bearish_grind.py
uv run pytest /Users/disaster/workspace/code/zenQuant/tests/test_historical_pipeline.py
```

预期：

- 全部通过
- `test_variant_comparison.py` 能断言 `current` 与 `paulwei_baseline` 两个 variant 都能产出稳定报告结构
- `test_202308_bearish_grind.py` 能断言 bearish grind 序列不再完全落入 `RANGE_LIGHT`
- `test_historical_pipeline.py` 能断言 `cycle_context` 已进入历史输出

失败含义：

- 若 `test_variant_comparison.py` 失败，说明 comparison contract 或 replay 注入点发生漂移
- 若 `test_202308_bearish_grind.py` 失败，说明 2023-08 修复行为回退
- 若 `test_historical_pipeline.py` 失败，说明历史输出结构或 determinism 被破坏

### 3.2 回归与门禁测试

执行以下命令：

```bash
uv run pytest /Users/disaster/workspace/code/zenQuant/tests/test_golden_output.py /Users/disaster/workspace/code/zenQuant/tests/test_backtest_invariants.py /Users/disaster/workspace/code/zenQuant/tests/test_parity.py /Users/disaster/workspace/code/zenQuant/tests/test_execution_router.py /Users/disaster/workspace/code/zenQuant/tests/test_execution_sensitivity.py /Users/disaster/workspace/code/zenQuant/tests/test_scenario_risk.py
```

预期：

- 全部通过
- golden 变化被视为 intentional behavior change，而非无意漂移
- parity、execution、risk 相关基础门禁不被 2023-08 修复破坏

失败含义：

- `golden_output` 失败：行为变化未同步或非预期
- `backtest_invariants` 失败：历史路径不再满足核心不变量
- `parity` 失败：plugin / legacy 路径语义可能发生偏移
- `execution_*` / `scenario_risk` 失败：执行或风控基线被破坏

### 3.3 脚本级验证

执行以下命令：

```bash
uv run python /Users/disaster/workspace/code/zenQuant/scripts/compare_variants.py --root data/raw/binance --symbol BTCUSDT --output-dir /tmp/zenquant-variant-compare
uv run python /Users/disaster/workspace/code/zenQuant/scripts/walk_forward_eval.py
uv run python /Users/disaster/workspace/code/zenQuant/scripts/adversarial_stress.py --historical
```

预期：

- `compare_variants.py` 成功生成：
  - `/tmp/zenquant-variant-compare/comparison.json`
  - `/tmp/zenquant-variant-compare/comparison.md`
- `walk_forward_eval.py` 能完成 OOS 验证，不出现明显 overfit 告警
- `adversarial_stress.py --historical` 能完成 historical replay robustness 验证

失败含义：

- `compare_variants.py` 失败：对照研究入口或输出契约损坏
- `walk_forward_eval.py` 失败：泛化验证链路损坏或策略退化
- `adversarial_stress.py --historical` 失败：策略在 hostile/worst windows 下出现新增脆弱性

## 4. 输出接口说明

### 4.1 comparison.json 顶层字段

`comparison.json` 目前应包含以下稳定字段：

- `created_at`
- `symbol`
- `portfolio_symbol`
- `variants`
- `windows`
- `window_reports`
- `validation_reports`

### 4.2 variants

每个 variant 对象至少包含：

- `name`
- `label`
- `risk_config`
- `execution_config`

当前测试必须断言 `variants[].name` 至少包含：

- `current`
- `paulwei_baseline`

### 4.3 window_reports

每个 `window_reports[]` 对象至少包含：

- `variant`
- `window_label`
- `metrics`
- `diagnostics`
- `cycle_context`
- `directional_state_distribution`
- `regime_distribution`
- `playbook_usage`
- `win_rate`
- `comparison_to_baseline`

其中：

- `comparison_to_baseline` 对 `paulwei_baseline` 必须为 `null`
- `comparison_to_baseline` 对其他 variant 必须存在

### 4.4 metrics

`window_reports[].metrics` 目前至少应包含：

- `sharpe`
- `net_pnl`
- `max_drawdown`
- `profit_factor`
- `trade_count`

这些字段应视为必须断言字段。

### 4.5 diagnostics

`window_reports[].diagnostics` 是观察性与解释性字段集合，测试应至少确认对象存在，且关键计数结构可访问。

当前建议最少检查：

- `playbook_counts`
- `risk_flag_counts`
- `actionable_exit_counts`

### 4.6 cycle_context

`window_reports[].cycle_context` 是解释字段，不改变 live intent/order contract。

当前建议最少检查：

- `dominant_cycle`
- `dominant_phase`
- `phase_distribution`
- `phase_bar_counts`
- `sharpe_by_phase`

### 4.7 validation_reports

每个 `validation_reports[]` 对象至少包含：

- `variant`
- `avg_train_sharpe`
- `avg_test_sharpe`
- `avg_oos_ratio`
- `worst_test_sharpe`
- `comparison_to_baseline`

其中：

- 对 `paulwei_baseline`，`comparison_to_baseline` 必须为 `null`
- 对其他 variant，`comparison_to_baseline` 必须存在

## 5. 当前验收标准

测试负责人封板时按以下标准判定：

- `compare_variants.py` 可成功生成 JSON/Markdown 报告
- `2023-08` 不再属于当前已知 failure
- `historical adversarial` 不出现新增灾难性失败
- `walk_forward_eval.py` 不出现显著 overfit 信号
- `golden`、`invariants`、`parity`、`execution/risk` 相关测试保持全绿

建议把 `2023-08` 窗口重点观察以下指标：

- `directional_state_distribution`
- `regime_distribution`
- `playbook_usage`
- `sharpe`
- `net_pnl`
- `win_rate`

## 6. 已知边界与非目标

- `current` 与 `paulwei_baseline` 在真实历史路径上当前可能仍然相同
- 这表示 comparison framework 已落地，但尚未证明存在一个可泛化、可 promote 的“优于 PaulWei”新变体
- 当前阶段不是在证明 execution alpha 已经成立，而是在证明：
  - comparison contract 稳定
  - 2023-08 修复已落地
  - validation harness 未被破坏
- `cycle_context` 是解释字段，不改变 live/runtime contract

## 7. 测试结论记录建议

测试负责人输出结果时，建议至少记录：

- 执行日期
- 提交哈希
- 各命令通过/失败状态
- `comparison.json` 路径
- `2023-08` 关键窗口摘要
- walk-forward 摘要
- adversarial historical 摘要
- 发现的任何 contract drift 或字段缺失

## 8. Liquidation Data Collection 验收补充

若测试负责人同时验证 liquidation 研究链路，应额外执行：

```bash
uv run python /Users/disaster/workspace/code/zenQuant/scripts/backfill_liquidations.py --symbol BTCUSDT --days 30
uv run python /Users/disaster/workspace/code/zenQuant/scripts/collect_liquidations.py --symbol BTCUSDT --flush-interval 300
uv run python /Users/disaster/workspace/code/zenQuant/scripts/research_liquidation.py --root data/raw/binance --symbol BTCUSDT --output-dir <dir> --skip-walk-forward --include-worst-periods
```

当前验收重点：

- 若 Binance 官方历史 backfill 不可用，脚本必须明确报出 blocker，而不是静默成功
- `research_liquidation.py` 必须输出 liquidation readiness
- 当 liquidation readiness 不满足时，研究结果必须是 `insufficient_data`
