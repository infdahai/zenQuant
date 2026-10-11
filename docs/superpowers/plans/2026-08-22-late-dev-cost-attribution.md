# Late-Dev Cost Attribution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and run a deterministic, read-only diagnostic that explains the frozen Quant V2 late-dev 15bp failure without training, parameter search, or strategy changes.

**Architecture:** A pure research module validates the frozen report envelope and reduces only serialized A1-A5 rows and episode ledgers into per-seed bridges and evidence classifications. A thin CLI owns file hashing, canonical JSON/Markdown writes, exit codes, and a conditional redesign-hypothesis artifact. The existing frozen replay remains the sole producer of the currently missing raw artifact.

**Tech Stack:** Python 3.12, standard-library `json`/`hashlib`/`enum`, existing ZenQuant research contracts, pytest, Ruff.

**Spec:** `docs/superpowers/specs/2026-08-22-late-dev-cost-attribution-design.md`

## Global Constraints

- BTC only; candidate is frozen August rank-1.
- Use exactly the six manifest windows, seeds `42, 123, 456, 789, 1024`, and cost stress `0 / 5 / 15bp`.
- No Optuna, RL, parameter mutation, campaign, new asset, or frozen-test feedback.
- OptionsState remains observation-only and is not an attribution input.
- The diagnostic must not import or mutate strategy, risk, execution, profile, or live adapter code.
- Right-censored open episodes are reported but excluded from PnL and PF calculations.
- Cross-seed consistency requires the same conclusion in at least four of five seeds.
- Missing or invalid raw input never triggers replay implicitly.
- `.claude/settings.local.json` is user-local and stays untouched and uncommitted.

## File map

- Create `zenquant/research/late_dev_cost_attribution.py`: pure validation, aggregation, classification, canonical serialization, and hypothesis construction.
- Create `scripts/analyze_quant_v2_late_dev_cost.py`: operational CLI and atomic output writes.
- Create `tests/test_late_dev_cost_attribution.py`: synthetic frozen-matrix contract and arithmetic tests.
- Modify `tests/test_import_boundaries.py`: prohibit imports from strategy/execution/live layers.
- Create only when real evidence supports it: `docs/research/regime_objective_redesign_from_late_dev.json`.
- Generate ignored outputs under `results/quant_v2_late_dev_cost_attribution/`.

---

### Task 1: Frozen report envelope and typed result contract

**Files:**
- Create: `zenquant/research/late_dev_cost_attribution.py`
- Create: `tests/test_late_dev_cost_attribution.py`

**Interfaces:**
- Consumes: `Mapping[str, Any]` plus `source_artifact: str`, `source_sha256: str`, `source_size_bytes: int`.
- Produces: `build_late_dev_cost_attribution(report: Mapping[str, Any], *, source_artifact: str, source_sha256: str, source_size_bytes: int) -> dict[str, Any]`, `LateDevAttributionValidationError`, `EvidenceStatus`, and `canonical_late_dev_attribution_bytes(report: Mapping[str, Any]) -> bytes`.

- [ ] **Step 1: Write failing envelope and determinism tests**

Build a local `_complete_raw_report()` fixture from the checked-in manifest. It creates all 30 `(window, seed)` entries with literal A1/A5 data and does not import another test module.

```python
def test_build_report_rejects_duplicate_or_missing_frozen_matrix():
    report = _complete_raw_report()
    report["runs"].pop()
    with pytest.raises(LateDevAttributionValidationError, match="matrix"):
        _build(report)


def test_canonical_bytes_are_deterministic():
    first = _build(_complete_raw_report())
    second = _build(deepcopy(_complete_raw_report()))
    assert canonical_late_dev_attribution_bytes(first) == canonical_late_dev_attribution_bytes(second)
    assert b'"created_at"' not in canonical_late_dev_attribution_bytes(first)
```

- [ ] **Step 2: Run tests and verify RED**

```bash
uv run pytest tests/test_late_dev_cost_attribution.py -q
```

Expected: collection fails because the new research module does not exist.

- [ ] **Step 3: Implement the minimal envelope validator**

