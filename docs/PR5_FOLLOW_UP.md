# PR #5 Follow-Up

Date: 2026-03-16

## Review Triage

The PR review on `#5` contains one class of issue that was worth treating as merge-relevant and several that are better handled as explicit follow-up work.

### Merge-Relevant

- Decision-chain test failures in [tests/test_e2e_decision_chain.py](/Users/disaster/workspace/code/zenQuant/tests/test_e2e_decision_chain.py)

Status:
- No longer failing on the current branch
- Verified with:
  - `uv run python -m unittest tests.test_e2e_decision_chain`
  - `uv run pytest -q`

Current result:
- `64 passed, 4 skipped`

Conclusion:
- The blocking review comment appears stale against the current branch state.

## Why 4 Tests Are Skipped

The skipped tests are intentional and should remain opt-in because they depend on live external systems.

### Skipped Tests

1. `tests/test_deribit_api_validation.py`
   Reason:
   - requires live Deribit network access
   - gated by `ZENQUANT_RUN_DERIBIT_NETWORK_TESTS=1`

2. `tests/test_deribit_api_validation.py`
   Reason:
   - requires live Deribit network access
   - gated by `ZENQUANT_RUN_DERIBIT_NETWORK_TESTS=1`

3. `tests/test_deribit_api_validation.py`
   Reason:
   - requires live Deribit network access
   - gated by `ZENQUANT_RUN_DERIBIT_NETWORK_TESTS=1`

4. `tests/test_deribit_postgresql.py`
   Reason:
   - requires PostgreSQL DSN
   - gated by `ZENQUANT_TEST_PG_DSN`
   - also requires `ZENQUANT_RUN_DERIBIT_NETWORK_TESTS=1`

### Rationale

These tests should not run by default because:

- Deribit is an external dependency with non-deterministic availability and latency.
- PostgreSQL-backed tests require a configured local or CI database.
- The default suite should remain deterministic, fast, and suitable for replay-safe development.

### How to Run the Skipped Tests

```bash
export ZENQUANT_RUN_DERIBIT_NETWORK_TESTS=1
uv run python -m unittest tests.test_deribit_api_validation -v

export ZENQUANT_TEST_PG_DSN="postgresql://user:pass@host:5432/dbname"
uv run python -m unittest tests.test_deribit_postgresql -v
```

## Follow-Up Work Worth Doing

These are the review comments that are worth addressing, but not inside PR #5.

### 1. Parameterize Strategy Thresholds

Files:
- [zenquant/strategy/playbook_engine.py](/Users/disaster/workspace/code/zenQuant/zenquant/strategy/playbook_engine.py)
- [zenquant/strategy/sizing_engine.py](/Users/disaster/workspace/code/zenQuant/zenquant/strategy/sizing_engine.py)
- [zenquant/strategy/exit_engine.py](/Users/disaster/workspace/code/zenQuant/zenquant/strategy/exit_engine.py)

Why:
- The hardcoded thresholds are strategy semantics, not presentation.
- They should move into explicit config only when there is a validation plan behind them.

Scope:
- Extract current magic thresholds into config objects.
- Preserve existing defaults exactly.
- Add tests proving default behavior is unchanged.

Non-goal:
- Do not tune the values in the same change.

### 2. Unify `transition_risk` Semantics

Files:
- [zenquant/strategy/playbook_engine.py](/Users/disaster/workspace/code/zenQuant/zenquant/strategy/playbook_engine.py)
- [zenquant/strategy/exit_engine.py](/Users/disaster/workspace/code/zenQuant/zenquant/strategy/exit_engine.py)

Why:
- The review correctly points out that playbook selection and exit logic use different `transition_risk` thresholds.
- That may be intentional, but if so it should be explicit rather than implicit.

Scope:
- Decide whether the thresholds should be:
  - shared
  - tiered by design
  - or documented as intentionally different
- Add a focused test proving the intended handoff between playbook de-risking and exit de-risking.

### 3. Revisit Confidence Blending

Files:
- [zenquant/strategy/sizing_engine.py](/Users/disaster/workspace/code/zenQuant/zenquant/strategy/sizing_engine.py)
- [tests/test_sizing_engine.py](/Users/disaster/workspace/code/zenQuant/tests/test_sizing_engine.py)

Why:
- The current 50/50 arithmetic blend is simple and deterministic, but the review is correct that it is not especially well-justified.

Scope:
- Compare current arithmetic mean against:
  - geometric mean
  - min-based blend
  - regime-aware weighting
- Do this as research or a separate guarded change, not as an unvalidated refactor.

Non-goal:
- Do not silently change production sizing semantics in a documentation/test PR.

### 4. Backtest Methodology Documentation

Files:
- [README.md](/Users/disaster/workspace/code/zenQuant/README.md)
- new doc under [docs](/Users/disaster/workspace/code/zenQuant/docs)

Why:
- This is worth doing because it improves auditability without changing runtime behavior.

Scope:
- Record:
  - data sources
  - training/evaluation windows
  - fee assumptions
  - slippage assumptions
  - replay constraints
  - known limitations

### 5. Playbook / Exit Analytics

Files:
- likely new module under `zenquant/app` or `zenquant/historical`

Why:
- Hit rate by exit type and profit factor by playbook are useful, but they are reporting features, not PR #5 blockers.

Scope:
- Build from existing execution reports and published decisions.
- Keep analytics read-only and post-trade.

Non-goal:
- Do not mix analytics state into execution or UI code paths.

## Recommended Order

1. Parameterize thresholds without changing defaults.
2. Resolve and document `transition_risk` semantics.
3. Add methodology documentation.
4. Add analytics/reporting.
5. Only then consider changing confidence blending.

## Current Recommendation

PR #5 should not be held open for the non-blocking review comments above.

The only truly merge-relevant issue was test health, and the current branch already verifies cleanly under the default suite.
