# Trend Participation Donor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement an isolated fixed-1x BTC trend donor that compares exact `z84` sign with frozen hysteresis and determines whether it captures persistent trends profitably after 15bp stress.

**Architecture:** A pure daily signal module produces latent state and diagnostics; a private replay adapter projects PIT safety and converts research intents into independently timed 5m execution events. Separate accounting, placebo, perturbation, trend-capture, and acceptance modules produce deterministic artifacts without importing into production paths.

**Tech Stack:** Python 3.12, frozen dataclasses, NumPy/pandas for offline statistics, existing execution fee/slippage primitives and episode ledger, pytest, JSON/Parquet artifacts.

**Spec:** `docs/superpowers/specs/2026-08-24-trend-participation-two-layer-design.md`

## Global Constraints

- Active signal source is only `BINANCE_SPOT_BTCUSDT_1D`; spot fallback is `NONE`.
- `z84` uses 85 consecutive closes, 84 log returns, sample standard deviation `ddof=1`, and `sigma_floor=1e-12`.
- A daily bar available at 00:00 UTC first affects the strictly later 04:00 UTC decision.
- Compare only `z84_sign_fixed_1x` and `z84_hysteresis_fixed_1x`; August A5 is appendix-only.
- Starting capital and notional are fixed, leverage is 1x, and compounding is disabled.
- Actual-fill gross PnL already contains slippage; implementation shortfall is diagnostic and is not deducted twice.
- Stress costs use `realized_turnover_notional`; a completed direct flip targets 2x but partial fills pay only realized costs.
- Forward FSM and ledger start flat; pre-start rows warm only rolling buffers.
- Production strategy, risk, execution, domain snapshots, A1-A5, and OptionsState remain unchanged.
- Every task is handled by a fresh implementation subagent and two fresh reviewers before commit.

## File Map

- Create `zenquant/research/trend_participation.py`: z-score math, latent FSM, diagnostics, monthly labels, perturbations, placebo, and acceptance.
- Create `zenquant/research/trend_participation_replay.py`: private intent, safety projection, execution-time adapter, fill/accounting result.
- Create `scripts/run_trend_participation.py`: registered diagnostic/forward runner and artifact writer.
- Create `tests/test_trend_participation.py`: signal/FSM/diagnostic/statistics tests.
- Create `tests/test_trend_participation_replay.py`: execution, partial fill, safety, funding, and cost tests.
- Create `tests/test_trend_participation_cli.py`: identity, evidence-role, artifact, and source-drift tests.
- Modify `tests/test_import_boundaries.py`: block production imports.
- Reuse without schema changes: `zenquant/research/episode_ledger.py`, `zenquant/research/accounting_kernel_identity.py`, and fee/slippage primitives from `zenquant.execution`.

---

### Task 1: Exact Daily Signal and Availability Barrier

**Files:**
- Create: `zenquant/research/trend_participation.py`
- Test: `tests/test_trend_participation.py`

**Interfaces:**
- Produces: `DailyClose`, `TrendSignalObservation`, `compute_z_score(closes, lookback)`, `first_eligible_4h_cutoff(available_at_ns)`.

- [ ] **Step 1: Write failing math and PIT tests**

```python
def test_z84_uses_exactly_85_closes_and_sample_std() -> None:
    closes = tuple(math.exp(index / 1000) for index in range(85))
    expected = math.log(closes[-1] / closes[0]) / (statistics.stdev(_log_returns(closes)) * math.sqrt(84))
    assert compute_z_score(closes, 84) == pytest.approx(expected)

def test_midnight_daily_close_first_reaches_0400_decision() -> None:
    assert first_eligible_4h_cutoff(MIDNIGHT_NS) == MIDNIGHT_NS + FOUR_HOURS_NS
```

- [ ] **Step 2: Run focused tests and confirm RED**

Run: `uv run pytest tests/test_trend_participation.py -q`

Expected: module import fails.

- [ ] **Step 3: Implement exact signal math**

```python
@dataclass(frozen=True, slots=True)
class TrendSignalObservation:
    signal_id: str
    source_cutoff_ns: int
    available_at_ns: int
    first_decision_cutoff_ns: int
    z84: float | None
    status: str
    quality_flags: tuple[str, ...]

def first_eligible_4h_cutoff(available_at_ns: int) -> int:
    return ((available_at_ns // FOUR_HOURS_NS) + 1) * FOUR_HOURS_NS
```

