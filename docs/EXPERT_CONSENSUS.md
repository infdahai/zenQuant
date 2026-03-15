# Expert Consensus on ZenQuant Architecture

**Date**: 2026-03-15
**Status**: Phase 3 Implementation In Progress

---

## Executive Summary

Two independent quantitative trading experts reviewed ZenQuant's architecture and both reached the **same core conclusion**:

> **For medium-term (4H/1D) crypto trading, edge comes from correct state abstraction and disciplined playbook execution, not from heavier AI models.**

This document synthesizes their recommendations and provides implementation guidance.

---

## Core Agreement Between Experts

### What Both Experts Agree On

1. **Rules can go very far** - Especially for medium-term systems with clear regime shifts
2. **State abstraction is critical** - Decompose "regime" into orthogonal dimensions
3. **Playbook-driven architecture** - Different market states require different trading logic
4. **Lightweight AI has a place** - But only for state confidence, not final decisions
5. **Academic trends ≠ production edge** - Papers chase novelty (heavy AI), production needs stability

### Key Architectural Layers (Both Experts)

```
Canonical Data Layer
    ↓
Temporal Feature Layer
    ↓
State Engine (4D: directional, volatility, positioning, quality)
    ↓
Playbook Engine (Bull/Bear/Range/Preservation)
    ↓
Sizing Engine (base × confidence × stress × haircut)
    ↓
Exit Engine (invalidation, trailing, time, regime)
    ↓
Risk Overlay (final veto)
    ↓
Execution + Audit
```

---

## Expert 1: Playbook Architecture Focus

### Key Insights

- **Problem**: Current system is feature-driven, not state-driven
- **Solution**: Explicit state → playbook → sizing → exit layers
- **Why it matters**: Medium-term edge comes from correct playbook switching, not more indicators

### Recommended Phases

1. **Phase 3**: State Engine (4D state decomposition)
2. **Phase 4**: Playbook Engine (4 core playbooks)
3. **Phase 5**: Sizing Engine (composable sizing function)
4. **Phase 6**: Exit Engine (4 exit types)
5. **Phase 7**: Funding/OI integration (into positioning state)
6. **Phase 8**: Optional ML for state confidence

### Where Lightweight AI Fits

✅ **Good uses**:
- State confidence estimation
- Transition prediction (early warning)
- Anomaly detection

❌ **Bad uses**:
- Direct long/short prediction
- Direct leverage sizing
- Direct exit triggers

---

## Expert 2: Rules vs. AI Emphasis

### Key Insights

- **Academic reality**: Yes, top papers are increasingly AI-focused
- **Production reality**: Many profitable systems are still rules + small models
- **Critical distinction**: Papers chase novelty, production needs auditability

### Why Rules Work for ZenQuant

Your system characteristics favor rules:
- ✅ Medium-low frequency (4H/1D)
- ✅ Clear regime shifts
- ✅ Risk management > single-point prediction
- ✅ Replay/audit/explainability required
- ✅ Imperfect data
- ✅ Execution costs matter

### What Kills Medium-Term Strategies

❌ Trading wrong playbook in wrong state
❌ Oversizing in stress states
❌ Holding losers too long (no invalidation exit)
❌ Cutting winners too early (no playbook-aware trailing)
❌ Ignoring positioning stress

### What This Architecture Fixes

✅ Playbook switching (right logic for right state)
✅ Stress-aware sizing (smaller in hostile conditions)
✅ Invalidation exits (cut losers fast)
✅ Playbook-aware trailing (let winners run in trends)
✅ Positioning state (avoid crowded trades)

---

## Synthesis: Implementation Priorities

### Immediate (Phase 3 - In Progress)

**State Engine Foundation**

Current status: ✅ Implemented, tests passing

What's done:
- 4D state classification (directional, volatility, positioning, quality)
- Hysteresis to avoid rapid state flipping
- Transition risk indicator (0.0 = stable, 1.0 = about to flip)
- Backward compatibility (legacy `regime` field preserved)
- Helper methods (`overall_confidence()`, `is_tradable()`)

What remains:
- [ ] Dashboard visualization of 4D state
- [ ] PostgreSQL persistence for `MarketState`
- [ ] Backtest validation (returns should match Phase 2)

### Next (Phase 4)

**Playbook Engine**

Goal: Explicit playbook selection based on market state

Tasks:
1. Define `PlaybookSelection` domain object
2. Implement 4 core playbooks:
   - **Bull Continuation**: UPTREND + healthy positioning → wide trailing, allow pyramid
   - **Bear Defense**: DOWNTREND + healthy positioning → tight trailing, no pyramid
   - **Range Light**: NEUTRAL + low vol → small size, quick exits
   - **Capital Preservation**: DISTORTED vol OR UNSAFE quality → reduce-only
