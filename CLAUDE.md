# ZenQuant — Claude Code Context

> 本文件每次Claude Code session自动加载。保持精简，详细文档见AGENTS.md和docs/。

## 项目定位

Regime-aware crypto perpetual futures trading system. BTC-first, 4H execution timeframe.
完整角色定义和架构约束见 `AGENTS.md`。

## 当前信号体系 (6 plugins)

| Signal | Weight | Status | Source |
|--------|--------|--------|--------|
| multi_momentum_signal | 0.45 | Active | Price structure (multi-horizon ROC) |
| cvd_signal | 0.25 | Active | Cumulative volume delta |
| liquidity_signal | 0.20 | Active | Order book imbalance |
| crowding_signal | 0.10 | Active | Funding x OI positioning |
| macro_regime_signal | 0.12 | Active | VIX/DXY/Treasury yields |
| options_vol_signal | 0.15 | Disabled | Deribit ATM IV/skew (pending 30d data) |

## 架构三层分离

```
Layer 3 (参数)  → AutoResearch迭代优化 (weights, thresholds)
Layer 2 (逻辑)  → 5-gate harness保护 (signal compute(), regime rules)
Layer 1 (数据)  → 一次搭建永久运行 (GitHub Actions, DataStores, Parquet)
```

## 战略优先级 (Phase 2 Plan, 2026-04-02)

**架构**: v2.0 confidence-weighted direction (signal × weight × confidence)
**数据**: BTC 12,766 + ETH 11,342 + BNB 1,740 + SOL 1,938 bars
**性能**: BTC Train=1.92 / Test=2.52 (不过拟合). ETH=-0.31 (需适配)
**下一步**: Phase 2 滚动IC监控 + 截面信号

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
