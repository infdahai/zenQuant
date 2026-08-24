# Trend Research Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the fail-closed BTC spot-daily data, source-identity, evidence-role, and trusted-registration foundation required by both trend research layers.

**Architecture:** Add immutable research contracts under `zenquant.research` and a dedicated Binance spot-daily normalizer/auditor under `zenquant.data`. Keep downloaded data and generated receipts outside Git; checked-in hypothesis manifests freeze formulas and requested windows, while execution envelopes bind the final corpus before any result is computed.

**Tech Stack:** Python 3.12, frozen dataclasses, pandas/pyarrow, hashlib/json, pytest, existing `uv` and `zenquant-check` tooling.

**Spec:** `docs/superpowers/specs/2026-08-24-trend-participation-two-layer-design.md`

## Global Constraints

- Asset and active signal source are exactly BTC and `BINANCE_SPOT_BTCUSDT_1D`.
- Requested reused window is `[2026-01-01T00:00:00Z, 2026-08-24T00:00:00Z)` and may not be shortened after inspection.
- Spot fallback is `NONE`; REQUIRED spot, perpetual 5m, and funding inputs fail closed.
- Evidence role is determined by market-event cutoff, never ingest/backfill time.
- Forward start is the first confirmed 4h cutoff at or after the later trusted receipt plus 24 hours.
- No production strategy, risk, execution, `DecisionSnapshot`, or A1-A5 schema change is allowed.
- Each task is implemented by a fresh subagent, then reviewed first for spec compliance and second for code quality before commit.

## File Map

- Create `zenquant/research/registration.py`: canonical manifest hashing, receipt validation, forward-cutoff derivation, and evidence-role classification.
- Create `zenquant/data/spot_daily.py`: deterministic CSV normalization, daily-grid audit, and immutable source-envelope construction.
- Create `scripts/freeze_trend_research_inputs.py`: CLI that audits all REQUIRED inputs and writes an execution manifest without computing signals.
- Create `scripts/build_trend_registration_receipt.py` and `.github/workflows/trend-research-registration.yml`: bind manifest and commit identities to a verifiable GitHub Actions run creation time.
- Modify `scripts/convert_csv_to_parquet.py`: delegate Binance spot 1d normalization to the strict library path.
- Modify `scripts/data_readiness_status.py`: expose trend-research readiness and blockers.
- Create `config/research/time_space_shadow_hypothesis_v1.json` and `config/research/trend_participation_hypothesis_v1.json`: checked-in frozen hypotheses.
- Create `tests/test_trend_research_registration.py`, `tests/test_spot_daily.py`, and `tests/test_trend_research_input_freeze.py`: focused contracts.

---

### Task 1: Immutable Manifest and Evidence Contracts

**Files:**
- Create: `zenquant/research/registration.py`
- Test: `tests/test_trend_research_registration.py`

**Interfaces:**
- Produces: `canonical_sha256(payload: Mapping[str, Any]) -> str`
- Produces: `load_hypothesis_manifest(path: Path) -> HypothesisManifest`
- Produces: `derive_forward_start_ns(receipts: Sequence[RegistrationReceipt]) -> int`
- Produces: `classify_evidence_role(event_cutoff_ns: int, reused_start_ns: int, reused_end_ns: int, forward_start_ns: int) -> EvidenceRole`

- [ ] **Step 1: Write failing immutable-contract tests**

```python
def test_forward_start_uses_later_receipt_plus_24h_and_ceil_4h() -> None:
    receipts = (_receipt(100), _receipt(200))
    assert derive_forward_start_ns(receipts) == ceil_4h_ns(200 + DAY_NS)

def test_backfilled_old_event_remains_reused() -> None:
    assert classify_evidence_role(99, reused_start_ns=50, reused_end_ns=100, forward_start_ns=120) is EvidenceRole.TEST_REUSED_DIAGNOSTIC

def test_pre_reused_window_is_discovery_only() -> None:
    assert classify_evidence_role(49, reused_start_ns=50, reused_end_ns=100, forward_start_ns=120) is EvidenceRole.DISCOVERY_ONLY
```

- [ ] **Step 2: Run the tests and confirm RED**

Run: `uv run pytest tests/test_trend_research_registration.py -q`

Expected: collection fails because `zenquant.research.registration` does not exist.

- [ ] **Step 3: Implement the frozen contracts**

