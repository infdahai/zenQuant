# ZenQuant — Claude Code Context

> 本文件每次Claude Code session自动加载。保持精简，详细文档见AGENTS.md和docs/。

## 项目定位

Regime-aware crypto perpetual futures trading system. BTC-first, 4H execution timeframe.
完整角色定义和架构约束见 `AGENTS.md`。

## 当前信号体系 (9 plugins, 6 active — after ablation 2026-04-04)

| Signal | Weight | Status | Source | Ablation |
|--------|--------|--------|--------|----------|
| multi_momentum_signal | 0.45 | **Active** | Price structure (multi-horizon ROC) | LOAD-BEARING (-0.04) |
| crash_reversal_signal | 0.25 | **Active** | 极端崩盘反转 (bar ret<-5%) | LOAD-BEARING (-0.14) |
| cvd_signal | 0.25 | **Active** | Real CVD from aggTrades | Re-enabled (+0.27) |
| funding_alpha_signal | 0.10 | **Active** | 资金费率反转(contrarian) | LOAD-BEARING (-0.10) |
| liquidity_signal | 0.20 | **Active** | Order book imbalance | PHANTOM (0/12766 fires, live-only) |
| crowding_signal | 0.10 | **Active** | Funding x OI positioning | Re-enabled (+0.11) |
| macro_regime_signal | 0.12 | Disabled | VIX/DXY → NOISE for BTC | DEAD WEIGHT (+0.46) |
| vol_mean_reversion_signal | 0.20 | Disabled | Vol spike reversion weak | DEAD WEIGHT (-0.13) |
| options_vol_signal | 0.15 | Disabled | Deribit ATM IV/skew (collecting data) | Pending |

## 架构三层分离

```
Layer 3 (参数)  → AutoResearch迭代优化 (weights, thresholds)
Layer 2 (逻辑)  → 5-gate harness保护 (signal compute(), regime rules)
Layer 1 (数据)  → 一次搭建永久运行 (GitHub Actions, DataStores, Parquet)
```

## 当前性能 (2026-04-04, PaulWei asymmetric DD)

**全期 (2020-2025)**: Sharpe 1.95, $100k→$10.5M, DD 22.3%, 6年全部正收益
**Risk guards**: VPIN toxic flow (+16% Sharpe), weekend vol adjustment, asymmetric DD
**数据**: real CVD 13,638 4H + 54,552 1H, COT 326wk, spot basis 7,697, NDX/Gold
**Walk-forward**: 8/8 windows positive, avg OOS ratio 1.11, Train/Test gap 9%
**Tests**: 259 pass, 7 gates + adversarial + anti-snooping

核心原则(文章"骨架+血肉"): 6层决策链是骨架(不替代)，AI只调参数(血肉)
每个新因子必须IC>0.03才启用(防止因子幻象)

## 质量门禁 (Harness — 7 gates + adversarial)

Gate 0: Factor Health (IC stability, sprint contracts, walk-forward stability)
Gate A: ruff lint
Gate B: Import boundaries
Gate C: Golden output regression
Gate D: Plugin/legacy parity
Gate E: Full pytest (242 tests)
Gate F: Walk-forward overfit detection (8 OOS windows)
+ Adversarial: 3 GAN-style scenarios (whipsaw, vacuum, crowding trap)
+ Signal Ablation: periodic load-bearing analysis
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
- **Signal ablation发现交互效应** — 单因子ablation有误杀风险, 需sequential验证 (2026-04-04)
- **PaulWei asymmetric DD** — 做多DD 15/22/28%, 做空DD 8/14/20%, 57.3x收益 (2026-04-04)
- **Anthropic harness engineering** — load-bearing analysis + GAN-style adversarial evaluator (2026-04-04)
- **多时间帧共识** — Elder/AQR/ICT: 大周期定方向,小周期做执行. 4H:1H=4x在最优范围 (2026-04-04)