```python
LATE_DEV_ATTRIBUTION_SCHEMA_VERSION = "quant-v2-late-dev-cost-attribution.v1"


class EvidenceStatus(StrEnum):
    CONSISTENT = "CONSISTENT"
    MIXED = "MIXED"
    INSUFFICIENT_SAMPLE = "INSUFFICIENT_SAMPLE"
    UNATTRIBUTABLE = "UNATTRIBUTABLE"


class LateDevAttributionValidationError(ValueError):
    pass


def build_late_dev_cost_attribution(
    report: Mapping[str, Any],
    *,
    source_artifact: str,
    source_sha256: str,
    source_size_bytes: int,
) -> dict[str, Any]:
    if not isinstance(report, Mapping):
        raise LateDevAttributionValidationError("raw report must be a JSON object")
    manifest = load_architecture_experiment_manifest()
    runs = _validate_report_envelope(report, manifest=manifest)
    return {
        "schema_version": LATE_DEV_ATTRIBUTION_SCHEMA_VERSION,
        "source": {
            "artifact": source_artifact,
            "sha256": source_sha256,
            "size_bytes": source_size_bytes,
        },
        "manifest_sha256": manifest.sha256,
        "validated_run_count": len(runs),
        "seed_stage_bridges": [],
        "episode_attribution": [],
        "evidence": {},
        "research_only_non_promotable": True,
    }


def canonical_late_dev_attribution_bytes(report: Mapping[str, Any]) -> bytes:
    return json.dumps(report, allow_nan=False, indent=2, sort_keys=True).encode("utf-8")
```

Validate schema version, manifest/candidate/profile hashes, exact stage order,
lowercase 64-character source SHA-256, positive source size, and the exact
ordered matrix:

```python
expected = list(manifest.expected_run_keys)
observed = [(str(run.get("window")), int(run.get("seed", -1))) for run in runs]
if observed != expected or len(observed) != len(set(observed)):
    raise LateDevAttributionValidationError("raw report matrix mismatch")
```

Require exactly one OOS window named `late_dev_2025`. Do not rerun strategy logic.

- [ ] **Step 4: Verify GREEN and lint**

```bash
uv run pytest tests/test_late_dev_cost_attribution.py -q
uv run ruff check zenquant/research/late_dev_cost_attribution.py tests/test_late_dev_cost_attribution.py
uv run ruff format --check zenquant/research/late_dev_cost_attribution.py tests/test_late_dev_cost_attribution.py
```

- [ ] **Step 5: Commit**

```bash
git add zenquant/research/late_dev_cost_attribution.py tests/test_late_dev_cost_attribution.py
git commit -m "feat(research): validate late-dev attribution inputs"
```

---

### Task 2: Seed-stage bridges and accounting decompositions

**Files:**
- Modify: `zenquant/research/late_dev_cost_attribution.py`
- Modify: `tests/test_late_dev_cost_attribution.py`

**Interfaces:**
- Consumes: validated late-dev runs and stage payloads.
- Produces: `_build_seed_stage_bridge(run, stage_name)`, `_summarize_episodes(stage)`, and `_derive_execution_action(execution_row)`.

- [ ] **Step 1: Write failing arithmetic and censoring tests**

```python
def test_seed_bridge_reconciles_baseline_to_extra_15bp():
    bridge = _build(_complete_raw_report())["seed_stage_bridges"][0]
    assert bridge["baseline_net_pnl"] == 120.0
    assert bridge["additional_15bp_cost"] == 15.0
    assert bridge["extra_15bp_net_pnl"] == 105.0
    assert bridge["reconciliation_error"] == 0.0


def test_open_episode_is_visible_but_excluded_from_closed_episode_pnl():
    episodes = _build(_complete_raw_report())["episode_attribution"][0]
    assert episodes["closed_episode_count"] == 2
    assert episodes["open_episode_count"] == 1
    assert episodes["closed_net_pnl"] == 2.0
```

Mutate a 15bp total so it no longer equals baseline less `turnover * 0.0015`;
expect `LateDevAttributionValidationError("15bp bridge does not reconcile")`.

- [ ] **Step 2: Run tests and verify RED**

```bash
uv run pytest tests/test_late_dev_cost_attribution.py -q -k "bridge or open_episode"
```

- [ ] **Step 3: Implement exact accounting**

```python
def _build_seed_stage_bridge(run: Mapping[str, Any], stage_name: str) -> dict[str, Any]:
    stage = _stage(run, stage_name)
    baseline = _finite(stage["cost_stress"]["0"]["net_pnl"], "baseline net PnL")
    stressed = _finite(stage["cost_stress"]["15"]["net_pnl"], "15bp net PnL")
    turnover = _finite(stage["turnover_notional"], "turnover notional")
    additional_cost = turnover * 15.0 / 10_000.0
    error = stressed - (baseline - additional_cost)
    if not math.isclose(error, 0.0, rel_tol=0.0, abs_tol=1e-6):
        raise LateDevAttributionValidationError("15bp bridge does not reconcile")
    return {
        "seed": int(run["seed"]),
        "stage": stage_name,
        "baseline_net_pnl": baseline,
        "turnover_notional": turnover,
        "additional_15bp_cost": additional_cost,
        "extra_15bp_net_pnl": stressed,
        "reconciliation_error": error,
    }
```

