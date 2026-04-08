# 历史有效性验证记录（2026-04-07）

本文档记录 [`LIVE_VALIDATION_HANDOFF_SPEC.md`](/Users/disaster/workspace/code/zenQuant/docs/LIVE_VALIDATION_HANDOFF_SPEC.md) 中历史验证部分的一次完整执行结果，作为测试负责人封板时的仓库内审计记录。

## 范围

- 数据根目录：`data/raw/binance`
- 资产：`BTCUSDT`
- 提交哈希：`12f4f4b633011bbf72cfa0dedddf8f8c2e9fad05`
- 验证目标：
  - `current`
  - `paulwei_baseline`
  - `2023-08` 慢跌修复
  - `cycle_context` 历史输出

## 执行命令

```bash
uv run pytest tests/test_variant_comparison.py tests/test_202308_bearish_grind.py tests/test_historical_pipeline.py -q --tb=short
uv run pytest tests/test_golden_output.py tests/test_backtest_invariants.py tests/test_parity.py tests/test_execution_router.py tests/test_execution_sensitivity.py tests/test_scenario_risk.py -q --tb=short
uv run python scripts/compare_variants.py --root data/raw/binance --symbol BTCUSDT --output-dir /tmp/zenQuant-variant-compare
uv run python scripts/walk_forward_eval.py
uv run python scripts/adversarial_stress.py --historical
```

## 结果摘要

### 1. 规格核心测试

- `tests/test_variant_comparison.py`
- `tests/test_202308_bearish_grind.py`
- `tests/test_historical_pipeline.py`

结果：`5 passed`

### 2. 回归与门禁测试

- `tests/test_golden_output.py`
- `tests/test_backtest_invariants.py`
- `tests/test_parity.py`
- `tests/test_execution_router.py`
- `tests/test_execution_sensitivity.py`
- `tests/test_scenario_risk.py`

结果：`58 passed`

### 3. Variant Comparison

产物：

- `/tmp/zenQuant-variant-compare/comparison.json`
- `/tmp/zenQuant-variant-compare/comparison.md`

关键字段检查：

- `variants = ["current", "paulwei_baseline"]`
- `window_reports[*].cycle_context` 存在
- `validation_reports[*]` 存在

关键窗口摘要：

| Variant | Window | Sharpe | Net PnL | Max DD | Profit Factor | Trades |
|---------|--------|--------|---------|--------|---------------|--------|
| current | overall | 2.2751 | 176851646.63 | 14.44% | 1.2706 | 7438 |
| current | 2023-08 | 4.0497 | 51093.72 | 10.47% | 0.1528 | 169 |
| paulwei_baseline | overall | 2.2751 | 176851646.63 | 14.44% | 1.2706 | 7438 |
| paulwei_baseline | 2023-08 | 4.0497 | 51093.72 | 10.47% | 0.1528 | 169 |

说明：

- 当前 `current` 与 `paulwei_baseline` 在真实历史路径上仍一致。
- 这表示 comparison framework 已稳定落地，但尚未证明存在一个可推广的“优于 PaulWei”的新执行变体。

### 4. Walk-Forward OOS

`scripts/walk_forward_eval.py` 结果：

- 窗口数：`8`
- `ROBUST`：`6/8`
- `OK`：`2/8`
- `Overfit windows`：`0/8`
- Avg Train Sharpe：`2.23`
- Avg Test Sharpe：`2.54`
- Avg OOS Ratio：`1.25`

结论：`good out-of-sample stability`

### 5. Historical Adversarial

`scripts/adversarial_stress.py --historical` 结果：

- `5/5 scenarios passed`
- Full backtest：Sharpe `2.21`，Max DD `14.5%`

最关键窗口：

| Scenario | Sharpe | Max DD | Trades | Result |
|----------|--------|--------|--------|--------|
| HISTORICAL_2023-08_DD | 2.50 | 10.3% | 151 | PASS |

## 结论

按当前规格口径，本次验证结论为：

- `2023-08` 不再属于当前已知 failure
- historical adversarial 没有新增灾难性失败
- walk-forward 没有显著 overfit 信号
- golden / invariants / parity / execution-risk 门禁未被破坏

因此，策略在仓库当前 BTC 历史数据上具备可接受的历史稳健性。

## 边界

- 这只是历史层有效，不等于上线证明。
- 下一层验证仍然应是 paper/live incremental validation。
- `/tmp/zenQuant-variant-compare/` 是本地生成产物路径，不纳入版本控制。
