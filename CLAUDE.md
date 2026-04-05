# ZenQuant — Claude Code Context

> 本文件每次Claude Code session自动加载。保持精简，详细文档见AGENTS.md和docs/。

## 项目定位

Regime-aware crypto perpetual futures trading system. BTC-first, 4H execution timeframe.
完整角色定义和架构约束见 `AGENTS.md`。

## 当前信号体系 (9 plugins, 7 active)

| Signal | Weight | Status | Source | Note |
|--------|--------|--------|--------|------|
| multi_momentum_signal | 0.45 | **Active** | Multi-horizon ROC | LOAD-BEARING |
| crash_reversal_signal | 0.25 | **Active** | 极端崩盘反转 | LOAD-BEARING (IC=-0.21) |
| cvd_signal | 0.25 | **Active** | Real CVD from aggTrades | Re-enabled |
| funding_alpha_signal | 0.10 | **Active** | 资金费率反转 | LOAD-BEARING |
| liquidity_signal | 0.20 | **Active** | Order book imbalance | PHANTOM (live-only) |
| crowding_signal | 0.10 | **Active** | Funding x OI | Re-enabled |
| options_vol_signal | 0.15 | **Active** | Deribit IV/skew + GEX | Auto-neutral without data |
| macro_regime_signal | 0.12 | Disabled | VIX/DXY → NOISE | DEAD WEIGHT |
| vol_mean_reversion_signal | 0.20 | Disabled | Vol spike weak | DEAD WEIGHT |

## Risk Guards (configurable via RiskOverlayConfig)

| Guard | Trigger | Effect |
|-------|---------|--------|
| Asymmetric DD | Long 15/22/28%, Short 8/14/20% | lev ×0.65/0.40/0.20 |
| VPIN toxic flow | vpin > 0.10 (p95) | lev ×0.70 |
| GEX regime | PINNING / AMPLIFYING | lev ×1.15 / ×0.70 |
| Basis premium | basis_z > 2.0 (long only) | lev ×0.80 |
| Basis discount | basis_z < -2.0 (short only) | lev ×0.80 |
| Weekend vol | Sat/Sun | lev ×1.25 |
| COT crowding | leveraged_z > 2.5 (directional) | lev ×0.70 |
| Equity stress | QQQ+IWM crash | lev ×0.50 |
| Vol shock | realized_vol > 0.95 | lev ×0.40 |
| Macro event | FOMC/CPI day | lev ×0.75 |
| Kill switch | DD > 30% | reduce_only → recovery |

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

## Known Vulnerabilities (adversarial replay 2026-04-05)

**Historical Replay**: 4/5 worst periods PASS, 1/5 FAIL (80% pass rate)

| Period | Issue | Root Cause | Fix Attempted | Result |
|--------|-------|------------|---------------|--------|
| **2023-08** | Sharpe -3.69 (-15.6% BTC decline) | Regime misclassification: RANGE 82% (should use BEAR_DEFENSE) | Lower momentum threshold -0.5→-0.15 | **Worse** (-3.69→-4.20) |

**Details**: `logs/paper_trading/2023-08_root_cause.md`  
**Lesson**: Slow grinds (persistent downtrend without crashes) are strategy limitation. BEAR_DEFENSE works (68% win rate) but under-used (6.5% vs needed 27.9%). Threshold tuning degraded other periods.  
**Decision**: Document vulnerability, no fix deployed. Not all market conditions are tradable.

---

## Testing Limitations

### BTC Cycle Dependency ⚠️

**Issue**: BTC has strong 4-year halving cycles (2020→2024→2028). Train/Dev/Test split may not capture all cycle phases evenly.

**Current Split**:
- **Train** (2020-2023): Full Cycle 3 (POST_HALVING → BULL → CRASH → BEAR → RECOVERY) ✅
- **Dev** (2024-2025): Partial Cycle 4 (POST_HALVING → BULL_INITIAL, missing CRASH/BEAR) ⚠️
- **Test** (2026+): Future (unknown phases)

**Impact on Metrics**:
- Sharpe varies 5x across cycle phases (0.5 in BEAR → 3.0 in BULL_PEAK)
- OOS ratio may mislead if Dev/Train in different phases
- Example: RL tuner showed Train Sharpe 3.31 vs Dev Sharpe 2.06 (OOS ratio 0.62)
  - Looks like overfitting, but actually **cycle phase effect** (BULL_PEAK vs POST_HALVING)
  - Evidence: RL params = Manual params on Dev (both 2.06) → parameter-insensitive, not overfitting

**Mitigation**:
1. **Walk-forward validation** — 8 windows cover different cycle positions (current)
2. **Phase-specific reporting** — break down Sharpe by cycle phase (planned 2026 Q2)
3. **Cycle-aware testing** — stratified validation (planned 2026 Q3)
4. **Full-cycle validation** — 2028 when Cycle 4 completes (gold standard)

**Current Confidence**: ⚠️ **MODERATE** (1.5 cycles better than most crypto funds, but < traditional quant standards)

**Details**: `docs/CYCLE_AWARE_TESTING.md`

### Dev Set Budget (SNOOPING_LOG.md)

**Status**: 32/50 uses (🟠 64% exhausted)  
**Remaining**: 18 uses  
**Next refresh**: 2026 Q3 (滚动更新: Train扩展至2024, Dev更新为2025-2026)

---

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