Closed episodes group by explicit `side` and report count, gross PnL, fees,
funding, net PnL, MFE, MAE, duration, add/reduce/fill totals, and negative-tail
share. Open episodes contribute only count and unrealized PnL.

```python
def _derive_execution_action(row: Mapping[str, Any]) -> str:
    before = float(row["filled_position_before"])
    after = float(row["filled_position_after"])
    if before == 0.0 and after != 0.0:
        return "entry"
    if after == 0.0:
        return "exit"
    if before * after < 0.0:
        return "flip"
    if abs(after) > abs(before):
        return "add"
    if abs(after) < abs(before):
        return "reduce"
    return "unchanged"
```

Regime grouping uses explicit `regime`. Playbook grouping returns
`INSUFFICIENT_SAMPLE` when no `playbook_id` exists; never infer it.

- [ ] **Step 4: Verify GREEN**

```bash
uv run pytest tests/test_late_dev_cost_attribution.py tests/test_episode_ledger.py tests/test_gate_v3.py -q
```

- [ ] **Step 5: Commit**

```bash
git add zenquant/research/late_dev_cost_attribution.py tests/test_late_dev_cost_attribution.py
git commit -m "feat(research): decompose late-dev cost failure"
```

---

### Task 3: Four-of-five classification and terminal recommendation

**Files:**
- Modify: `zenquant/research/late_dev_cost_attribution.py`
- Modify: `tests/test_late_dev_cost_attribution.py`

**Interfaces:**
- Consumes: Task 2 bridges, direction rows, regime rows, execution rows, and episode summaries.
- Produces: `_classify_evidence(seed_evidence: Sequence[Mapping[str, Any]]) -> dict[str, Any]` and `build_regime_objective_hypothesis(attribution: Mapping[str, Any]) -> dict[str, Any] | None`.

- [ ] **Step 1: Write failing policy tests**

```python
def test_global_direction_failure_stops_donor_training():
    result = _build(_complete_raw_report(direction_hit_rate=0.47, a1_sign_15bp=-10.0))
    assert result["evidence"]["directional_edge"]["status"] == "CONSISTENT"
    assert result["terminal_recommendation"] == "stop_donor_training"
    assert build_regime_objective_hypothesis(result) is None


def test_regime_localized_failure_prepares_redesign_at_four_of_five():
    result = _build(_complete_raw_report(regime_localized_seed_count=4))
    assert result["evidence"]["regime_localization"]["supporting_seed_count"] == 4
    assert result["terminal_recommendation"] == "prepare_regime_objective_redesign"


def test_three_of_five_is_mixed_and_cannot_open_redesign():
    result = _build(_complete_raw_report(regime_localized_seed_count=3))
    assert result["evidence"]["regime_localization"]["status"] == "MIXED"
    assert result["terminal_recommendation"] == "insufficient_evidence_no_strategy_change"
```

- [ ] **Step 2: Run tests and verify RED**

```bash
uv run pytest tests/test_late_dev_cost_attribution.py -q -k "direction_failure or regime_localized or three_of_five"
```

- [ ] **Step 3: Implement frozen evidence rules**

- Direction supports a seed when A1_SIGN 15bp PnL is `<=0` and hit rate `<0.50`.
- Confidence has no incremental edge when A1_SCORE and A1_SIGN hit rates match
  within `1e-12` and A1_SCORE 15bp PnL is `<=0`.
- Cost flip supports a seed only when A5 baseline PnL is `>0` and A5 15bp PnL is `<=0`.
- Regime localization supports a seed only when one explicit regime contributes
  at least `60%` of absolute negative A5 row net PnL and all other regimes remain
  net positive after their allocated 15bp turnover cost.
- Episode tail supports a seed when the worst `10%` of at least `20` closed
  episodes explain at least `60%` of total closed-episode loss.
- Execution shortfall is insufficient below `20` observations; otherwise it
  supports when summed shortfall explains at least `25%` of absolute baseline loss.
- `CONSISTENT` is `>=4` seeds, `MIXED` is `1-3`, zero is `UNATTRIBUTABLE`, and
  unmet sample floors are `INSUFFICIENT_SAMPLE`.

Apply recommendation priority exactly:

```python
if required_inputs_incomplete:
    recommendation = "insufficient_evidence_no_strategy_change"
elif directional_edge_support >= 4:
    recommendation = "stop_donor_training"
elif regime_localization_support >= 4:
    recommendation = "prepare_regime_objective_redesign"
else:
    recommendation = "insufficient_evidence_no_strategy_change"
```

Always emit `research_only_non_promotable: True` and preserve
`BLOCKED_PAUSE_PROMOTION_AND_TRAINING`.

- [ ] **Step 4: Verify GREEN**

```bash
uv run pytest tests/test_late_dev_cost_attribution.py tests/test_quant_v2_acceptance.py -q
```

- [ ] **Step 5: Commit**

```bash
git add zenquant/research/late_dev_cost_attribution.py tests/test_late_dev_cost_attribution.py
git commit -m "feat(research): classify late-dev failure evidence"
```

---

### Task 4: CLI, Markdown, hypothesis isolation, and exit codes

**Files:**
- Create: `scripts/analyze_quant_v2_late_dev_cost.py`
- Modify: `zenquant/research/late_dev_cost_attribution.py`
- Modify: `tests/test_late_dev_cost_attribution.py`
- Modify: `tests/test_import_boundaries.py`

**Interfaces:**
- Consumes: `--input` and `--output-dir` operational paths only.
- Produces: canonical JSON, deterministic Markdown, optional hypothesis JSON, and exit codes `0/1/2`.

- [ ] **Step 1: Write failing CLI and import-boundary tests**

```python
def test_cli_writes_report_but_no_hypothesis_for_stop_donor(tmp_path):
    source = tmp_path / "architecture_attribution.json"
    source.write_text(json.dumps(_complete_raw_report(direction_hit_rate=0.47, a1_sign_15bp=-10.0)))
    completed = subprocess.run(
        [sys.executable, str(CLI), "--input", str(source), "--output-dir", str(tmp_path / "out")],
        text=True,
        capture_output=True,
    )
    assert completed.returncode == 0
    assert (tmp_path / "out" / "late_dev_cost_attribution.json").exists()
    assert not (tmp_path / "out" / "regime_objective_redesign_from_late_dev.json").exists()


def test_cli_invalid_input_writes_nothing_and_returns_two(tmp_path):
    source = tmp_path / "bad.json"
    source.write_text("{}")
    completed = _run_cli(source, tmp_path / "out")
    assert completed.returncode == 2
    assert not (tmp_path / "out").exists()
```

Add a Gate B AST test forbidding direct imports from `zenquant.strategy`,
`zenquant.execution`, `zenquant.risk`, and `zenquant.app`.

- [ ] **Step 2: Run tests and verify RED**

```bash
uv run pytest tests/test_late_dev_cost_attribution.py tests/test_import_boundaries.py -q -k "cli or late_dev"
```

- [ ] **Step 3: Implement the thin CLI**

Expose no research degree of freedom:

```python
parser.add_argument("--input", type=Path, default=Path("results/quant_v2_acceptance/architecture_attribution.json"))
parser.add_argument("--output-dir", type=Path, default=Path("results/quant_v2_late_dev_cost_attribution"))
```

Hash in 1MiB chunks. Build all bytes before creating the output directory.
Write sibling `*.tmp` files and use `Path.replace()` for atomic publication.

- Exit `2`: invalid/missing input; write nothing.
- Exit `1`: valid but `insufficient_evidence_no_strategy_change`.
- Exit `0`: complete `stop_donor_training` or `prepare_regime_objective_redesign`.

Markdown renders identities, five seed bridges, evidence statuses,
regime/action/episode summaries, limitations, and recommendation from JSON; it
does not recompute metrics. Write an optional hypothesis inside the output
directory only for `prepare_regime_objective_redesign`; it contains one failure
family, one hypothesis, old-family exclusions, one OOS falsification gate, and
no grid or training command.

- [ ] **Step 4: Verify GREEN and lint**

```bash
uv run pytest tests/test_late_dev_cost_attribution.py tests/test_import_boundaries.py -q
uv run ruff check scripts/analyze_quant_v2_late_dev_cost.py zenquant/research/late_dev_cost_attribution.py tests/test_late_dev_cost_attribution.py tests/test_import_boundaries.py
uv run ruff format --check scripts/analyze_quant_v2_late_dev_cost.py zenquant/research/late_dev_cost_attribution.py tests/test_late_dev_cost_attribution.py tests/test_import_boundaries.py
```

- [ ] **Step 5: Commit**