Reject missing/duplicate/non-consecutive/non-finite rows, non-positive closes, fewer than `lookback+1` closes, and sigma at or below `1e-12`. A real gap clears the rolling buffer; 85 new consecutive closes are required.

- [ ] **Step 4: Run focused tests and confirm GREEN**

Run: `uv run pytest tests/test_trend_participation.py -q`

Expected: all signal tests pass.

- [ ] **Step 5: Commit signal math**

```bash
git add zenquant/research/trend_participation.py tests/test_trend_participation.py
git commit -m "feat(research): add exact z84 trend signal"
```

### Task 2: Frozen Sign and Hysteresis State Machines

**Files:**
- Modify: `zenquant/research/trend_participation.py`
- Modify: `tests/test_trend_participation.py`

**Interfaces:**
- Produces: `TrendSide`, `next_sign_state()`, `next_hysteresis_state()`, `run_latent_path()`.

- [ ] **Step 1: Write the complete transition-table tests**

```python
@pytest.mark.parametrize(("state", "z84", "expected"), [
    (TrendSide.FLAT, .50, TrendSide.LONG),
    (TrendSide.FLAT, -.50, TrendSide.SHORT),
    (TrendSide.LONG, -.50, TrendSide.SHORT),
    (TrendSide.LONG, -.10, TrendSide.FLAT),
    (TrendSide.SHORT, .50, TrendSide.LONG),
    (TrendSide.SHORT, .10, TrendSide.FLAT),
])
def test_hysteresis_transitions_are_mutually_exclusive(state, z84, expected) -> None:
    assert next_hysteresis_state(state, z84) is expected

def test_unknown_resets_state_and_recovery_starts_flat() -> None:
    assert next_hysteresis_state(TrendSide.LONG, None) is TrendSide.UNKNOWN
    assert recover_after_warmup(TrendSide.UNKNOWN, .25) is TrendSide.FLAT
```

- [ ] **Step 2: Run and confirm RED**

Run: `uv run pytest tests/test_trend_participation.py -q`

Expected: state functions are missing.

- [ ] **Step 3: Implement both paths as pure functions**

```python
def next_hysteresis_state(state: TrendSide, z84: float | None) -> TrendSide:
    if z84 is None:
        return TrendSide.UNKNOWN
    if state is TrendSide.LONG:
        return TrendSide.SHORT if z84 <= -.50 else TrendSide.FLAT if z84 <= 0 else TrendSide.LONG
    if state is TrendSide.SHORT:
        return TrendSide.LONG if z84 >= .50 else TrendSide.FLAT if z84 >= 0 else TrendSide.SHORT
    return TrendSide.LONG if z84 >= .50 else TrendSide.SHORT if z84 <= -.50 else TrendSide.FLAT
```

`run_latent_path()` initializes `FLAT` at the registered boundary, applies at most one daily transition, and emits a stable ID for every observation.

- [ ] **Step 4: Run focused tests and confirm GREEN**

Run: `uv run pytest tests/test_trend_participation.py -q`

Expected: all transition tests pass.

- [ ] **Step 5: Commit state machines**

```bash
git add zenquant/research/trend_participation.py tests/test_trend_participation.py
git commit -m "feat(research): add frozen trend hysteresis"
```

### Task 3: Diagnostic-Only Time-Space Features

**Files:**
- Modify: `zenquant/research/trend_participation.py`
- Modify: `tests/test_trend_participation.py`

**Interfaces:**
- Produces: `TrendDiagnostics`, `build_trend_diagnostics(rows, latent_states)`.

- [ ] **Step 1: Write failing diagnostic formulas and no-authority test**

```python
def test_path_efficiency_and_curvature_are_exact() -> None:
    row = build_trend_diagnostics(_daily_path(), _states())[-1]
    assert row.path_efficiency_28 == pytest.approx(abs(_net_log_move()) / _sum_abs_log_moves())
    assert row.signed_curvature_7 == pytest.approx(row.z7 - _z7_seven_days_ago())

def test_changing_diagnostics_cannot_change_latent_state() -> None:
    assert run_latent_path(_signals(), diagnostics=_diagnostics_a()) == run_latent_path(_signals(), diagnostics=_diagnostics_b())
```

- [ ] **Step 2: Run and confirm RED**

Run: `uv run pytest tests/test_trend_participation.py -q`

Expected: diagnostic builder is missing.

- [ ] **Step 3: Implement frozen diagnostics**

