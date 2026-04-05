# Crypto Quant Strategies Beyond HFT: Taxonomy & Framework Integration Analysis

**Research Date**: 2026-04-05  
**Scope**: Strategy types applicable to 4H execution timeframe, integration with zenQuant regime-aware framework

---

## Executive Summary

This research identifies **7 major categories** of crypto quant strategies beyond HFT, with **4 categories** directly compatible with zenQuant's 4H execution framework. The most promising additions are:

1. **Basis/Funding Arbitrage** (market-neutral, 10-15% annualized yield)
2. **Volatility Trading** (delta-neutral options strategies, IC=0.248 for 5min RV)
3. **Statistical Arbitrage** (BTC-ETH pairs, Sharpe 2.45, mean-reverting)

Key finding: **Crypto-specific alpha sources** (funding rates, on-chain data, spot-perp basis) offer structural edges not available in TradFi, justifying dedicated signal development.

---

## 1. TAXONOMY OF CRYPTO QUANT STRATEGIES

### 1.1 Market Microstructure Arbitrage (Sub-1s to 1H)

**Description**: Exploit price inefficiencies across venues or instruments

#### A. Cross-Exchange Arbitrage
- **Mechanism**: Buy low on Exchange A, sell high on Exchange B
- **Timeframe**: Sub-second to minutes
- **Returns**: 0.1-0.5% per opportunity (pre-fees)
- **Challenges**: Execution speed, withdrawal delays, exchange risk
- **4H Compatibility**: ❌ (requires sub-second execution)

#### B. Triangular Arbitrage
- **Mechanism**: 3+ swaps across currency pairs (e.g., BTC→ETH→USDT→BTC)
- **Timeframe**: Milliseconds to seconds
- **Returns**: 0.05-0.2% per cycle
- **Challenges**: Gas fees on DEX, requires simultaneous execution
- **4H Compatibility**: ❌ (HFT-only)

#### C. Spot-Perp Basis Trading
- **Mechanism**: Long spot + Short perp = funding rate capture
- **Timeframe**: Days to weeks (funding every 8H)
- **Returns**: 10-15% annualized (90%+ days positive funding)
- **Challenges**: Basis collapse during bear markets, collateral management
- **4H Compatibility**: ✅ **HIGH** — Can layer with directional signals

**Academic Support**: 
- Kristoufek & Bouri (2023): Arbitrage opportunities arise during network congestion + volatility
- Recent data: Basis compressed from 6.63% to 4.46% annualized (217bps) in late 2025

---

### 1.2 Statistical Arbitrage (1H to Daily)

**Description**: Mean-reversion strategies on cointegrated or correlated pairs

#### A. Pairs Trading (BTC-ETH, BTC-LTC)
- **Mechanism**: Trade z-score deviations from long-term equilibrium
- **Timeframe**: 4H to Daily
- **Returns**: 12% monthly (Fischer et al. 2019), Sharpe 2.45 (BTC-ETH, 2026 study)
- **Key Insight**: Cointegration > Correlation (ADF test required)
- **4H Compatibility**: ✅ **VERY HIGH** — Natural fit for 4H execution

**Academic Support**:
- Mann (2025): BTC-ETH correlation = 0.918, but cointegration ensures mean reversion
- IJSRA (2026): BTC-ETH Sharpe 2.45 using cointegration-based entry/exit

#### B. Cross-Asset Statistical Arb
- **Mechanism**: Exploit temporary mispricings vs. traditional assets (Gold, NDX)
- **Timeframe**: Daily to Weekly
- **Returns**: Strategy-dependent
- **4H Compatibility**: ⚠️ **MEDIUM** — Slower regime shifts, better as macro overlay

---

### 1.3 Factor-Based Investing (4H to Daily)

**Description**: Systematic exposure to risk premia (momentum, mean reversion, value, liquidity)