```bash
git add scripts/analyze_quant_v2_late_dev_cost.py zenquant/research/late_dev_cost_attribution.py tests/test_late_dev_cost_attribution.py tests/test_import_boundaries.py
git commit -m "feat(research): add late-dev attribution CLI"
```

---

### Task 5: Reproduce the raw artifact and run real attribution

**Files:**
- Generate ignored: `results/quant_v2_acceptance/architecture_attribution.json`
- Generate ignored: `results/quant_v2_acceptance/acceptance_summary.json`
- Generate ignored: `results/quant_v2_late_dev_cost_attribution/late_dev_cost_attribution.json`
- Generate ignored: `results/quant_v2_late_dev_cost_attribution/late_dev_cost_attribution.md`
- Generate conditionally: `results/quant_v2_late_dev_cost_attribution/regime_objective_redesign_from_late_dev.json`
- Modify after evidence review: `docs/QUANT_V2_ACCEPTANCE_HANDOFF.md`

**Interfaces:**
- Consumes: fixed Binance history and checked-in manifest.
- Produces: real-data attribution and a concise tracked handoff update.

- [ ] **Step 1: Run focused preflight**

```bash
uv run pytest tests/test_late_dev_cost_attribution.py tests/test_architecture_experiment.py tests/test_quant_v2_acceptance.py tests/test_episode_ledger.py tests/test_execution_router.py tests/test_parity.py -q
```

- [ ] **Step 2: Regenerate the missing frozen artifact without search**

```bash
uv run python scripts/run_architecture_experiment.py \
  --root /Users/disaster/workspace/code/zenQuant/data/raw/binance \
  --output results/quant_v2_acceptance/architecture_attribution.json
```

Expected: exactly 30 fixed runs. Do not modify the manifest or run a campaign.

- [ ] **Step 3: Rebuild the blocked acceptance summary**

```bash
uv run python scripts/build_quant_v2_acceptance_summary.py \
  --input results/quant_v2_acceptance/architecture_attribution.json \
  --output results/quant_v2_acceptance/acceptance_summary.json
```

Expected: exit `1` with `BLOCKED_PAUSE_PROMOTION_AND_TRAINING`. Exit `2` stops.

- [ ] **Step 4: Run the real attribution**

```bash
uv run python scripts/analyze_quant_v2_late_dev_cost.py \
  --input results/quant_v2_acceptance/architecture_attribution.json \
  --output-dir results/quant_v2_late_dev_cost_attribution
```

Expected: exit `0` for complete diagnosis or `1` for insufficient evidence;
exit `2` is invalid. Record source and output SHA-256 values.

- [ ] **Step 5: Review evidence before tracking any redesign record**

```bash
uv run python -m json.tool results/quant_v2_late_dev_cost_attribution/late_dev_cost_attribution.json >/dev/null
rg -n "terminal_recommendation|supporting_seed_count|BLOCKED_PAUSE" \
  results/quant_v2_late_dev_cost_attribution/late_dev_cost_attribution.json \
  results/quant_v2_late_dev_cost_attribution/late_dev_cost_attribution.md
```

Only for `prepare_regime_objective_redesign`, copy the generated hypothesis to
`docs/research/regime_objective_redesign_from_late_dev.json` after confirming it
contains no grid, tuning command, or activation. Otherwise do not create it.

- [ ] **Step 6: Update the handoff with actual evidence**

Append raw/report SHA-256 values, recommendation, evidence statuses, per-seed
bridge range, strongest supported family, unchanged blocked verdict, and whether
a redesign hypothesis was created to `docs/QUANT_V2_ACCEPTANCE_HANDOFF.md`.

- [ ] **Step 7: Run final gates**

```bash
uv run ruff check scripts/analyze_quant_v2_late_dev_cost.py zenquant/research/late_dev_cost_attribution.py tests/test_late_dev_cost_attribution.py tests/test_import_boundaries.py
uv run ruff format --check scripts/analyze_quant_v2_late_dev_cost.py zenquant/research/late_dev_cost_attribution.py tests/test_late_dev_cost_attribution.py tests/test_import_boundaries.py
uv run pytest
uv run zenquant-check
```

If the macOS sandbox blocks process semaphores, rerun the same full suite outside
the restricted sandbox and record that fact; do not reinterpret it as code failure.

- [ ] **Step 8: Commit tracked evidence only**

```bash
git add docs/QUANT_V2_ACCEPTANCE_HANDOFF.md
git add docs/research/regime_objective_redesign_from_late_dev.json 2>/dev/null || true
git commit -m "docs(research): record late-dev cost attribution"
```

Do not add ignored 1GB artifacts or generated results to Git.
