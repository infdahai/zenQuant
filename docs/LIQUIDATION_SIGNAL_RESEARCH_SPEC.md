# 研究规格：Liquidation Signal

## 1. 目的

本文档定义“清算数据是否值得纳入正式信号层”的研究轨道。

目标不是证明“清算一定能赚钱”，也不是把“大清算=高效捡尸体”当作产品结论。目标是验证：

- 清算数据是否能为 BTC 4H bar-close 体系提供额外信息增益
- 清算更适合增强 `state`、`risk`、`execution timing` 还是 `tactical reversal`
- 清算增强后的变体，是否能在泛化验证中优于“不使用清算”的基线

## 2. 当前基础设施与前提

仓库已经具备清算数据的点时正确基础设施：

- [liquidation_store.py](/Users/disaster/workspace/code/zenQuant/zenquant/data/liquidation_store.py)
- [test_liquidation_store.py](/Users/disaster/workspace/code/zenQuant/tests/test_liquidation_store.py)

当前可用 `LiquidationSnapshot` 字段为：

- `total_liq_usd`
- `long_liq_usd`
- `short_liq_usd`
- `liq_imbalance`
- `max_single_liq_usd`
- `liq_intensity_z`
- `liq_imbalance_z`
- `quality_flags`

研究默认前提：

- BTC first
- 4H bar-close only
- point-in-time correctness mandatory
- live/backtest parity mandatory
- 清算默认先基于现有 Binance perp 聚合路径

## 3. 外部证据与研究立场

### 3.1 交易所与数据源口径