```python
@dataclass(frozen=True, slots=True)
class TrendDiagnostics:
    cutoff_ns: int
    trend_age_days: int | None
    log_trend_age: float | None
    breakout_age_4h_bars: int | None
    fast_slow_disagreement: bool | None
    signed_curvature_7: float | None
    path_efficiency_28: float | None
    episode_drawdown_sigma: float | None
    weekly_completion_fraction: float
    quality_flags: tuple[str, ...]
```

Compute z7/z14/z28 with the same return/std rules as z84. Use only `t-1` breakout boundaries. Keep every field out of `next_hysteresis_state()` arguments.

- [ ] **Step 4: Run focused tests and confirm GREEN**

Run: `uv run pytest tests/test_trend_participation.py -q`

Expected: all tests pass.

- [ ] **Step 5: Commit diagnostics**

```bash
git add zenquant/research/trend_participation.py tests/test_trend_participation.py
git commit -m "feat(research): add trend participation diagnostics"
```

### Task 4: PIT Safety Projection and Private Research Intent

**Files:**
- Create: `zenquant/research/trend_participation_replay.py`
- Test: `tests/test_trend_participation_replay.py`

**Interfaces:**
- Produces: `TrendParticipationResearchIntent`, `ResearchSafetySnapshot`, `project_research_target()`.

- [ ] **Step 1: Write failing safety-table tests**

```python
def test_reduce_only_never_crosses_zero() -> None:
    safety = _safety(reduce_only=True)
    assert project_research_target(latent_target=-1, current_filled=.4, safety=safety, decision_cutoff_ns=100) == 0

def test_cap_violation_only_reduces_current_exposure() -> None:
    assert project_research_target(latent_target=1, current_filled=.8, safety=_safety(cap_violation=True, cap=.5), decision_cutoff_ns=100) == .5
    assert project_research_target(latent_target=1, current_filled=.2, safety=_safety(cap_violation=True, cap=.5), decision_cutoff_ns=100) == .2

def test_future_safety_snapshot_is_rejected() -> None:
    with pytest.raises(ValueError, match="available_at_ns"):
        project_research_target(1, 0, _safety(available_at_ns=101), decision_cutoff_ns=100)
```

- [ ] **Step 2: Run and confirm RED**

Run: `uv run pytest tests/test_trend_participation_replay.py -q`

Expected: replay module is missing.

- [ ] **Step 3: Implement exact projection precedence**

```python
@dataclass(frozen=True, slots=True)
class TrendParticipationResearchIntent:
    intent_id: str
    signal_decision_cutoff_ns: int
    latent_target: int
    approved_target: float
    safety_snapshot_id: str

def project_research_target(
    latent_target: int,
    current_filled: float,
    safety: ResearchSafetySnapshot,
    *,
    decision_cutoff_ns: int,
) -> float:
    if safety.available_at_ns > decision_cutoff_ns:
        raise ValueError("safety available_at_ns exceeds decision cutoff")
    if safety.kill or safety.unsafe:
        return 0.0
    if safety.reduce_only:
        return 0.0 if current_filled * latent_target <= 0 else math.copysign(min(abs(current_filled), abs(latent_target)), current_filled)
    if safety.cap_violation:
        return math.copysign(min(abs(current_filled), safety.cap_value), current_filled) if current_filled else 0.0
    return float(latent_target)
```

Safety does not mutate latent state. Recovery can alter approved exposure only on the next confirmed 4h decision.

- [ ] **Step 4: Run focused tests and confirm GREEN**

Run: `uv run pytest tests/test_trend_participation_replay.py -q`

Expected: all safety tests pass.

- [ ] **Step 5: Commit research intent**

```bash
git add zenquant/research/trend_participation_replay.py tests/test_trend_participation_replay.py
git commit -m "feat(research): add trend research safety projection"
```

### Task 5: Strictly Later 5m Replay Adapter

**Files:**
- Modify: `zenquant/research/trend_participation_replay.py`
- Modify: `tests/test_trend_participation_replay.py`

**Interfaces:**
- Produces: `TrendParticipationReplayAdapter.run(...) -> TrendReplayResult` with separate decision and execution timestamps.

- [ ] **Step 1: Write failing timing, residual, and isolation tests**

