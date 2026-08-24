"""Fail-closed source freezing for registered trend research."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

from zenquant.data.spot_daily import DAY_NS, normalize_spot_daily_csvs
from zenquant.research.registration import REUSED_END_NS, REUSED_START_NS

FIVE_MINUTES_NS = 300_000_000_000
EIGHT_HOURS_NS = 28_800_000_000_000


def _load_freezer():
    path = Path(__file__).parents[1] / "scripts" / "freeze_trend_research_inputs.py"
    spec = importlib.util.spec_from_file_location("freeze_trend_research_inputs_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


module = _load_freezer()


def _write_complete_sources(tmp_path: Path) -> tuple[Path, tuple[Path, ...], Path, Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    raw = tmp_path / "BTCUSDT-1d.csv"
    spot_open = tuple(range(REUSED_START_NS - 85 * DAY_NS, REUSED_END_NS, DAY_NS))
    pd.DataFrame(
        {
            "open_time": tuple(value // 1_000_000 for value in spot_open),
            "open": tuple(100.0 + index for index in range(len(spot_open))),
            "high": tuple(110.0 + index for index in range(len(spot_open))),
            "low": tuple(90.0 + index for index in range(len(spot_open))),
            "close": tuple(100.0 + index for index in range(len(spot_open))),
            "volume": tuple(10.0 for _ in spot_open),
            "close_time": tuple((value + DAY_NS) // 1_000_000 - 1 for value in spot_open),
            "quote_volume": tuple(1000.0 for _ in spot_open),
            "trade_count": tuple(5 for _ in spot_open),
            "taker_buy_base": tuple(6.0 for _ in spot_open),
            "taker_buy_quote": tuple(600.0 for _ in spot_open),
            "ignore": tuple(0 for _ in spot_open),
        }
    ).to_csv(raw, index=False)
    spot = tmp_path / "spot.parquet"
    normalize_spot_daily_csvs((raw,), spot)

    perp = tmp_path / "perp.parquet"
    perp_open = tuple(range(REUSED_START_NS, REUSED_END_NS, FIVE_MINUTES_NS))
    pd.DataFrame(
        {
            "open_time_ns": perp_open,
            "open": tuple(100.0 for _ in perp_open),
            "high": tuple(110.0 for _ in perp_open),
            "low": tuple(90.0 for _ in perp_open),
            "close": tuple(105.0 for _ in perp_open),
            "volume": tuple(10.0 for _ in perp_open),
            "close_time_ns": tuple(value + FIVE_MINUTES_NS - 1 for value in perp_open),
        }
    ).to_parquet(perp, index=False)

    funding = tmp_path / "funding.parquet"
    funding_times = tuple(range(REUSED_START_NS, REUSED_END_NS, EIGHT_HOURS_NS))
    pd.DataFrame(
        {
            "funding_time_ns": funding_times,
            "funding_rate": tuple(0.0001 for _ in funding_times),
        }
    ).to_parquet(funding, index=False)
    return spot, (raw,), perp, funding


def _inputs(
    tmp_path: Path,
    *,
    hypothesis: str = "time_space_shadow_hypothesis_v1.json",
    funding: Path | None | object = ...,
):
    spot, raw, perp, complete_funding = _write_complete_sources(tmp_path)
    selected_funding = complete_funding if funding is ... else funding
    return module.FreezeInputs(
        hypothesis_path=Path("config/research") / hypothesis,
        spot_1d_path=spot,
        spot_raw_paths=raw,
        perp_5m_path=perp,
        funding_path=selected_funding,
        reused_start_ns=REUSED_START_NS,
        reused_end_ns=REUSED_END_NS,
        repository_root=Path.cwd(),
    )


def test_freeze_refuses_missing_funding_and_does_not_compute_signal(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(module, "compute_z84", lambda *_: pytest.fail("signal computation forbidden"), raising=False)

    with pytest.raises(module.InputFreezeError, match=r"REQUIRED.*BINANCE_PERPETUAL_BTCUSDT_FUNDING"):
        module.build_execution_manifest(_inputs(tmp_path, funding=None))

    missing = _inputs(tmp_path / "missing")
    missing.funding_path.unlink()
    with pytest.raises(module.InputFreezeError, match=r"REQUIRED.*BINANCE_PERPETUAL_BTCUSDT_FUNDING"):
        module.build_execution_manifest(missing)


def test_envelope_binds_hypothesis_sources_and_accounting_kernel(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path)

    payload = module.build_execution_manifest(inputs)

    assert payload["schema_version"] == "trend-research-execution-manifest.v1"
    assert payload["hypothesis_sha256"] == module.load_hypothesis_manifest(inputs.hypothesis_path).sha256
    assert payload["reused_window"] == [REUSED_START_NS, REUSED_END_NS]
    assert set(payload["sources"]) == {
        "BINANCE_SPOT_BTCUSDT_1D",
        "BINANCE_PERPETUAL_BTCUSDT_5M",
        "BINANCE_PERPETUAL_BTCUSDT_FUNDING",
    }
    spot = payload["sources"]["BINANCE_SPOT_BTCUSDT_1D"]
    assert set(spot) >= {
        "raw_sha256",
        "normalizer_identity",
        "normalized_sha256",
        "last_cutoff_ns",
        "coverage",
    }
    assert spot["raw_sha256"] == [
        {
            "path": str(inputs.spot_raw_paths[0]),
            "sha256": hashlib.sha256(inputs.spot_raw_paths[0].read_bytes()).hexdigest(),
        }
    ]
    assert spot["normalized_path"] == str(inputs.spot_1d_path)
    assert spot["lineage"]["status"] == "VERIFIED"
    assert spot["warmup"] == {
        "start_ns": REUSED_START_NS - 85 * DAY_NS,
        "end_ns": REUSED_START_NS,
        "required_closes": 85,
        "observed_closes": 85,
        "status": "READY",
    }
    assert spot["coverage"]["first_open_ns"] == REUSED_START_NS
    assert spot["coverage"]["last_close_ns"] == REUSED_END_NS - 1
    assert payload["sources"]["BINANCE_PERPETUAL_BTCUSDT_5M"]["path"] == str(inputs.perp_5m_path)
    assert payload["sources"]["BINANCE_PERPETUAL_BTCUSDT_FUNDING"]["path"] == str(inputs.funding_path)
    assert payload["sources"]["BINANCE_PERPETUAL_BTCUSDT_5M"]["coverage"]["missing_count"] == 0
    assert payload["sources"]["BINANCE_PERPETUAL_BTCUSDT_FUNDING"]["coverage"]["missing_count"] == 0
    assert payload["accounting_kernel"]["schema_version"] == "accounting-kernel-identity.v1"
    assert payload["formula_sha256"].startswith("sha256:")
    assert payload["config_sha256"].startswith("sha256:")
    assert payload["source_set_sha256"].startswith("sha256:")
    assert payload["construction_identity"]["sha256"].startswith("sha256:")
    construction_paths = {item["path"] for item in payload["construction_identity"]["files"]}
    assert construction_paths == {
        "scripts/freeze_trend_research_inputs.py",
        "zenquant/data/spot_daily.py",
        "zenquant/domain/accounting.py",
        "zenquant/execution/router.py",
        "zenquant/research/accounting_kernel_identity.py",
        "zenquant/research/registration.py",
        "zenquant/risk/guard.py",
    }
    assert (
        payload["workspace_state"]["head"]
        == subprocess.run(["git", "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
    )
    assert all(set(entry) >= {"status", "path", "content_sha256"} for entry in payload["workspace_state"]["entries"])
    forbidden = {"direction", "pnl", "signal"}
    assert forbidden.isdisjoint(_all_keys(payload))


def test_freeze_rejects_window_drift_and_required_source_gap(tmp_path: Path) -> None:
    drifted = _inputs(tmp_path / "drifted")
    drifted = module.FreezeInputs(
        hypothesis_path=drifted.hypothesis_path,
        spot_1d_path=drifted.spot_1d_path,
        spot_raw_paths=drifted.spot_raw_paths,
        perp_5m_path=drifted.perp_5m_path,
        funding_path=drifted.funding_path,
        reused_start_ns=REUSED_START_NS,
        reused_end_ns=REUSED_END_NS - DAY_NS,
        repository_root=drifted.repository_root,
    )
    with pytest.raises(module.InputFreezeError, match="reused window"):
        module.build_execution_manifest(drifted)

    gapped = _inputs(tmp_path / "gapped")
    frame = pd.read_parquet(gapped.perp_5m_path).drop(index=5)
    frame.to_parquet(gapped.perp_5m_path, index=False)
    with pytest.raises(module.InputFreezeError, match=r"REQUIRED.*BINANCE_PERPETUAL_BTCUSDT_5M"):
        module.build_execution_manifest(gapped)


def test_freeze_rejects_truncated_perpetual_candle_cutoff(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path)
    frame = pd.read_parquet(inputs.perp_5m_path)
    frame.loc[5, "close_time_ns"] = int(frame.loc[5, "open_time_ns"]) + 60_000_000_000
    frame.to_parquet(inputs.perp_5m_path, index=False)

    with pytest.raises(module.InputFreezeError, match=r"REQUIRED.*BINANCE_PERPETUAL_BTCUSDT_5M"):
        module.build_execution_manifest(inputs)


def test_freeze_rejects_rows_after_the_exact_frozen_end(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path)
    frame = pd.read_parquet(inputs.perp_5m_path)
    post_end = frame.iloc[[-1]].copy()
    post_end["open_time_ns"] = REUSED_END_NS
    post_end["close_time_ns"] = REUSED_END_NS + FIVE_MINUTES_NS - 1
    pd.concat((frame, post_end), ignore_index=True).to_parquet(inputs.perp_5m_path, index=False)

    with pytest.raises(module.InputFreezeError, match=r"REQUIRED.*BINANCE_PERPETUAL_BTCUSDT_5M"):
        module.build_execution_manifest(inputs)


def test_freeze_proves_raw_to_normalized_lineage_and_rejects_arbitrary_raw(tmp_path: Path) -> None:
    arbitrary = _inputs(tmp_path / "arbitrary")
    arbitrary.spot_raw_paths[0].write_text("not a Binance daily archive\n", encoding="utf-8")
    with pytest.raises(module.InputFreezeError, match=r"REQUIRED.*BINANCE_SPOT_BTCUSDT_1D"):
        module.build_execution_manifest(arbitrary)

    changed = _inputs(tmp_path / "changed")
    normalized = pd.read_parquet(changed.spot_1d_path)
    normalized.loc[100, "close"] += 1.0
    normalized.to_parquet(changed.spot_1d_path, index=False)
    with pytest.raises(module.InputFreezeError, match=r"REQUIRED.*BINANCE_SPOT_BTCUSDT_1D"):
        module.build_execution_manifest(changed)


def test_freeze_accepts_extra_raw_prefix_only_when_normalized_corpus_is_exact(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path)
    raw_frame = pd.read_csv(inputs.spot_raw_paths[0])
    pre_start = raw_frame.iloc[[0]].copy()
    warmup_start = REUSED_START_NS - 85 * DAY_NS
    pre_start["open_time"] = (warmup_start - DAY_NS) // 1_000_000
    pre_start["close_time"] = warmup_start // 1_000_000 - 1
    pd.concat((pre_start, raw_frame), ignore_index=True).to_csv(inputs.spot_raw_paths[0], index=False)
    normalize_spot_daily_csvs(
        inputs.spot_raw_paths,
        inputs.spot_1d_path,
        start_open_ns=warmup_start,
        end_open_ns=REUSED_END_NS,
    )

    payload = module.build_execution_manifest(inputs)

    coverage = payload["sources"]["BINANCE_SPOT_BTCUSDT_1D"]["coverage"]
    assert coverage["pre_start_count"] == 0
    assert coverage["post_end_count"] == 0


def test_freeze_rejects_normalized_spot_rows_before_warmup(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path)
    normalized = pd.read_parquet(inputs.spot_1d_path)
    pre_start = normalized.iloc[[0]].copy()
    warmup_start = REUSED_START_NS - 85 * DAY_NS
    pre_start["open_time_ns"] = warmup_start - DAY_NS
    pre_start["close_time_ns"] = warmup_start - 1
    pd.concat((pre_start, normalized), ignore_index=True).to_parquet(inputs.spot_1d_path, index=False)

    audit = module._audit_spot(inputs)

    assert audit["status"] == "INSUFFICIENT_DATA_SPOT_CONTRACT"
    assert audit["coverage"]["pre_start_count"] == 1


@pytest.mark.parametrize("mutation", ["missing", "gap", "zero", "nan"])
def test_freeze_requires_exact_85_close_spot_warmup(tmp_path: Path, mutation: str) -> None:
    inputs = _inputs(tmp_path)
    normalized = pd.read_parquet(inputs.spot_1d_path)
    warmup_start = REUSED_START_NS - 85 * DAY_NS
    if mutation == "missing":
        normalized = normalized[normalized["open_time_ns"] != warmup_start]
    elif mutation == "gap":
        normalized = normalized[normalized["open_time_ns"] != warmup_start + 40 * DAY_NS]
    elif mutation == "zero":
        normalized.loc[normalized["open_time_ns"] == warmup_start, "close"] = 0.0
    else:
        normalized.loc[normalized["open_time_ns"] == warmup_start, "close"] = float("nan")
    normalized.to_parquet(inputs.spot_1d_path, index=False)

    with pytest.raises(module.InputFreezeError, match=r"REQUIRED.*BINANCE_SPOT_BTCUSDT_1D"):
        module.build_execution_manifest(inputs)


@pytest.mark.parametrize("mutation", ["timestamp_only", "bad_ohlc", "negative_volume"])
def test_freeze_requires_complete_valid_perpetual_schema(tmp_path: Path, mutation: str) -> None:
    inputs = _inputs(tmp_path)
    frame = pd.read_parquet(inputs.perp_5m_path)
    if mutation == "timestamp_only":
        frame = frame[["open_time_ns", "close_time_ns"]]
    elif mutation == "bad_ohlc":
        frame.loc[5, "high"] = 99.0
    else:
        frame.loc[5, "volume"] = -1.0
    frame.to_parquet(inputs.perp_5m_path, index=False)

    with pytest.raises(module.InputFreezeError, match=r"REQUIRED.*BINANCE_PERPETUAL_BTCUSDT_5M"):
        module.build_execution_manifest(inputs)


@pytest.mark.parametrize("mutation", ["timestamp_only", "nan_rate"])
def test_freeze_requires_finite_funding_rate(tmp_path: Path, mutation: str) -> None:
    inputs = _inputs(tmp_path)
    frame = pd.read_parquet(inputs.funding_path)
    if mutation == "timestamp_only":
        frame = frame[["funding_time_ns"]]
    else:
        frame.loc[5, "funding_rate"] = float("nan")
    frame.to_parquet(inputs.funding_path, index=False)

    with pytest.raises(module.InputFreezeError, match=r"REQUIRED.*BINANCE_PERPETUAL_BTCUSDT_FUNDING"):
        module.build_execution_manifest(inputs)


def test_freeze_enforces_exact_hypothesis_filename_to_layer_mapping(tmp_path: Path) -> None:
    repository_root = tmp_path / "repository"
    config_dir = repository_root / "config/research"
    config_dir.mkdir(parents=True)
    wrong = config_dir / "time_space_shadow_hypothesis_v1.json"
    wrong.write_bytes(Path("config/research/trend_participation_hypothesis_v1.json").read_bytes())
    inputs = module.FreezeInputs(
        hypothesis_path=wrong,
        spot_1d_path=tmp_path / "unused-spot",
        spot_raw_paths=(tmp_path / "unused-raw",),
        perp_5m_path=tmp_path / "unused-perp",
        funding_path=tmp_path / "unused-funding",
        reused_start_ns=REUSED_START_NS,
        reused_end_ns=REUSED_END_NS,
        repository_root=repository_root,
    )

    with pytest.raises(module.InputFreezeError, match=r"filename.*layer"):
        module._load_checked_in_hypothesis(inputs)


def test_freeze_rehashes_every_input_and_rejects_mutation(tmp_path: Path, monkeypatch) -> None:
    inputs = _inputs(tmp_path)
    original = module._sha256_file
    calls: dict[Path, int] = {}

    def changing_digest(path: Path) -> str:
        resolved = Path(path).resolve()
        calls[resolved] = calls.get(resolved, 0) + 1
        digest = original(Path(path))
        if resolved == inputs.funding_path.resolve() and calls[resolved] > 1:
            return "f" * 64
        return digest

    monkeypatch.setattr(module, "_sha256_file", changing_digest)

    with pytest.raises(module.InputFreezeError, match="input mutated during freeze"):
        module.build_execution_manifest(inputs)

    assert all(calls[path.resolve()] >= 2 for path in inputs.all_input_paths())


def test_freeze_rejects_construction_code_mutation(tmp_path: Path, monkeypatch) -> None:
    inputs = _inputs(tmp_path)
    freezer_path = (inputs.repository_root / "scripts/freeze_trend_research_inputs.py").resolve()
    original = module._sha256_file
    calls = 0

    def changing_digest(path: Path) -> str:
        nonlocal calls
        digest = original(Path(path))
        if Path(path).resolve() == freezer_path:
            calls += 1
            if calls > 2:
                return "f" * 64
        return digest

    monkeypatch.setattr(module, "_sha256_file", changing_digest)

    with pytest.raises(module.InputFreezeError, match="input mutated during freeze"):
        module.build_execution_manifest(inputs)


def test_workspace_identity_binds_head_and_dirty_untracked_content(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repository, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repository, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repository, check=True)
    tracked = repository / "tracked.txt"
    tracked.write_text("before\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=repository, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=repository, check=True)
    tracked.write_text("after\n", encoding="utf-8")
    untracked = repository / "untracked.txt"
    untracked.write_text("new\n", encoding="utf-8")

    identity = module._workspace_state(repository)
    entries = {entry["path"]: entry for entry in identity["entries"]}

    assert (
        identity["head"]
        == subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repository, check=True, capture_output=True, text=True
        ).stdout.strip()
    )
    assert entries["tracked.txt"]["content_sha256"] == hashlib.sha256(tracked.read_bytes()).hexdigest()
    assert entries["untracked.txt"]["content_sha256"] == hashlib.sha256(untracked.read_bytes()).hexdigest()


def test_cli_rejects_dynamic_end_and_never_emits_partial_output(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path)
    output_dir = tmp_path / "registrations"
    command = [
        sys.executable,
        "scripts/freeze_trend_research_inputs.py",
        "--spot-1d",
        str(inputs.spot_1d_path),
        "--spot-raw",
        str(inputs.spot_raw_paths[0]),
        "--perp-5m",
        str(inputs.perp_5m_path),
        "--funding",
        str(inputs.funding_path),
        "--time-space-hypothesis",
        "config/research/time_space_shadow_hypothesis_v1.json",
        "--trend-hypothesis",
        "config/research/trend_participation_hypothesis_v1.json",
        "--reused-start",
        "2026-01-01T00:00:00Z",
        "--reused-end",
        "latest",
        "--output-dir",
        str(output_dir),
    ]

    completed = subprocess.run(command, check=False, capture_output=True, text=True)

    assert completed.returncode != 0
    assert "fixed reused end" in completed.stderr
    assert not output_dir.exists()


def test_cli_lineage_failure_never_emits_partial_output(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path)
    inputs.spot_raw_paths[0].write_text("not a valid daily archive\n", encoding="utf-8")
    output_dir = tmp_path / "registrations"
    command = [
        sys.executable,
        "scripts/freeze_trend_research_inputs.py",
        "--spot-1d",
        str(inputs.spot_1d_path),
        "--spot-raw",
        str(inputs.spot_raw_paths[0]),
        "--perp-5m",
        str(inputs.perp_5m_path),
        "--funding",
        str(inputs.funding_path),
        "--time-space-hypothesis",
        "config/research/time_space_shadow_hypothesis_v1.json",
        "--trend-hypothesis",
        "config/research/trend_participation_hypothesis_v1.json",
        "--reused-start",
        "2026-01-01T00:00:00Z",
        "--reused-end",
        "2026-08-24T00:00:00Z",
        "--output-dir",
        str(output_dir),
    ]

    completed = subprocess.run(command, check=False, capture_output=True, text=True)

    assert completed.returncode != 0
    assert "BINANCE_SPOT_BTCUSDT_1D" in completed.stderr
    assert not output_dir.exists()


def test_cli_writes_two_canonical_manifests_as_one_atomic_directory(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path)
    output_dir = tmp_path / "registrations"
    command = [
        sys.executable,
        "scripts/freeze_trend_research_inputs.py",
        "--spot-1d",
        str(inputs.spot_1d_path),
        "--spot-raw",
        str(inputs.spot_raw_paths[0]),
        "--perp-5m",
        str(inputs.perp_5m_path),
        "--funding",
        str(inputs.funding_path),
        "--time-space-hypothesis",
        "config/research/time_space_shadow_hypothesis_v1.json",
        "--trend-hypothesis",
        "config/research/trend_participation_hypothesis_v1.json",
        "--reused-start",
        "2026-01-01T00:00:00Z",
        "--reused-end",
        "2026-08-24T00:00:00Z",
        "--output-dir",
        str(output_dir),
    ]

    completed = subprocess.run(command, check=False, capture_output=True, text=True)

    assert completed.returncode == 0, completed.stderr
    outputs = sorted(output_dir.glob("*.json"))
    assert [path.name for path in outputs] == [
        "time_space_shadow_execution_v1.json",
        "trend_participation_execution_v1.json",
    ]
    for path in outputs:
        raw = path.read_text(encoding="utf-8")
        payload = json.loads(raw)
        assert (
            raw
            == json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True) + "\n"
        )


def _all_keys(value: object) -> set[str]:
    if isinstance(value, dict):
        return set(value).union(*(tuple(_all_keys(item) for item in value.values())))
    if isinstance(value, list):
        return set().union(*(tuple(_all_keys(item) for item in value)))
    return set()