3. Integrate into strategy engine
4. Dashboard shows active playbook + confidence

### Future (Phase 5-6)

**Sizing and Exit Engines**

- Extract sizing into composable function
- Implement 4 exit types (invalidation, trailing, time, regime)
- Make trailing stop playbook-aware

### Later (Phase 7-8)

**Funding/OI and Optional ML**

- Feed funding/OI into positioning state (not direct direction)
- Optional HMM for state confidence (not final decisions)

---

## Where Lightweight AI Fits (Consensus)

### The Right Place: State Engine

```
Features → State Engine → Playbook → Sizing → Exit → Risk → Execution
              ↑
         Optional ML
         (confidence only)
```

**What ML should do**:
- Improve state classification confidence
- Detect state transitions early
- Flag anomalous market behavior

**What ML should NOT do**:
- Replace playbook selection
- Directly emit leverage
- Directly trigger exits

### Why This Matters

If your rules + small model can't find edge, a bigger model probably won't either. The real alpha comes from:
1. Correct state identification
2. Correct playbook switching
3. Correct sizing in stress
4. Correct exit discipline

---

## Response to "Are Top Papers All AI Now?"

### Yes, but...

**Academic Reality**: Papers increasingly use transformers, deep RL, neural regime models

**Why**: Academic incentives reward novelty, not deployability

**Production Reality**: Many profitable systems are still:
- Rules + factors + state machines
- Small models for specific tasks
- Hybrid architectures (rules + ML enhancement)

### Examples from Recent Papers

1. **Quantformer** (2024): Transformer for factor construction
   - Academic: Novel architecture
   - Production: Unclear if it beats simpler models after transaction costs

2. **TLOB** (2025): Transformer for order book prediction
   - Paper's own finding: "Simple MLP can beat many SOTA models"
   - Lesson: Complexity ≠ edge

3. **Tactical Asset Allocation** (2025): ML + regime detection
   - Uses ML for regime classification (good)
   - Not end-to-end black box (good)

### Takeaway

Papers chase AI because that's what gets published. But for your 4H/1D system with deterministic requirements, **state-driven rules + optional small model enhancement** is the right architecture.

---

## Critical Success Factors

### What Makes This Architecture Work

1. **Orthogonal state dimensions** - Volatility can be high in both uptrends and downtrends
2. **Explicit playbook switching** - Same indicator values → different actions in different states
3. **Composable sizing** - Transparent breakdown (base × confidence × stress × haircut)
4. **Professional exits** - Exits are first-class, not afterthought
5. **Backward compatibility** - Legacy `regime` field preserved during migration

### What Would Make It Fail

❌ Adding more features without better state abstraction
❌ Premature AI-ification (end-to-end neural trader)
❌ Ignoring execution costs and state recovery
❌ Optimizing to one historical period
❌ Confusing "more signals" with "better strategy"

---

## Validation Strategy

### Phase 3 Validation (Current)

**Goal**: Verify state engine doesn't change returns

Test:
1. Run backtest with Phase 3 state engine
2. Compare returns to Phase 2 baseline
3. **Expected**: Identical returns (state is richer, but strategy logic unchanged)

### Phase 4 Validation

**Goal**: Verify playbook switching improves returns

Test:
1. Run backtest with playbook engine
2. Compare to Phase 3 baseline
3. **Expected**: Better risk-adjusted returns (better state → better decisions)

### Phase 5-6 Validation

**Goal**: Verify sizing/exit improvements

Test:
1. Stress-aware sizing should reduce drawdowns
2. Professional exits should improve profit factor
3. **Expected**: Higher Sharpe, lower max drawdown

---

## Final Recommendation

### For Your Current Project

✅ **Continue Phase 3** - State engine is the right foundation
✅ **Plan Phase 4** - Playbook engine is the missing strategic layer
✅ **Defer heavy AI** - Not needed until state/playbook/sizing/exit are mature

### One-Sentence Summary

**ZenQuant should evolve from a feature-driven strategy to a state-driven playbook architecture, using lightweight AI only for state confidence enhancement, not final trading decisions.**

---

## References

### Internal Documents
- `docs/PLAYBOOK_ARCHITECTURE.md` - Detailed architectural design
- `docs/PLAYBOOK_IMPLEMENTATION_GUIDE.md` - Code examples and migration path
- `AGENTS.md` - Product objectives and non-negotiable rules
- `README.md` - Project overview and roadmap

### Expert Opinions
- Expert 1: Playbook architecture proposal (2026-03-15)
- Expert 2: Rules vs. AI analysis (2026-03-15)

### Academic References
- Quantformer (2024): Transformer for quantitative trading
- TLOB (2025): Transformer for order book prediction
- Tactical Asset Allocation (2025): ML + regime detection

---

**End of Document**