```python
class EvidenceRole(StrEnum):
    DISCOVERY_ONLY = "DISCOVERY_ONLY"
    TEST_REUSED_DIAGNOSTIC = "TEST_REUSED_DIAGNOSTIC"
    FORWARD_CONFIRMATION = "FORWARD_CONFIRMATION"

@dataclass(frozen=True, slots=True)
class RegistrationReceipt:
    manifest_sha256: str
    commit_sha: str
    remote_identity: str
    received_at_ns: int

def derive_forward_start_ns(receipts: Sequence[RegistrationReceipt]) -> int:
    if len(receipts) != 2:
        raise RegistrationError("two trusted receipts are required")
    candidate = max(item.received_at_ns for item in receipts) + DAY_NS
    return ((candidate + FOUR_HOURS_NS - 1) // FOUR_HOURS_NS) * FOUR_HOURS_NS
```

The loader must reject unknown keys, non-canonical SHA strings, a missing exact reused window, fallback other than `NONE`, changed seeds, and a receipt whose manifest hash/remote identity does not match.

- [ ] **Step 4: Run focused tests and confirm GREEN**

Run: `uv run pytest tests/test_trend_research_registration.py -q`

Expected: all tests pass.

- [ ] **Step 5: Commit the contract**

```bash
git add zenquant/research/registration.py tests/test_trend_research_registration.py
git commit -m "feat(research): add trusted trend registration contracts"
```

### Task 2: Frozen Hypothesis Manifests

**Files:**
- Create: `config/research/time_space_shadow_hypothesis_v1.json`
- Create: `config/research/trend_participation_hypothesis_v1.json`
- Modify: `tests/test_trend_research_registration.py`

**Interfaces:**
- Consumes: `load_hypothesis_manifest()` from Task 1.
- Produces: two immutable manifest identities used by later execution envelopes.

- [ ] **Step 1: Add failing checked-in manifest tests**

```python
@pytest.mark.parametrize("name", ["time_space_shadow", "trend_participation"])
def test_checked_in_hypothesis_freezes_window_sources_and_search_policy(name: str) -> None:
    manifest = load_hypothesis_manifest(Path(f"config/research/{name}_hypothesis_v1.json"))
    assert manifest.reused_start_ns == 1767225600000000000
    assert manifest.reused_end_ns == 1787529600000000000
    assert manifest.training is False
    assert manifest.parameter_search is False
    assert manifest.seeds == (42, 123, 456, 789, 1024)
```

- [ ] **Step 2: Run the tests and confirm missing-file failures**

Run: `uv run pytest tests/test_trend_research_registration.py -q`

Expected: both parameterized cases fail with `FileNotFoundError`.

- [ ] **Step 3: Add exact JSON manifests**

The TimeSpace JSON freezes `hold_minus_flip_6`, 15bp minimum effect, month-block bootstrap, 180 days, 30 flips, and no authority. The donor JSON freezes `z84`, thresholds `-0.50/0/+0.50`, `ddof=1`, `sigma_floor=1e-12`, costs `0/5/15`, noise `0.001`, dropout `0.02`, shifts `[-84,-56,-28,28,56,84]`, 348 elapsed days, the 180-day common-intersection floor, 30 episodes, and three regime buckets. Both list explicit source requirements and fallback `NONE`.

- [ ] **Step 4: Run focused tests and confirm GREEN**

Run: `uv run pytest tests/test_trend_research_registration.py -q`

Expected: all tests pass and manifest digests are stable across repeated loads.

- [ ] **Step 5: Commit the hypotheses**

```bash
git add config/research/time_space_shadow_hypothesis_v1.json config/research/trend_participation_hypothesis_v1.json tests/test_trend_research_registration.py
git commit -m "feat(research): freeze trend research hypotheses"
```

### Task 3: Trusted Remote Registration Receipt

**Files:**
- Create: `scripts/build_trend_registration_receipt.py`
- Create: `.github/workflows/trend-research-registration.yml`
- Modify: `zenquant/research/registration.py`
- Modify: `tests/test_trend_research_registration.py`

**Interfaces:**
- Produces: `build_registration_envelope(manifest_path, commit_sha, provider_run) -> dict[str, Any]`.
- Produces: `verify_github_receipt(envelope, provider_payload) -> RegistrationReceipt`.

- [ ] **Step 1: Write failing provider-identity tests**

```python
def test_receipt_uses_provider_created_at_not_local_git_time() -> None:
    envelope = build_registration_envelope(_manifest(), "abc123", _github_run(created_at="2026-08-24T08:00:00Z"))
    assert envelope["received_at_ns"] == 1787558400000000000

def test_verifier_rejects_changed_commit_or_manifest() -> None:
    with pytest.raises(RegistrationError, match="provider identity mismatch"):
        verify_github_receipt(_envelope(commit="a"), _github_run(head_sha="b"))
```