- BitMEX 官方清算引擎说明表明，强平本身会触发市场平仓与更强的风险传导，因此它是结构性订单流冲击事件，而不是普通成交噪声。
  - 来源：[BitMEX Support](https://support.bitmex.com/hc/en-gb/articles/18589551021469-How-does-the-Liquidation-Engine-work)
- Tardis 文档指出 Binance `forceOrder` 在 2021-04-27 后最多每秒只推一次 snapshot，不应假设能保留每一笔逐笔 liquidation event。
  - 来源：[Tardis Docs](https://docs.tardis.dev/faq/data)

因此本项目中的清算数据研究口径固定为：

- 把 liquidation 当作“聚合压力/强平强度信号”
- 不把 liquidation 当作完整逐笔真相
- 不基于伪高精度假设设计 execution 逻辑

### 3.2 学术与业界结论

- CMU/WWW 2021 的 BitMEX 研究表明，大规模清算与剧烈价格跳动显著相关，且作者认为高杠杆和级联清算会支持大幅波动。
  - 来源：[Towards Understanding Cryptocurrency Derivatives: A Case Study of BitMEX](https://cylab.cmu.edu/_files/documents/towards-understanding-cryptocurrency.pdf)
- Coinbase Institutional 在 2025-10-17 周报中给出更贴近实务的表述：大规模去杠杆确实能让结构更健康，但不会自动触发反弹。
  - 来源：[Coinbase Institutional](https://www.coinbase.com/institutional/research-insights/research/weekly-market-commentary/weekly-2025-10-17)

本研究轨道采用的结论是：

- liquidation 是条件性有用信号
- liquidation 单独使用不足以构成稳定 alpha
- liquidation 更可能在 `crowding + funding/OI + state + volatility` 的联合作用下产生可交易意义

## 4. 研究问题

本研究必须依次回答以下问题：

### 4.1 信息增益问题

在已有信号体系之外，清算字段是否提供独立的附加信息，而不是重复表达现有的：

- funding crowding
- open interest stretch
- realized volatility shock
- order book liquidity stress

### 4.2 层级归属问题

清算更适合进入哪一层：

- `state enhancement`
- `risk throttle`
- `execution timing`
- `tactical reversal`

第一轮研究优先顺序固定为：

1. `liq_state_overlay`
2. `liq_risk_guard`
3. `liq_execution_timing`
4. `liq_reversal_gate`

### 4.3 泛化问题

清算增强后的策略，是否能在以下验证层上不弱于无清算基线：

- walk-forward OOS
- top-5 worst periods
- adversarial historical replay

若只能在单窗口上显著赚钱，但在其他窗口退化，则判定为不通过。

## 5. 研究边界

### 5.1 第一阶段允许的候选用法

#### `liq_state_overlay`

用途：

- 增强 `STRESS`
- 增强 `BEAR_DEFENSE`
- 识别 squeeze aftermath / forced unwind aftermath

原则：

- 只增强状态证据，不直接绕过主状态机
- 优先识别“被动去杠杆进行中”和“去杠杆后的修复阶段”

#### `liq_risk_guard`

用途：

- 当 liquidation 强、方向未稳定时，主动收紧 leverage cap 或 delta step

原则：

- 优先防误判，不优先追求收益提升
- 不能让清算数据直接替代既有 risk precedence

#### `liq_execution_timing`

用途：

- 不改方向，只改执行节奏
- 极端清算时避免追价，延迟补仓，或在 exhaustion 成立后更有纪律地回补

原则：

- 仅作用于 execution timing
- 不得引入逐笔级别假设

#### `liq_reversal_gate`

用途：

- 仅在极端清算 + 拥挤极值 + 状态支持时允许反向“捡尸体”

原则：

- 明确禁止把 liquidation 单独作为抄底或摸顶依据
- 第一阶段默认不直接 promote 到主 profile

### 5.2 第一阶段禁止事项

- 不引入未来窗口确认的 hindsight 特征到生产决策链
- 不以单一窗口收益最大化定义成功
- 不做多交易所 liquidation 拼接研究
- 不在第一阶段把清算信号升级为独立主导信号
- 不因清算研究破坏 live/backtest parity

## 6. 特征口径

第一阶段只允许基于现有 `LiquidationSnapshot` 做点时派生，不新增新的外生离线标签。

允许的派生特征示例：

- `liq_total_z`
- `liq_side_dominance`
- `liq_max_single_share`
- `liq_exhaustion_score = f(liq_intensity_z, liq_imbalance, funding_oi_z, realized_vol, state)`
- `liq_rebound_setup = f(extreme_long_liq, negative_funding_or_crowding, non-trending_or_reversal_ready_state)`

特征设计要求：

- 必须可解释
- 必须 point-in-time correct
- 必须可在 replay 中复现
- 必须在 `LIQ_STALE` 时自动降权、失效或退出决策

## 7. 变体定义

下一轮 comparison framework 扩展时，统一使用以下研究变体：

- `paulwei_baseline`
- `execution_plus`
- `strategy_plus`
- `full_variant`

边界固定为：

- `paulwei_baseline`
  - 当前固定执行/风控哲学
- `execution_plus`
  - 只允许修改 execution/risk，包括未来可能接入 `liq_execution_timing`
- `strategy_plus`
  - 只允许修改 state/playbook/signal，包括 `liq_state_overlay`
- `full_variant`
  - strategy 与 execution 都可动

任何研究结果都必须与 `paulwei_baseline` 直接对照。

## 8. 测试与验证要求

### 8.1 必须覆盖的单测/集成测试

- `LiquidationDataStore` 点时正确性不能回退
- `LIQ_STALE` 时信号自动降权或失效
- `liq_state_overlay` 不改变 live contract，只改变 state evidence 或解释字段
- `liq_reversal_gate` 若启用，必须覆盖：
  - 极端 long liquidation + crowding stretch + exhaustion 成立时，允许反弹 setup
  - 极端 liquidation 但趋势继续踩踏时，不得误触发抄底
- `compare_variants` 扩展后，多 variant 输出字段仍稳定

### 8.2 必须执行的非单测验证

- 先做 liquidation data readiness 检查：
  - total bars
  - nonzero liquidation bars
  - non-stale bars
  - extreme bars
- walk-forward OOS 对照
- top-5 worst periods 对照
- `adversarial_stress.py --historical` 对照
- 固定窗口 `2023-08` 专项对照

### 8.3 Promote 门槛

清算增强只有满足以下条件才允许进入主研究 profile：

- liquidation data readiness = true
- `avg_test_sharpe` 不低于无清算基线
- `worst_test_sharpe` 不恶化到失控
- adversarial historical pass 数不减少
- top-5 worst periods 不新增灾难性退化
- 对 `2023-08` 的改善必须能解释为 state/risk/execution 哪一层变好，而不是偶然赚钱

如果 `nonzero liquidation bars == 0` 或 `non-stale bars == 0`：

- 研究输出必须标记为 `insufficient_data`
- 不得给出 `promotion_candidate=true`
- 不得把“零差异”解读为“清算信号无效”

## 9. 当前结论口径

截至本文档版本，当前结论固定为：

- 清算数据值得收集和研究
- 清算数据应被视为结构性压力释放与拥挤信号
- 清算数据不应被产品化表述为“清算大就去捡尸体”
- 第一阶段默认先研究 `state_overlay` 与 `risk_guard`
- `liq_reversal_gate` 可以存在，但只能作为严格受限、需要额外确认的候选能力

## 10. 交付物要求

清算研究每一轮都应至少产出：

- 研究变体定义
- 特征说明
- comparison JSON/Markdown 对照结果
- walk-forward 摘要
- worst-period 摘要
- adversarial historical 摘要
- promote / do-not-promote 结论

任何“启用清算”的 promote 建议，如果缺少上述输出，默认无效。
