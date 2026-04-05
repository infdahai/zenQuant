# Crypto Quant Strategies: Quick Reference Table

**Last Updated**: 2026-04-05

## Strategy Compatibility Matrix

| Strategy | Timeframe | 4H Compatible | Sharpe Potential | Difficulty | zenQuant Status | Priority |
|----------|-----------|---------------|------------------|------------|----------------|----------|
| **Cross-Exchange Arb** | <1s | ❌ | 0.5-1.0 | Very High | N/A | SKIP |
| **Triangular Arb** | <1s | ❌ | 0.3-0.8 | Very High | N/A | SKIP |
| **Spot-Perp Basis** | Days-Weeks | ✅ HIGH | 0.3-0.5 | Low | Partial (guard only) | **MEDIUM** |
| **BTC-ETH Pairs** | 4H-Daily | ✅ VERY HIGH | 0.4-0.6 | Medium | Not deployed | **HIGH** |
| **Multi-Asset Pairs** | 4H-Daily | ✅ HIGH | 0.3-0.5 | Medium | Not deployed | MEDIUM |
| **Momentum (ROC)** | 4H-Weekly | ✅ VERY HIGH | 1.0+ | Low | ✅ DEPLOYED (0.45) | OPTIMIZE |
| **Mean Reversion (Crash)** | 15m-4H | ✅ HIGH | 0.5-0.8 | Low | ✅ DEPLOYED (0.25) | OPTIMIZE |
| **Vol Arb (IV vs RV)** | Daily-Weekly | ✅ HIGH | 0.2-0.4 | Medium | Partial (RV only) | **HIGH** |
| **Delta-Neutral Options** | Weekly | ⚠️ MEDIUM | 0.3-0.5 | High | Not deployed | LOW |
| **Funding Rate Arb** | 8H cycles | ✅ VERY HIGH | 0.1-0.2 | Low | ✅ DEPLOYED (0.10) | OPTIMIZE |
| **Funding Mean Reversion** | 4H-Daily | ✅ HIGH | 0.2-0.3 | Low | Not deployed | MEDIUM |
| **On-Chain Flows** | 1H-Daily | ⚠️ MEDIUM | 0.1-0.2 | High | Not deployed | LOW |
| **On-Chain Value (MVRV)** | Daily-Weekly | ⚠️ LOW | 0.1-0.3 | Medium | Not deployed | LOW |
| **Macro Overlay (VIX)** | Daily-Weekly | ⚠️ LOW | -0.1 | Low | ❌ TESTED (NOISE) | **AVOID** |
| **Equity Stress Filter** | Daily | ✅ HIGH | Defensive | Low | ✅ DEPLOYED | OPTIMIZE |
| **DeFi Yield Farming** | Days-Weeks | ❌ | 0.2-0.4 | Very High | N/A | SKIP |

## Top 3 Recommendations (Priority Order)

### 1. BTC-ETH Statistical Arbitrage (Pairs Trading)
- **Why**: Sharpe 2.45 (academic), 4H natural fit, market-neutral diversification
- **Effort**: 3 days (cointegration test + signal plugin)
- **Expected Impact**: Sharpe +0.4-0.6, uncorrelated to BTC beta
- **Risk**: Cointegration breakdown (monitor rolling ADF test)

### 2. IV/RV Volatility Arbitrage Enhancement
- **Why**: Already have 5min RV (IC=0.248), GARCH beats Deribit IV by 8.55%
- **Effort**: 2 days (Deribit API + GARCH model)
- **Expected Impact**: Sharpe +0.2-0.4, better entry timing
- **Risk**: GARCH model failure during regime shifts

### 3. Basis Premium Guard for Short Positions
- **Why**: Low effort, extends existing infrastructure, defensive
- **Effort**: 0.5 day (modify RiskOverlayConfig)
- **Expected Impact**: Sharpe +0.1-0.2, reduces tail risk
- **Risk**: False positives during fast basis mean reversion

## Crypto-Specific Alpha Sources

| Alpha Source | TradFi Equivalent | Edge Persistence | zenQuant Usage |
|--------------|------------------|------------------|----------------|
| **Funding Rates** | Futures roll | HIGH (structural long bias) | ✅ funding_alpha_signal |
| **Spot-Perp Basis** | Futures basis | HIGH (inefficiency) | ✅ basis_premium guard |
| **On-Chain Data** | None | MEDIUM (tools improving) | ❌ Not deployed |
| **24/7 Trading** | 9:30-4pm | HIGH (permanent) | ✅ weekend_vol guard |
| **Exchange Fragmentation** | Low (CME duopoly) | MEDIUM (consolidating) | ❌ Not exploited |
| **Retail Dominance** | Institutional | HIGH (emotional trading) | ✅ crash_reversal_signal |
| **Vol Risk Premium** | VIX premium | HIGH (IV > RV) | Partial (RV only) |

## Implementation Timeline

### Phase 1: High ROI, Low Effort (1 week)
1. Basis premium for shorts (0.5 day)
2. IV/RV spread in vol signal (2 days)
3. Testing & validation (2 days)

### Phase 2: High ROI, Medium Effort (2-3 weeks)
4. BTC-ETH pairs trading (3 days)
5. Walk-forward validation (3 days)
6. Paper trading 48H (1 week)

### Phase 3: Research (1-2 months)
7. On-chain exchange flows (1 week)
8. Multi-asset pairs (1 week)
9. DeFi yield arb exploration (2 weeks)

## Key Academic References

1. **Mann (2025)**: 3 alpha categories (arb, factors, on-chain) — [SSRN](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=5225612)
2. **Fischer et al. (2019)**: Stat arb 7.1bps/trade — [MDPI](https://www.mdpi.com/1911-8074/12/1/31)
3. **IJSRA (2026)**: BTC-ETH Sharpe 2.45 — [PDF](https://ijsra.net/sites/default/files/fulltext_pdf/IJSRA-2026-0283.pdf)
4. **Du et al. (2025)**: GARCH > IV by 8.55% — [Wiley](https://onlinelibrary.wiley.com/doi/10.1002/fut.70029)
5. **Kristoufek & Bouri (2023)**: Arb during congestion — [ScienceDirect](https://www.sciencedirect.com/science/article/abs/pii/S1544612322005116)

## What Top Funds Trade

- **Jump Trading**: HFT market making + prop arb + DeFi liquidity
- **Jane Street**: HFT + basis trades (BTC ETF arb, $790M IBIT Q4 2025)
- **Wintermute**: Market making (50% of Binance liquidity)
- **Susquehanna**: Options market making on Deribit
- **Alameda (legacy)**: Cross-exchange arb + funding arb (profitable until FTX fraud)

**Insight**: Top funds = 80% HFT, 20% medium-freq arb → zenQuant's 4H niche underserved

## Avoid These Strategies

1. **HFT Arbitrage** (cross-exchange, triangular) — Requires sub-second execution
2. **Illiquid Altcoins** — Slippage risk, low volume
3. **Macro Overlay (VIX/DXY)** — TESTED: IC ≈ 0 (NOISE for BTC)
4. **DeFi Yield Farming** — Smart contract risk, impermanent loss, requires lock-up
5. **On-Chain Governance** — Weeks of token lock-up, not compatible with 4H execution