```python
def test_same_timestamp_5m_open_is_never_executable() -> None:
    result = _adapter().run(intents=(_intent(cutoff=100),), bars=(_bar(start=100), _bar(start=105)))
    assert result.fills[0].transaction_time_ns == 105

def test_new_4h_authority_cancels_old_residual_before_retarget() -> None:
    result = _capped_adapter(.4).run(intents=(_intent(100, 1), _intent(200, -1)), bars=_bars())
    assert result.audit.old_residual_cancelled_at_ns == 200

def test_adapter_uses_private_subject_namespace() -> None:
    assert all(subject.startswith("research.trend_participation.") for subject in _adapter().published_subjects)
```

- [ ] **Step 2: Run and confirm RED**

Run: `uv run pytest tests/test_trend_participation_replay.py -q`

Expected: adapter is missing.

- [ ] **Step 3: Implement the private event adapter**

```python
@dataclass(frozen=True, slots=True)
class ResearchExecutionEvent:
    intent_id: str
    signal_decision_cutoff_ns: int
    execution_event_ns: int
    reference_price: float

def first_execution_bar(intent_ns: int, bars: Sequence[FiveMinuteBar]) -> FiveMinuteBar:
    return next(bar for bar in bars if bar.start_ns > intent_ns)
```

Use a private `InMemoryJetStream` and isolated portfolio. If the existing router cannot preserve `execution_event_ns`, call only deterministic gateway fill-price/fee primitives and feed resulting `ExecutionReport` objects to `PositionEpisodeLedger`. Never rewrite the decision cutoff to the 5m time.

- [ ] **Step 4: Run focused tests and confirm GREEN**

Run: `uv run pytest tests/test_trend_participation_replay.py -q`

Expected: all adapter tests pass.

- [ ] **Step 5: Commit adapter**

```bash
git add zenquant/research/trend_participation_replay.py tests/test_trend_participation_replay.py
git commit -m "feat(research): add delayed trend replay adapter"
```

### Task 6: Actual-Fill Accounting and Episode Reconciliation

**Files:**
- Modify: `zenquant/research/trend_participation_replay.py`
- Modify: `tests/test_trend_participation_replay.py`

**Interfaces:**
- Produces: `TrendPathAccounting`, `account_trend_path(result, stress_bps)`.

- [ ] **Step 1: Write failing full/partial flip and no-double-slippage tests**

```python
def test_completed_flip_has_two_x_realized_turnover() -> None:
    accounting = account_trend_path(_fills(long_one_then_short_one()), stress_bps=15)
    assert accounting.realized_turnover_notional == pytest.approx(2 * FIXED_NOTIONAL)

def test_partial_flip_charges_only_actual_fills_and_splits_at_zero() -> None:
    accounting = account_trend_path(_partial_cross_zero_fills(), stress_bps=15)
    assert accounting.realized_turnover_notional == sum(abs(f.filled_qty) * f.avg_price for f in _partial_cross_zero_fills())
    assert accounting.episodes[0].closed and accounting.episodes[1].side == -1

def test_actual_fill_slippage_is_not_deducted_twice() -> None:
    accounting = account_trend_path(_one_round_trip(), stress_bps=0)
    assert accounting.net_pnl_0bp == pytest.approx(accounting.gross_pnl_from_actual_fills + accounting.funding - accounting.actual_fees)
```

- [ ] **Step 2: Run and confirm RED**

Run: `uv run pytest tests/test_trend_participation_replay.py -q`

Expected: accounting type/function is missing.

- [ ] **Step 3: Implement frozen identities**

```python
@dataclass(frozen=True, slots=True)
class TrendPathAccounting:
    gross_pnl_from_actual_fills: float
    funding: float
    actual_fees: float
    implementation_shortfall: float
    realized_turnover_notional: float
    net_pnl_0bp: float
    net_pnl_5bp: float
    net_pnl_15bp: float
    episodes: tuple[PositionEpisode, ...]
```

Allocate fees by fill notional at zero crossing, funding only to the open episode at settlement time, and mark open episodes separately. Stress formulas must read only `realized_turnover_notional`.

- [ ] **Step 4: Run accounting and existing ledger tests**

Run: `uv run pytest tests/test_trend_participation_replay.py tests/test_episode_ledger.py tests/test_accounting_kernel_identity.py -q`

Expected: all tests pass.

- [ ] **Step 5: Commit accounting**

```bash
git add zenquant/research/trend_participation_replay.py tests/test_trend_participation_replay.py
git commit -m "feat(research): account trend donor actual fills"
```

### Task 7: Paired Comparison, Perturbations, and Placebos

**Files:**
- Modify: `zenquant/research/trend_participation.py`
- Modify: `tests/test_trend_participation.py`