- [ ] **Step 2: Run focused tests and confirm RED**

Run: `uv run pytest tests/test_trend_research_registration.py -q`

Expected: receipt builder/verifier is missing.

- [ ] **Step 3: Implement verifiable GitHub Actions receipt generation**

```python
def verify_github_receipt(envelope: Mapping[str, Any], provider: Mapping[str, Any]) -> RegistrationReceipt:
    expected = (envelope["provider_run_id"], envelope["commit_sha"], envelope["remote_identity"])
    observed = (provider["id"], provider["head_sha"], provider["repository"]["full_name"])
    if expected != observed:
        raise RegistrationError("provider identity mismatch")
    received_at_ns = iso8601_to_ns(str(provider["created_at"]))
    return RegistrationReceipt(str(envelope["manifest_sha256"]), str(provider["head_sha"]), str(envelope["remote_identity"]), received_at_ns)
```

The workflow retains `workflow_dispatch` for post-merge use and accepts one
`manifest_path` plus `manifest_kind`. Before merge, it bootstraps only from the
four fixed registration branches, each mapped to exactly one manifest. It
checks out the selected immutable commit, fetches its own run payload with
`gh api repos/$GITHUB_REPOSITORY/actions/runs/$GITHUB_RUN_ID`, writes one JSON
envelope, and uploads it as a GitHub artifact. It must fail when a manifest is
untracked, differs from the checked-out commit, the bootstrap branch is
unmapped, or the run head SHA differs.

- [ ] **Step 4: Run tests and workflow syntax check**

Run: `uv run pytest tests/test_trend_research_registration.py -q`

Run: `uv run python -c "import yaml; yaml.safe_load(open('.github/workflows/trend-research-registration.yml'))"`

Expected: tests pass and YAML parses.

- [ ] **Step 5: Commit trusted registration**

```bash
git add scripts/build_trend_registration_receipt.py .github/workflows/trend-research-registration.yml zenquant/research/registration.py tests/test_trend_research_registration.py
git commit -m "ci(research): add trusted trend registration receipt"
```

- [ ] **Step 6: Push and obtain the two hypothesis receipts before Phase 0**

```bash
git push personal HEAD:feature/trend-registration-time-space-hypothesis
git push personal HEAD:feature/trend-registration-trend-participation-hypothesis
```

Wait for both runs, download each `registration_receipt.json`, and verify it
against the live GitHub run API. Do not begin the real Phase 0 repair until both
receipts validate.

### Task 4: Deterministic Binance Spot-Daily Normalization

**Files:**
- Create: `zenquant/data/spot_daily.py`
- Modify: `scripts/convert_csv_to_parquet.py`
- Test: `tests/test_spot_daily.py`
- Modify: `tests/test_convert_csv_to_parquet.py`

**Interfaces:**
- Produces: `normalize_spot_daily_csvs(paths: Sequence[Path], output_path: Path) -> SpotDailyNormalizationResult`
- Produces: `audit_spot_daily(path: Path, start_ns: int, end_ns: int) -> SpotDailyAudit`

- [ ] **Step 1: Write failing duplicate, gap, and timestamp tests**

```python
def test_exact_archive_overlap_deduplicates_but_conflict_fails(tmp_path: Path) -> None:
    exact = normalize_spot_daily_csvs((_csv(tmp_path, "a", close=100), _csv(tmp_path, "b", close=100)), tmp_path / "out.parquet")
    assert exact.row_count == 1
    with pytest.raises(SpotDailyError, match="conflicting duplicate"):
        normalize_spot_daily_csvs((_csv(tmp_path, "a", close=100), _csv(tmp_path, "c", close=101)), tmp_path / "bad.parquet")

def test_audit_rejects_missing_utc_day() -> None:
    assert audit_spot_daily(_daily_parquet(days=(1, 3)), START_NS, END_NS).status == "INSUFFICIENT_DATA_SPOT_DAILY_GAPS"
```

- [ ] **Step 2: Run focused tests and confirm RED**

Run: `uv run pytest tests/test_spot_daily.py tests/test_convert_csv_to_parquet.py -q`

Expected: new module import fails.

- [ ] **Step 3: Implement strict normalization and audit**

