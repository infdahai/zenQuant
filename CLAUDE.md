# ZenQuant — Claude Code Context

> 本文件每次Claude Code session自动加载。保持精简，详细文档见AGENTS.md和docs/。

## 项目定位

Regime-aware crypto perpetual futures trading system. BTC-first, 4H execution timeframe.
完整角色定义和架构约束见 `AGENTS.md`。

## 当前信号体系 (9 plugins, 8 active)

| Signal | Weight | Status | Source | IC |
|--------|--------|--------|--------|----|
| multi_momentum_signal | 0.45 | Active | Price structure (multi-horizon ROC) | -0.08 (弱) |
| crash_reversal_signal | 0.25 | Active | 极端崩盘反转 (bar ret<-5%) | **-0.21** (最强) |
| cvd_signal | 0.25 | Active | Cumulative volume delta | -0.03 |
| vol_mean_reversion_signal | 0.20 | Active | 波动率均值回归 (rv20/rv60>1.5) | **+2.98%/5d excess** |
| liquidity_signal | 0.20 | Active | Order book imbalance | N/A(无OB数据) |
| macro_regime_signal | 0.12 | Active | VIX/DXY/Treasury/TLT | -0.01 |
| funding_alpha_signal | 0.10 | Active | 资金费率反转(contrarian) | -0.03 |
| crowding_signal | 0.10 | Active | Funding x OI positioning | N/A |
| options_vol_signal | 0.15 | Disabled | Deribit ATM IV/skew (pending 30d data) | N/A |

## 架构三层分离

```
Layer 3 (参数)  → AutoResearch迭代优化 (weights, thresholds)
Layer 2 (逻辑)  → 5-gate harness保护 (signal compute(), regime rules)
Layer 1 (数据)  → 一次搭建永久运行 (GitHub Actions, DataStores, Parquet)
```

## 战略优先级 (Phase 2 Plan, 2026-04-02)

**架构**: v2.0 confidence-weighted direction + pyramid execution + equity risk overlay
**数据**: BTC 12,766 + ETH 11,342 + BNB 1,740 + SOL 1,938 bars + 5min收集中
**性能**: BTC Train=1.53 / Test=1.74 (7年0亏损年). ETH=-0.84 (不交易)
**下一步**: 5min特征提取 + 1H执行层 + RL执行timing

核心原则(文章"骨架+血肉"): 6层决策链是骨架(不替代)，AI只调参数(血肉)
每个新因子必须IC>0.03才启用(防止因子幻象)

## 质量门禁 (Harness)

5-gate chain: ruff → import boundaries → golden output → parity → pytest(220+)
详见 `docs/HARNESS_ARCHITECTURE.md`

## 评估纪律 (最重要)

- **Train/Test严格分割**: Train=2020-2023, Test=2024-2026. Test是secret benchmark, NEVER用于调参
- **不报整体Sharpe**: 报分年度/分regime拆解. Sharpe 1.89掩盖了3/7年亏损
- **Optimize信息增益**: 每次实验前问"结果我已经知道吗?" 如果是→不做
- **Verification Agent**: "try to break it" — 主动寻找策略失败的场景
- **反偏见**: 不反复确认已知结论. 2025年Sharpe -1.1是最重要的信号

## 关键决策记录

- **v2.0: confidence-weighted direction** — 1行改动解决了两天的dampening问题. Sharpe+4%, DD-21% (2026-04-02)
- **市场不需要被分类** — 不做regime classification，confidence自然让市场选择策略 (2026-04-02)
- **Alpha 100%来自TREND regime** — RANGE trades贡献≈$0 (2026-04-02)
- **参数调优已饱和** — Bayesian 200 trials全部Sharpe=1.89, 瓶颈在信号逻辑层 (2026-04-02)
- **Macro信号是核心基础设施** — 但IC分析显示VIX/DXY对BTC预测力接近NOISE (2026-04-02)
- **确定性检查 > LLM判断** — Agent只做advisory，不做决策 (2026-04-01)
- **数据收集与策略参数分离** — infra一经搭建不随策略迭代变化 (2026-04-01)
- **Position sizing IS the stop loss** — PaulWei哲学: 仓位小到不会爆仓, 不需要价格止损 (2026-04-04)
- **BTC长期非对称** — 法币贬值→稀缺资产长期涨→做多比做空更安全→多空风控应不对称 (2026-04-04)
- **5min RV是最强因子** — IC=+0.248, 远超其他所有因子. 学术验证(Andersen 1998) (2026-04-04)
