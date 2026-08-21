# Quant V2 and OptionsState Shadow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the approved staged Quant V2 architecture experiment and a strictly observation-only BTC OptionsState sidecar.

**Architecture:** A frozen 4h `DecisionSnapshot` is the only source of approved exposure in the V2 experiment. Research stages add direction, confidence, volatility sizing, regime sizing, one no-trade band, and cost shadowing one at a time; OptionsState is joined point-in-time for reporting only.

**Tech Stack:** Python 3.13, frozen dataclasses, in-memory event bus, deterministic historical replay, PyArrow/Pandas, pytest.

**Spec:** `docs/superpowers/specs/2026-08-22-quant-v2-options-state-design.md`

## Global Constraints

- Preserve legacy behavior as the default.
- BTC only; all directional decisions occur on confirmed 4h close.
- Freeze August rank1, windows, five seeds, and cost assumptions; no search.
- OptionsState and A5 cost shadow must not affect approved targets or orders.
- Use TDD and single-purpose commits. Run focused tests after every task and `uv run zenquant-check` before completion.

---

### Task 1: Frozen 4h DecisionSnapshot contract

**Files:**
- Create: `zenquant/strategy/decision_snapshot.py`
- Modify: `zenquant/domain/models.py`, `zenquant/domain/__init__.py`, `zenquant/strategy/engine.py`
- Test: `tests/test_decision_snapshot.py`, `tests/test_plugin_integration.py`

**Interfaces:**
- Produces immutable `DecisionSnapshot`, `build_decision_snapshot(...)`, and event `strategy.decision_snapshot.4h.<symbol>`.
- The stable `decision_id` is a SHA-256 digest of canonical snapshot inputs and excludes wall-clock time.

- [ ] Write tests for frozen mutation failure, 4h-only construction, stable replay identity, formula/config identity, and event publication.
- [ ] Run the focused tests and verify the new contract fails because it does not exist.
- [ ] Implement the minimal frozen type, canonical builder, and explicit V2 publication path.
- [ ] Verify focused tests and existing strategy/plugin integration tests pass.
- [ ] Commit as `feat(strategy): add frozen 4h decision snapshot`.

### Task 2: A0/A1 causal direction attribution

**Files:**
- Create: `zenquant/research/architecture_experiment.py`, `scripts/run_architecture_experiment.py`
- Modify: `zenquant/historical/replay.py`
- Test: `tests/test_architecture_experiment.py`

**Interfaces:**
- Produces stages `A0`, `A1_SIGN`, and `A1_SCORE` plus per-window/per-seed attribution JSON.
- A1_SIGN uses `sign(raw_score)`; A1_SCORE multiplies that sign by frozen confidence. Both use fixed 1x notional and no compounding.

- [ ] Write failing tests proving A1 isolation, fixed-notional accounting, coverage, direction hit rate, cost stress, and regime splits.
- [ ] Implement pure stage transforms and report aggregation without parameter search.
- [ ] Verify A0 reproduces the frozen baseline and Options/risk/execution state cannot enter A1 outputs.
- [ ] Commit as `feat(research): add A1 direction attribution experiment`.

### Task 3: Episode ledger and Gate v3 profit factor

**Files:**
- Create: `zenquant/research/episode_ledger.py`, `zenquant/research/gate_v3.py`
- Modify: `zenquant/historical/replay.py`, `zenquant/domain/models.py`
- Test: `tests/test_episode_ledger.py`, `tests/test_gate_v3.py`

**Interfaces:**
- Consumes chronological fills and funding allocations.
- Produces closed/open `PositionEpisode` records and nullable PF plus status: `INSUFFICIENT_SAMPLE`, `NO_LOSS_EPISODES`, or `DEFINED`.

- [ ] Write failing tests for flat-entry, adds, partial reductions, flat exits, flips split at zero, proportional flip fees, funding, open episodes, MFE/MAE, and PF boundaries.
- [ ] Implement deterministic episode accounting and expose episode metrics from replay.
- [ ] Replace Gate v3 hard PF with episode PF; keep fill/action PF diagnostic only.
- [ ] Verify gate cadence: at most 1/day no flag, 1-2/day soft, above 2/day hard, above 5/day hard under every policy.
- [ ] Commit accounting and gate changes as separate single-purpose commits.

### Task 4: Target-tracking V2 execution purity