```python
@dataclass(frozen=True, slots=True)
class SpotDailyAudit:
    status: str
    row_count: int
    first_open_ns: int | None
    last_close_ns: int | None
    missing_open_ns: tuple[int, ...]
    duplicate_open_ns: tuple[int, ...]
    sha256: str

def audit_spot_daily(path: Path, start_ns: int, end_ns: int) -> SpotDailyAudit:
    frame = pd.read_parquet(path).sort_values("open_time_ns")
    expected = set(range(start_ns, end_ns, DAY_NS))
    observed = set(int(value) for value in frame.open_time_ns)
    missing = tuple(sorted(expected - observed))
    status = "READY" if not missing and frame.open_time_ns.is_unique else "INSUFFICIENT_DATA_SPOT_DAILY_GAPS"
    return SpotDailyAudit(status, len(frame), _first(frame), _last(frame), missing, _duplicates(frame), sha256(path.read_bytes()).hexdigest())
```

Normalization must accept microsecond/millisecond Binance archives, require positive finite OHLC, require `high >= max(open, close)` and `low <= min(open, close)`, preserve raw source digests, accept only byte/value-identical overlap, and write ordered Parquet atomically. Change the existing converter only for `source=binance_spot` and `tf=1d`; futures and other timeframes retain current behavior.

- [ ] **Step 4: Run focused tests and confirm GREEN**

Run: `uv run pytest tests/test_spot_daily.py tests/test_convert_csv_to_parquet.py -q`

Expected: all tests pass.

- [ ] **Step 5: Commit normalization**

```bash
git add zenquant/data/spot_daily.py scripts/convert_csv_to_parquet.py tests/test_spot_daily.py tests/test_convert_csv_to_parquet.py
git commit -m "feat(data): add strict BTC spot daily normalization"
```

### Task 5: Source Envelope and Fail-Closed Input Freeze

**Files:**
- Create: `scripts/freeze_trend_research_inputs.py`
- Test: `tests/test_trend_research_input_freeze.py`

**Interfaces:**
- Consumes: manifest loader and `audit_spot_daily()`.
- Produces: `build_execution_manifest(...) -> dict[str, Any]` and a CLI-written immutable JSON envelope.

- [ ] **Step 1: Write failing source-envelope tests**

```python
def test_freeze_refuses_missing_funding_and_does_not_compute_signal(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(module, "compute_z84", lambda *_: pytest.fail("signal computation forbidden"), raising=False)
    with pytest.raises(InputFreezeError, match="REQUIRED.*funding"):
        build_execution_manifest(_inputs(tmp_path, funding=None))

def test_envelope_binds_raw_normalizer_and_output_digests(tmp_path: Path) -> None:
    payload = build_execution_manifest(_complete_inputs(tmp_path))
    spot = payload["sources"]["BINANCE_SPOT_BTCUSDT_1D"]
    assert set(spot) >= {"raw_sha256", "normalizer_identity", "normalized_sha256", "last_cutoff_ns"}
```

- [ ] **Step 2: Run focused tests and confirm RED**

Run: `uv run pytest tests/test_trend_research_input_freeze.py -q`

Expected: script module is missing.

- [ ] **Step 3: Implement the blind freezer**

```python
def build_execution_manifest(inputs: FreezeInputs) -> dict[str, Any]:
    audits = {source.source_id: source.audit() for source in inputs.sources}
    blockers = sorted(source_id for source_id, audit in audits.items() if audit.required and not audit.ready)
    if blockers:
        raise InputFreezeError(f"REQUIRED sources not ready: {blockers}")
    return {
        "schema_version": "trend-research-execution-manifest.v1",
        "hypothesis_sha256": inputs.hypothesis_sha256,
        "reused_window": [inputs.reused_start_ns, inputs.reused_end_ns],
        "sources": {key: value.to_dict() for key, value in sorted(audits.items())},
        "accounting_kernel": build_accounting_kernel_identity(),
    }
```

The CLI accepts explicit spot/perpetual/funding paths and output directory, refuses dynamic end dates, writes via temporary file then rename, re-hashes every input after construction, and emits no direction/PnL field.

- [ ] **Step 4: Run focused tests and confirm GREEN**

Run: `uv run pytest tests/test_trend_research_input_freeze.py -q`

Expected: all tests pass.

- [ ] **Step 5: Commit the freezer**

```bash
git add scripts/freeze_trend_research_inputs.py tests/test_trend_research_input_freeze.py
git commit -m "feat(research): freeze trend execution inputs"
```

### Task 6: Readiness Surface and Real Data Repair Runbook