**Interfaces:**
- Produces: `build_paired_paths()`, `apply_trend_perturbation()`, `build_shift_placebos()`, `PairedTrendComparison`.

- [ ] **Step 1: Write failing pairing and common-intersection tests**

```python
def test_sign_and_hysteresis_share_events_but_have_independent_fills() -> None:
    comparison = build_paired_paths(_events(), _manifest())
    assert comparison.sign.source_sha256 == comparison.hysteresis.source_sha256
    assert comparison.sign.fill_ledger_sha256 != comparison.hysteresis.fill_ledger_sha256

def test_placebos_trim_84_days_from_both_ends_and_start_flat() -> None:
    placebos = build_shift_placebos(_signals(days=400), shifts=(-84,-56,-28,28,56,84))
    assert {path.start_ns for path in placebos} == {_signals(days=400)[84].cutoff_ns}
    assert all(path.initial_state is TrendSide.FLAT for path in placebos)
```

- [ ] **Step 2: Run and confirm RED**

Run: `uv run pytest tests/test_trend_participation.py -q`

Expected: comparison interfaces are missing.

- [ ] **Step 3: Implement deterministic paths**

Apply seeded zero-mean Gaussian `0.10%` price noise to copied daily closes and execution prices; apply independent `2%` dropout only to copied 4h/5m execution events. Preserve source-gap versus injected-dropout flags. Build all six non-wrapping shifts on the shared `[84:-84]` intersection with equal capital and independent fill ledgers.

- [ ] **Step 4: Run focused tests and confirm GREEN**

Run: `uv run pytest tests/test_trend_participation.py -q`

Expected: all perturbation/placebo tests pass.

- [ ] **Step 5: Commit robustness paths**

```bash
git add zenquant/research/trend_participation.py tests/test_trend_participation.py
git commit -m "feat(research): add trend donor robustness paths"
```

### Task 8: Persistent-Trend Capture and Acceptance Gate

**Files:**
- Modify: `zenquant/research/trend_participation.py`
- Modify: `tests/test_trend_participation.py`

**Interfaces:**
- Produces: `build_monthly_trend_grid()`, `evaluate_trend_donor() -> TrendDonorAcceptance`.

- [ ] **Step 1: Write failing outcome-independent grid and gate tests**

```python
def test_persistent_month_label_does_not_depend_on_donor_pnl() -> None:
    a = build_monthly_trend_grid(_prices(), _donor_pnl(winning=True))
    b = build_monthly_trend_grid(_prices(), _donor_pnl(winning=False))
    assert [m.persistent_trend for m in a] == [m.persistent_trend for m in b]

def test_capture_is_ratio_of_sums() -> None:
    report = summarize_capture((_month(donor=10, oracle=20), _month(donor=30, oracle=100)))
    assert report.net_capture_15 == pytest.approx(40 / 120)

def test_348_days_without_180_day_placebo_intersection_is_insufficient() -> None:
    result = evaluate_trend_donor(_evidence(common_days=179), _manifest())
    assert result.status == "INSUFFICIENT_SAMPLE"
```

- [ ] **Step 2: Run and confirm RED**

Run: `uv run pytest tests/test_trend_participation.py -q`

Expected: monthly/gate functions are missing.

- [ ] **Step 3: Implement exact evaluation**

Label every eligible UTC month persistent only when absolute spot log return is at least ex-ante `sigma84*sqrt(intervals)` and full-month path efficiency is at least `0.50`. Assign the month to the point-in-time regime bucket visible at the first eligible month decision; later hindsight states cannot reassign it. Use fixed-1x perpetual oracle boundary prices and ratio-of-sums capture. Implement canonical positive 15bp PnL, canonical paired delta at least `0.50%` of initial capital, one-sided 95% paired month-block lower bound above zero, 4/5 path rules, 20% switch reduction, MDD `<20%`, episode PF `>=1`, all-placebo dominance, three regime buckets, and switch ceilings. Reused evidence can only reject/continue/report insufficiency.

- [ ] **Step 4: Run focused tests and confirm GREEN**

Run: `uv run pytest tests/test_trend_participation.py -q`

Expected: all evaluation tests pass.

- [ ] **Step 5: Commit gate**

```bash
git add zenquant/research/trend_participation.py tests/test_trend_participation.py
git commit -m "feat(research): gate persistent trend participation"
```

### Task 9: Registered Runner and Artifact Publication