**Files:**
- Modify: `zenquant/execution/router.py`, `zenquant/domain/models.py`, `zenquant/historical/replay.py`
- Test: `tests/test_execution_router.py`, `tests/test_replay.py`, `tests/test_parity.py`

**Interfaces:**
- Adds `execution_authority_mode="legacy" | "target_tracking_v2"`, default `legacy`.
- Every V2 order audit references a decision ID, approved target, positions before/after, and optional hard-safety reason.

- [ ] Write failing tests for delta direction, no overshoot, no autonomous DCA/profit protect/quality/squeeze/flip behavior, and safety reduce-only behavior.
- [ ] Add the smallest isolated V2 branch in the execution kernel; leave legacy branches unchanged.
- [ ] Add purity counters to replay and prove all three violation counters remain zero.
- [ ] Verify backtest/replay/live-adapter parity and commit as `feat(execution): add pure target tracking mode`.

### Task 5: A2-A5-shadow incremental stages

**Files:**
- Modify: `zenquant/research/architecture_experiment.py`, `zenquant/strategy/decision_snapshot.py`
- Test: `tests/test_architecture_experiment.py`, `tests/test_decision_snapshot.py`

**Interfaces:**
- Adds stages `A2`, `A3`, `A4`, and `A5_SHADOW` without changing earlier stages.
- A5 records cost observations with forward 1/3/6-bar returns but never returns a block decision.

- [ ] Write failing tests for 20% volatility targeting, 1.5x cap, invalid-vol flat target, fixed regime mapping, one 4% band, safety bypasses, and non-blocking cost observations.
- [ ] Implement each stage as one pure transform and preserve earlier-stage golden results.
- [ ] Emit cumulative comparison reports with target tracking error, time-to-target, shortfall, slippage, overshoot, reversal, and executor-created exposure.
- [ ] Commit each causal stage separately.

### Task 6: OptionsState calculations and point-in-time store

**Files:**
- Create: `zenquant/data/options_state.py`
- Modify: `zenquant/data/options_store.py`, `zenquant/data/deribit_options_chain.py`, `zenquant/domain/models.py`
- Test: `tests/test_options_state.py`, `tests/test_options_store.py`

**Interfaces:**
- Produces immutable `OptionsState` at a confirmed 4h cutoff.
- Includes 7d/30d/90d ATM IV, Black-76 25-delta put/call IV and RR, 30d IV-RV, term slopes, 24h spot/skew change, OI concentrations, normalized GEX distances, trend quality, source age, and quality flags.

- [ ] Write failing tests for Black-76 delta selection, tenor selection, IV unit normalization, IV-RV, term slopes, conditional skew labels, OI/GEX fields, point-in-time lookup, and stale/missing neutral behavior.
- [ ] Rename the old 90%/110% skew output as an explicit moneyness proxy while preserving compatibility aliases only at legacy boundaries.
- [ ] Implement deterministic state construction and completeness diagnostics.
- [ ] Commit as `feat(data): add point-in-time options state shadow`.

### Task 7: Options shadow isolation and counterfactual report

**Files:**
- Modify: `zenquant/strategy/decision_snapshot.py`, `zenquant/historical/replay.py`
- Create: `zenquant/research/options_shadow.py`
- Test: `tests/test_options_shadow.py`, `tests/test_replay.py`

**Interfaces:**
- Decision snapshots may reference an `options_state_id` but target formulas cannot consume OptionsState fields.
- Produces OFF-vs-shadow reporting for hostile MDD, CVaR, tail loss, and trend return retention.

- [ ] Write a contract test comparing orders, fills, equity, and approved targets byte-for-byte with shadow disabled/enabled.
- [ ] Join only the latest OptionsState whose source timestamp is not after the 4h cutoff.
- [ ] Implement the counterfactual report without activating the shadow multiplier.
- [ ] Commit as `feat(research): add options shadow counterfactual report`.

### Task 8: Integrated Gate v3 verification and handoff

**Files:**
- Modify: `README.md`, `CLAUDE.md`, `docs/LIVE_VALIDATION_HANDOFF_SPEC.md`
- Test: existing harness and full suite.

- [ ] Run all focused suites, five deterministic robustness seeds, and A0-A5 fixed-window reports.
- [ ] Run `uv run zenquant-check` and the full pytest suite.
- [ ] Document commands, artifacts, stage gates, known limitations, and the explicit prohibition on activating OptionsState without new approval.
- [ ] Commit documentation separately as `docs: document Quant V2 architecture experiment`.