**Files:**
- Modify: `scripts/data_readiness_status.py`
- Modify: `zenquant/data/readiness.py`
- Modify: `tests/test_data_readiness_status.py`
- Modify: `docs/FORWARD_COLLECTION_AUTOMATION.md`

**Interfaces:**
- Produces: `trend_research_status` with exact status, gaps, last cutoff, and next action.

- [ ] **Step 1: Add failing readiness output test**

```python
def test_readiness_reports_spot_daily_gap_without_neutral_fallback(tmp_path: Path) -> None:
    payload = build_readiness_payload(**_roots(tmp_path), trend_spot_path=_gapped_spot(tmp_path))
    status = payload["trend_research_status"]
    assert status["status"] == "INSUFFICIENT_DATA_SPOT_DAILY_GAPS"
    assert status["fallback"] == "NONE"
```

- [ ] **Step 2: Run the test and confirm RED**

Run: `uv run pytest tests/test_data_readiness_status.py -q`

Expected: `trend_spot_path` is not accepted.

- [ ] **Step 3: Add the readiness section and exact runbook commands**

```bash
uv run python scripts/download_spot_data.py --symbol BTCUSDT --start-date 2025-10-08 --end-date 2026-08-24 --timeframes 1d
uv run python scripts/convert_csv_to_parquet.py --source binance_spot --symbol BTCUSDT
uv run python scripts/data_readiness_status.py --trend-spot-path data/raw/binance_spot/klines/BTCUSDT/1d/data.parquet
```

Document that the first date includes the 85-close warmup, downloads require network access, the readiness command must report `READY`, and generated data is frozen before donor computation.

- [ ] **Step 4: Run focused and documentation tests**

Run: `uv run pytest tests/test_data_readiness_status.py tests/test_agent_docs_contract.py -q`

Expected: all tests pass.

- [ ] **Step 5: Commit readiness integration**

```bash
git add scripts/data_readiness_status.py zenquant/data/readiness.py tests/test_data_readiness_status.py docs/FORWARD_COLLECTION_AUTOMATION.md
git commit -m "feat(data): expose trend research readiness"
```

### Task 7: Foundation Verification Checkpoint

**Files:**
- Modify only if a verification failure requires an in-scope fix.

**Interfaces:**
- Produces: reviewed, revertible foundation ready for both layer plans.

- [ ] **Step 1: Run focused foundation tests**

Run: `uv run pytest tests/test_trend_research_registration.py tests/test_spot_daily.py tests/test_convert_csv_to_parquet.py tests/test_trend_research_input_freeze.py tests/test_data_readiness_status.py -q`

Expected: all tests pass.

- [ ] **Step 2: Run architecture boundaries and full gate**

Run: `uv run pytest tests/test_import_boundaries.py tests/test_parity.py -q`

Run: `uv run zenquant-check`

Expected: no new failures; any pre-existing Gate E product blocker is recorded separately and must not be relabeled as caused by this plan.

- [ ] **Step 3: Obtain two-stage cross-review**

Dispatch one fresh reviewer for exact spec compliance and a second fresh reviewer for code quality, determinism, and data-loss risk. Resolve all Critical/Important findings with focused tests and separate fix commits.

- [ ] **Step 4: Record the immutable execution command**

Run: `uv run python scripts/freeze_trend_research_inputs.py --spot-1d data/raw/binance_spot/klines/BTCUSDT/1d/data.parquet --perp-5m data/raw/binance/klines/BTCUSDT/5m/data.parquet --funding data/raw/binance/funding/BTCUSDT/data.parquet --time-space-hypothesis config/research/time_space_shadow_hypothesis_v1.json --trend-hypothesis config/research/trend_participation_hypothesis_v1.json --output-dir config/research/registrations`

Expected: either two execution manifests with all source digests or an explicit
`INSUFFICIENT_DATA` blocker; never a partial research result.

- [ ] **Step 5: Commit and remotely register the frozen execution manifests**

If Step 4 succeeds:

```bash
git add config/research/registrations/time_space_shadow_execution_v1.json config/research/registrations/trend_participation_execution_v1.json
git commit -m "chore(research): register frozen trend input corpus"
git push personal HEAD:feature/trend-registration-time-space-execution
git push personal HEAD:feature/trend-registration-trend-participation-execution
```

Download and verify both execution receipts. For each layer, derive
`forward_start_ns` from the later of its hypothesis/execution receipt times plus
24 hours. If Step 4 is insufficient, stop here without creating receipts or
starting either layer.