#### A. Momentum Strategies
- **Mechanism**: Buy recent winners, sell recent losers (ROC, MACD)
- **Timeframe**: 4H to Weekly
- **Returns**: Variable (zenQuant's multi_momentum_signal = 0.45 weight, LOAD-BEARING)
- **4H Compatibility**: ✅ **VERY HIGH** — Already core to zenQuant

**Research Note**: 
- Momentum performs best in trending/bullish markets
- 4H charts provide cleaner signals than 15m (lower noise)

#### B. Mean Reversion
- **Mechanism**: Fade extreme moves (Bollinger Bands, RSI, z-scores)
- **Timeframe**: 15m to 4H
- **Returns**: Works best in range-bound/choppy conditions
- **4H Compatibility**: ✅ **HIGH** — zenQuant's crash_reversal_signal (IC=-0.21)

**Key Insight**: BTC shows "exceptionally strong mean-reverting behavior" due to volatility + emotional trading

#### C. Value/Liquidity Factors
- **Mechanism**: Size, liquidity, on-chain value metrics
- **Timeframe**: Daily to Weekly
- **4H Compatibility**: ⚠️ **MEDIUM** — Better as regime filter than execution signal

**Academic Support**: Mann (2025) — Size, momentum, liquidity factors statistically significant in crypto

---

### 1.4 Volatility Trading (Options, Realized Vol)

**Description**: Trade volatility risk premium or vol mispricing

#### A. Volatility Arbitrage (IV vs RV)
- **Mechanism**: Sell options when IV > forecasted RV, buy when IV < RV
- **Timeframe**: Daily to Weekly (option expiry-dependent)
- **Returns**: 5-10% annualized (VRP stable periods)
- **Key Finding**: GARCH forecasts > Implied Vol for BTC/ETH future realized vol
- **4H Compatibility**: ✅ **HIGH** — zenQuant already uses 5min RV (IC=0.248, strongest factor)

**Academic Support**:
- GARCH RV forecasts outperform IV by 8.55% (2025 study)
- Delta-hedged vol-spread strategies yield "robust profits" on Deribit BTC/ETH options

#### B. Delta-Neutral Option Selling
- **Mechanism**: Sell strangles/straddles, delta-hedge with spot
- **Timeframe**: Weekly to Monthly
- **Returns**: Collect theta + funding, minimal directional risk
- **4H Compatibility**: ⚠️ **MEDIUM** — Requires active gamma hedging (zenQuant uses GEX regime guard)

---

### 1.5 Funding Rate Strategies (Perpetual-Specific)

**Description**: Crypto-native alpha from perpetual futures mechanics

#### A. Funding Rate Arbitrage
- **Mechanism**: Long spot + Short perp = collect positive funding (90%+ days)
- **Timeframe**: 8H funding cycles, hold days-weeks
- **Returns**: 10-15% annualized (115.9% peak over 6mo in bull markets)
- **Risk**: Negative funding in bear markets, basis collapse
- **4H Compatibility**: ✅ **VERY HIGH** — zenQuant already has funding_alpha_signal (0.10 weight)

**Key Insight**: 
- Positive funding correlates with bullish sentiment (longs pay shorts)
- Can layer with directional trades: reduce long leverage when funding >95th percentile

#### B. Funding Rate Mean Reversion
- **Mechanism**: Fade extreme funding rates (short when funding >2σ, long when <-2σ)
- **Timeframe**: 4H to Daily
- **Returns**: Strategy-dependent
- **4H Compatibility**: ✅ **HIGH** — Complements existing funding_alpha_signal

---

### 1.6 On-Chain Data Strategies (1H to Daily)

**Description**: Trade blockchain-native signals (exchange flows, miner activity, whale wallets)

#### A. Exchange Flow Analysis
- **Mechanism**: Net inflows to CEX = bearish (sellers), outflows = bullish (hodlers)
- **Timeframe**: 1H to Daily
- **Returns**: Highly variable
- **Challenge**: Data lags, noisy signal
- **4H Compatibility**: ⚠️ **MEDIUM** — Better as regime confirmation than execution trigger

#### B. On-Chain Value Metrics
- **Mechanism**: MVRV, NUPL, realized price, SOPR
- **Timeframe**: Daily to Weekly
- **Returns**: CNN-LSTM model w/ on-chain data = 82.03% accuracy, Sharpe 6.47 (2026 study)
- **4H Compatibility**: ⚠️ **LOW** — Too slow for 4H execution, better for regime overlay

**Academic Support**:
- Mann (2025): On-chain metric signaling = 3rd major alpha category
- Boruta + CNN-LSTM: 1682.7% annualized return (likely overfit, real-world < 20%)

---

### 1.7 Macro Overlay Strategies (Daily to Weekly)

**Description**: Risk-on/risk-off regime filters using TradFi indicators

#### A. Risk Regime Switching
- **Mechanism**: VIX, DXY, Fed liquidity → allocate to BTC based on risk appetite
- **Timeframe**: Daily to Weekly
- **Returns**: Strategy-dependent
- **ZenQuant Status**: macro_regime_signal DISABLED (VIX/DXY = NOISE for BTC prediction)
- **4H Compatibility**: ⚠️ **LOW** — BTC increasingly crypto-native, less TradFi correlation

**Key Finding**: 
- BTC down 40% on March 12, 2020 (COVID crash) = risk-off correlation
- But long-term BTC-NDX correlation unstable → macro overlay unreliable

#### B. Equity Stress Filters
- **Mechanism**: Reduce leverage when QQQ+IWM crash
- **Timeframe**: Daily
- **Returns**: Defensive (Sharpe improvement via drawdown reduction)
- **4H Compatibility**: ✅ **HIGH** — zenQuant already has equity stress guard (lev ×0.50)

---

## 2. STRATEGY COMPATIBILITY WITH 4H TIMEFRAME

| Strategy Category | 4H Compatible? | Integration Effort | Expected Sharpe Add |
|-------------------|---------------|-------------------|-------------------|
| **Cross-Exchange Arb** | ❌ | N/A | 0 |
| **Triangular Arb** | ❌ | N/A | 0 |
| **Spot-Perp Basis** | ✅ HIGH | Low (data exists) | +0.3-0.5 (market-neutral) |
| **Pairs Trading (BTC-ETH)** | ✅ VERY HIGH | Medium (cointegration test) | +0.4-0.6 |
| **Momentum (Multi-Horizon)** | ✅ VERY HIGH | ✅ ALREADY DEPLOYED | Core (0.45 weight) |
| **Mean Reversion (Crash)** | ✅ HIGH | ✅ ALREADY DEPLOYED | Core (0.25 weight) |
| **Volatility Arb (IV vs RV)** | ✅ HIGH | Medium (Deribit IV data) | +0.2-0.4 |
| **Funding Rate Arb** | ✅ VERY HIGH | ✅ ALREADY DEPLOYED | +0.1-0.2 (defensive) |
| **On-Chain Flows** | ⚠️ MEDIUM | High (new data pipeline) | +0.1-0.2 (noisy) |
| **Macro Overlay (VIX/DXY)** | ⚠️ LOW | ✅ TESTED & DISABLED | -0.1 (NOISE) |
| **Equity Stress Filter** | ✅ HIGH | ✅ ALREADY DEPLOYED | Defensive |

**Verdict**: 
- 5/10 strategies are 4H-compatible
- 4/10 already deployed in zenQuant (momentum, mean reversion, funding, equity stress)
- Top 3 NEW opportunities: Basis trading, Pairs trading, Vol arb

---

## 3. UNIQUE CRYPTO ALPHA SOURCES VS TRADFI

### 3.1 Crypto-Specific Edges

| Alpha Source | TradFi Equivalent | Crypto Advantage | Persistence |
|--------------|------------------|------------------|-------------|
| **Funding Rates** | Futures roll yield | 90%+ days positive, 10-15% APY | HIGH (structural: retail long bias) |
| **Spot-Perp Basis** | Futures basis | Higher volatility, faster mean reversion | HIGH (crypto market inefficiency) |
| **On-Chain Data** | N/A (none) | Transparent blockchain, real-time whale tracking | MEDIUM (improving tools erode edge) |
| **24/7 Trading** | 9:30-4pm EST | Weekend vol, cross-timezone arb | HIGH (zenQuant weekend lev ×1.25) |
| **Exchange Fragmentation** | CME/CBOE duopoly | 50+ CEX/DEX = price divergence | MEDIUM (consolidating) |
| **Retail Dominance** | Institutional | Emotional overreaction, mean reversion | HIGH (BTC "exceptionally strong" mean reversion) |
| **Volatility Risk Premium** | VIX premium | Higher IV vs RV spread | HIGH (IV overestimates RV consistently) |

**Key Insight**: Funding rates + basis + 24/7 trading = **structural crypto edges** not present in equities/FX

---

### 3.2 What Top Crypto Funds Actually Trade

| Fund/Firm | Strategy Mix | Timeframe | Notes |
|-----------|-------------|-----------|-------|
| **Jump Trading** | Market making, prop arbitrage, DeFi liquidity | Sub-1s to Daily | Ultra-low-latency infrastructure, integrated CeFi/DeFi |
| **Jane Street** | Market making, basis trades (BTC ETF arb) | Sub-1s to Weekly | $790M IBIT position (Q4 2025), scaling back US crypto due to regulatory risk |
| **Wintermute** | Market making (50% of Binance liquidity) | Sub-1s to 1H | Crypto-native, exchange partnerships |
| **Susquehanna (SIG)** | Options market making, volatility trading | Intraday to Weekly | Deribit options dominance |
| **Alameda (Legacy)** | Cross-exchange arb, funding arb, market making | Sub-1s to Daily | Collapsed 2022 (FTX fraud), but strategies were profitable |

**Verdict**: 
- Top funds = 80% HFT/market making + 20% medium-frequency arb
- Medium-frequency edge = basis/funding/volatility (4H compatible)
- zenQuant's 4H niche = **underserved by large funds** (too slow for Jump, too systematic for retail)

---

## 4. TOP 3 RECOMMENDATIONS FOR ZENQUANT

### Recommendation 1: Deploy BTC-ETH Statistical Arbitrage (Pairs Trading)

**Rationale**:
- BTC-ETH correlation = 0.918 (high), cointegration stable (mean-reverting)
- Academic support: Sharpe 2.45, 12% monthly returns (2σ z-score entry)
- 4H timeframe = optimal (cleaner than 15m, faster than daily)

**Implementation**:
1. Run ADF cointegration test on BTC-ETH 4H ratio (last 90 days)
2. If cointegrated (p < 0.05), compute z-score of log(BTC/ETH) ratio
3. Entry: z > +2 (short BTC, long ETH), z < -2 (long BTC, short ETH)
4. Exit: z crosses 0 or hits +/- 3 (stop loss)
5. Position size: 20% of total capital (market-neutral)

**Expected Impact**:
- Sharpe: +0.4-0.6 (uncorrelated to directional trades)
- Drawdown: Independent of BTC direction (market-neutral)
- Risk: Cointegration breakdown (monitor rolling ADF test)

**Feasibility**: 
- Data: Already have BTC + ETH prices (add ETH perp data)
- Code: New signal plugin `btc_eth_pairs_signal.py` (~200 LOC)
- Testing: 7-gate harness, walk-forward on 2020-2023
- Time: 2-3 days implementation + 1 day testing

---

### Recommendation 2: Enhance Volatility Trading (IV vs RV Arbitrage)

**Rationale**:
- zenQuant already has 5min RV (IC=0.248, strongest factor)
- GARCH RV forecasts beat Deribit IV by 8.55%
- Selling overpriced IV = 5-10% annualized (VRP)

**Implementation**:
1. Fetch Deribit BTC 30-day ATM IV (BVIV index)
2. Forecast 30-day realized vol using GARCH(1,1) on 5min returns
3. If IV - Forecast_RV > +1σ historical spread → reduce long leverage (overpriced vol = risk-off)
4. If IV - Forecast_RV < -1σ → increase long leverage (cheap vol = opportunity)
5. Optional: Sell OTM strangles when IV/RV > 1.2 (delta-hedge weekly)

**Expected Impact**:
- Sharpe: +0.2-0.4 (improves timing of existing signals)
- Drawdown: Reduces entry during "expensive vol" regimes
- Risk: GARCH model failure, gamma risk if selling options

**Feasibility**:
- Data: Deribit API (free), GARCH library (arch Python package)
- Code: Enhance `options_vol_signal.py` with IV/RV spread (~150 LOC)
- Testing: Gate F walk-forward, adversarial scenario (2020-03-12 COVID crash)
- Time: 1-2 days implementation + 1 day testing

---

### Recommendation 3: Layer Basis Premium as Risk Guard (Not Standalone Signal)

**Rationale**:
- Spot-perp basis = market sentiment gauge (contango = bullish, backwardation = bearish)
- zenQuant already has `basis_premium` guard (basis_z > 2.0 → lev ×0.80)
- Can extend: basis_z < -2.0 (backwardation) → reduce shorts or flip long

**Implementation**:
1. Keep existing `basis_premium` guard for long positions
2. Add inverse guard: If basis_z < -2.0 AND short position → reduce lev ×0.80
3. Optional: If basis_z < -2.5 (extreme backwardation) → force neutral (rare event = uncertainty)

**Expected Impact**:
- Sharpe: +0.1-0.2 (defensive, not alpha-generative)
- Drawdown: Reduces losses during basis collapse (e.g., late 2025: 6.63% → 4.46%)
- Risk: False positives during fast mean reversion

**Feasibility**:
- Data: Already exists (`BasisDataStore`)
- Code: Modify `RiskOverlayConfig` in `risk_overlay.py` (~50 LOC)
- Testing: Gate E pytest + adversarial scenario (2025-12 basis collapse)
- Time: 0.5 days implementation + 0.5 day testing

---

## 5. STRATEGIES TO AVOID (NOT 4H COMPATIBLE)

### 5.1 High-Frequency Strategies (Sub-1H)
- Cross-exchange arbitrage (requires sub-second execution)
- Triangular arbitrage (gas fees kill margins)
- Order book microstructure (tick data, co-location)

### 5.2 Illiquid Altcoins
- Pairs trading on low-volume alts (slippage risk)
- DeFi yield farming (smart contract risk, impermanent loss)
- On-chain governance arb (requires weeks of lock-up)

### 5.3 Macro Overlay (Already Tested & Failed)
- VIX/DXY signals = NOISE for BTC (IC ≈ 0, zenQuant disabled macro_regime_signal)
- TradFi correlation unstable (works 2020-2021, breaks 2022-2025)

---

## 6. ACADEMIC PAPERS & KEY SOURCES

### 6.1 Must-Read Papers

1. **Mann, William (2025)**: "Quantitative Alpha in Crypto Markets: A Systematic Review"  
   - SSRN: https://papers.ssrn.com/sol3/papers.cfm?abstract_id=5225612  
   - Key: 3 alpha categories (cross-exchange arb, factors, on-chain), size/momentum/liquidity factors significant

2. **Fischer, Krauss et al. (2019)**: "Statistical Arbitrage in Cryptocurrency Markets"  
   - MDPI: https://www.mdpi.com/1911-8074/12/1/31  
   - Key: Random forest on 40 coins, 7.1 bps per trade, 120min holding period

3. **Kristoufek & Bouri (2023)**: "Exploring Sources of Statistical Arbitrage Among Bitcoin Exchanges"  
   - ScienceDirect: https://www.sciencedirect.com/science/article/abs/pii/S1544612322005116  
   - Key: Arbitrage opportunities during congestion + volatility, volume reduces arb

4. **IJSRA (2026)**: "Statistical Arbitrage Using Cointegration"  
   - https://ijsra.net/sites/default/files/fulltext_pdf/IJSRA-2026-0283.pdf  
   - Key: BTC-ETH Sharpe 2.45, cointegration > correlation

5. **Du et al. (2025)**: "Pricing Cryptocurrency Options with Volatility of Volatility"  
   - Wiley: https://onlinelibrary.wiley.com/doi/10.1002/fut.70029  
   - Key: VOV models reduce IV errors by 8.55%, GARCH > IV for RV forecasting

### 6.2 Data Providers Referenced
- **CryptoQuant**: On-chain data, exchange flows
- **Glassnode**: MVRV, NUPL, on-chain analytics
- **Deribit**: BTC/ETH options IV, BVIV/EVIV indices
- **Amberdata**: Funding rates, on-chain data APIs
- **CoinGlass**: Funding rate aggregator

---

## 7. INTEGRATION ROADMAP (PRIORITY ORDER)

### Phase 1: High ROI, Low Effort (1 week)
1. **Basis Premium Enhancement** (0.5 day) — Extend to short positions
2. **IV/RV Spread in Vol Signal** (2 days) — GARCH forecast vs Deribit IV
3. **Testing & Validation** (2 days) — 7-gate harness + adversarial

### Phase 2: High ROI, Medium Effort (2-3 weeks)
4. **BTC-ETH Pairs Trading** (3 days) — Cointegration test + z-score entries
5. **Walk-Forward Validation** (3 days) — 2020-2023 train, 2024-2026 test
6. **Paper Trading Validation** (1 week) — Run live 48H, compare to backtest

### Phase 3: Research & Exploration (1-2 months)
7. **On-Chain Exchange Flows** (1 week) — CryptoQuant API integration
8. **Multi-Asset Pairs** (1 week) — BTC-LTC, ETH-SOL cointegration tests
9. **DeFi Yield Arb** (2 weeks) — Aave/Compound rate differentials (if idle capital exists)

---

## 8. FRAMEWORK INTEGRATION NOTES

### 8.1 How Strategies Fit zenQuant's 3-Layer Architecture

**Layer 1 (Data)**:
- Basis/Funding: Already exists (`BasisDataStore`, funding rate history)
- Pairs Trading: Need ETH perp data (Binance API, similar to BTC)
- Vol Arb: Need Deribit IV data (new API integration)

**Layer 2 (Logic)**:
- Basis/Funding: Extend `risk_overlay.py` guards
- Pairs Trading: New signal plugin `btc_eth_pairs_signal.py`
- Vol Arb: Enhance `options_vol_signal.py` with GARCH model

**Layer 3 (Parameters)**:
- AutoResearch can optimize: z-score thresholds (pairs), IV/RV spread triggers (vol), basis_z levels (risk guards)
- Bayesian search space: 4 new params (z_entry, z_exit, iv_rv_threshold, basis_short_z)

### 8.2 Walk-Forward Compatibility
- All 3 recommendations preserve train/test split (2020-2023 train, 2024-2026 test)
- Cointegration tests use rolling 90-day window → no lookahead bias
- GARCH models use expanding window → realistic live trading simulation

### 8.3 Risk Guard Integration
- Pairs trading = market-neutral → exempt from asymmetric DD guards
- Vol arb = directional modifier → interacts with existing lev_multiplier
- Basis premium = already integrated, just extend to shorts

---

## CONCLUSION

**Key Findings**:
1. **7 major crypto quant strategy categories** identified, 4 compatible with 4H timeframe
2. **Crypto-specific alpha** (funding, basis, 24/7 trading) offers structural edges vs TradFi
3. **Top funds** (Jump, Jane Street) focus on HFT; **4H niche underserved** (zenQuant's advantage)
4. **Top 3 opportunities**: Pairs trading (Sharpe +0.5), Vol arb (Sharpe +0.3), Basis enhancement (Sharpe +0.1)

**Next Steps**:
1. Run BTC-ETH cointegration test (ADF) on historical 4H data
2. Fetch Deribit IV data, backtest GARCH IV/RV spread signal
3. Extend basis premium guard to short positions
4. Validate all changes through 7-gate harness + adversarial scenarios

**Estimated Impact**:
- Combined Sharpe improvement: +0.6-1.0 (if all 3 deployed)
- Diversification: Market-neutral pairs + vol overlay reduce correlation to BTC beta
- Drawdown: Basis guard reduces tail risk during liquidity crises

**Timeline**: 
- Phase 1 (basis + vol): 1 week
- Phase 2 (pairs trading): 2-3 weeks
- Phase 3 (exploration): 1-2 months

---

## SOURCES

### Academic Papers
- [Quantitative Alpha in Crypto Markets - Mann (2025)](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=5225612)
- [Statistical Arbitrage in Cryptocurrency Markets - Fischer et al. (2019)](https://www.mdpi.com/1911-8074/12/1/31)
- [Exploring Sources of Statistical Arbitrage - Kristoufek & Bouri (2023)](https://www.sciencedirect.com/science/article/abs/pii/S1544612322005116)
- [Statistical Arbitrage Using Cointegration - IJSRA (2026)](https://ijsra.net/sites/default/files/fulltext_pdf/IJSRA-2026-0283.pdf)
- [Pricing Cryptocurrency Options with VOV - Du et al. (2025)](https://onlinelibrary.wiley.com/doi/10.1002/fut.70029)
- [Volatility Models for Cryptocurrencies - ScienceDirect](https://www.sciencedirect.com/science/article/abs/pii/S1042443121001359)
- [Bitcoin Price Direction Prediction Using On-Chain Data - ScienceDirect](https://www.sciencedirect.com/science/article/pii/S266682702500057X)

### Industry Research
- [Bitcoin Basis Trading - CFB Benchmarks](https://www.cfbenchmarks.com/blog/revisiting-the-bitcoin-basis-how-momentum-sentiment-impact-the-structural-drivers-of-basis-activity)
- [Funding Rate Arbitrage - ScienceDirect (2025)](https://www.sciencedirect.com/science/article/pii/S2096720925000818)
- [Inside Jump Crypto - Insights4VC](https://insights4vc.substack.com/p/inside-jump-crypto-13b-terra-trade)
- [Top Crypto Market Makers - Empirica](https://empirica.io/blog/market-makers-and-liquidity-providers-list-review-rating/)
- [Crypto Pairs Trading - Amberdata](https://blog.amberdata.io/crypto-pairs-trading-why-cointegration-beats-correlation)
- [Delta-Neutral Strategy - Amberdata](https://blog.amberdata.io/delta-neutral-strategy-risk-opportunity-in-crypto-derivatives)
- [On-Chain Data for Trading - Amberdata](https://blog.amberdata.io/onchain-data-for-algorithmic-trading-strategy-development)
- [Systematic Crypto Trading Strategies - Medium](https://medium.com/@briplotnik/systematic-crypto-trading-strategies-momentum-mean-reversion-volatility-filtering-8d7da06d60ed)

### Practitioner Guides
- [How to Arbitrage with Crypto Futures - BitMEX](https://blog.bitmex.com/arbitrage-in-crypto/)
- [What is Basis Trading - Backpack](https://learn.backpack.exchange/articles/what-is-basis-trading)
- [Funding Rate Arbitrage Guide - CoinGlass](https://www.coinglass.com/learn/what-is-funding-rate-arbitrage)
- [Volatility Trading Strategies - PickMyTrade (2026)](https://blog.pickmytrade.trade/volatility-trading-strategies-2026-guide/)
- [DeFi Yield Farming Strategies - Coin Bureau (2026)](https://coinbureau.com/analysis/best-defi-yield-farming-platforms)
- [Mean Reversion Trading in Crypto - Stoic AI](https://stoic.ai/blog/mean-reversion-trading-how-i-profit-from-crypto-market-overreactions/)

### Data Providers
- [CryptoQuant - On-Chain Analytics](https://cryptoquant.com/)
- [Glassnode - Digital Asset Intelligence](https://glassnode.com/)
- [Volmex - Crypto Volatility Indices](https://volmex.finance/)
- [CoinDesk On-Chain Data](https://data.coindesk.com/on-chain-data)

---

**Document Version**: 1.0  
**Last Updated**: 2026-04-05  
**Next Review**: After Phase 1 implementation (1 week)