**Files:**
- Create: `scripts/run_trend_participation.py`
- Create: `tests/test_trend_participation_cli.py`

**Interfaces:**
- Consumes: hypothesis/execution manifests, dual receipt envelope, immutable source paths.
- Produces: direction attribution, episode ledger, acceptance JSON, and checkpoint envelope.

- [ ] **Step 1: Write failing CLI and evidence-role tests**

```python
def test_reused_runner_never_promotes_and_excludes_august_from_gate(tmp_path: Path) -> None:
    result = run_trend_participation(_args(tmp_path, role="TEST_REUSED_DIAGNOSTIC"))
    acceptance = json.loads(result.acceptance_path.read_text())
    assert acceptance["status"] != "ELIGIBLE_FOR_SEPARATE_ACTIVATION_REVIEW"
    assert "august_a5" not in acceptance["causal_paths"]

def test_source_mutation_aborts_publication(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="changed during run"):
        run_trend_participation(_mutating_args(tmp_path))
```

- [ ] **Step 2: Run and confirm RED**

Run: `uv run pytest tests/test_trend_participation_cli.py -q`

Expected: runner is missing.

- [ ] **Step 3: Implement deterministic publication**

```python
def run_trend_participation(args: RunArgs) -> RunResult:
    contract = load_registered_execution_contract(args.execution_manifest, args.registration_envelope)
    before = hash_source_set(contract.sources)
    paired = run_registered_paired_paths(contract, as_of_ns=args.as_of_ns)
    acceptance = evaluate_trend_donor(paired, contract.hypothesis)
    if hash_source_set(contract.sources) != before:
        raise RuntimeError("source corpus changed during run")
    return write_trend_artifacts_atomically(paired, acceptance, args.output_dir)
```

Artifacts must bind both manifests, receipts, source-set before/after hashes, accounting kernel, exact windows, all seeds and evidence roles. Each forward checkpoint appends a new envelope and never rewrites registration.

- [ ] **Step 4: Run CLI tests and confirm GREEN**

Run: `uv run pytest tests/test_trend_participation_cli.py -q`

Expected: all tests pass.

- [ ] **Step 5: Commit runner**

```bash
git add scripts/run_trend_participation.py tests/test_trend_participation_cli.py
git commit -m "feat(research): add registered trend donor runner"
```

### Task 10: Isolation, Full Verification, and First Diagnostic

**Files:**
- Modify: `tests/test_import_boundaries.py`
- Modify only for in-scope review fixes.

**Interfaces:**
- Produces: independently reviewed Layer 2 implementation with no live authority.

- [ ] **Step 1: Add and run production import boundary test**

```python
def test_production_modules_do_not_import_trend_participation() -> None:
    forbidden = forbidden_imports("zenquant.research.trend_participation", roots=("zenquant.strategy", "zenquant.risk", "zenquant.execution", "zenquant.app.live"))
    assert forbidden == []
```

Run: `uv run pytest tests/test_import_boundaries.py -q`

Expected: pass after only the test boundary changes.

- [ ] **Step 2: Run all donor tests**

Run: `uv run pytest tests/test_trend_participation.py tests/test_trend_participation_replay.py tests/test_trend_participation_cli.py tests/test_episode_ledger.py tests/test_accounting_kernel_identity.py -q`

Expected: all tests pass.

- [ ] **Step 3: Run parity and full harness**

Run: `uv run pytest tests/test_parity.py tests/test_architecture_experiment.py tests/test_options_shadow.py -q`

Run: `uv run zenquant-check`

Expected: no new failures and byte-identical legacy/plugin/A1-A5/OptionsState behavior. Record known pre-existing Gate E product blockers separately.

- [ ] **Step 4: Complete two-stage cross-review**

Assign a fresh spec-compliance reviewer and a different code-quality/economic-accounting reviewer. Every Critical/Important finding requires a failing regression test, the smallest fix, focused verification, and a separate single-purpose commit.

- [ ] **Step 5: Run the first registered diagnostic**

Run: `uv run python scripts/run_trend_participation.py --execution-manifest config/research/registrations/trend_participation_execution_v1.json --registration-envelope results/trend_research_registration/trend_participation_registration_envelope.json --as-of 2026-08-24T00:00:00Z --output-dir results/trend_participation_20260824`

Expected today: either explicit data insufficiency or a `TEST_REUSED_DIAGNOSTIC` result. It must never claim clean OOS confirmation, retrain parameters, activate risk, or publish orders.
