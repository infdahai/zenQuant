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

from zenquant.data.spot_daily import DAY_NS
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
    spot = tmp_path / "spot.parquet"
    spot_open = tuple(range(REUSED_START_NS, REUSED_END_NS, DAY_NS))
    pd.DataFrame(
        {
            "open_time_ns": spot_open,
            "close_time_ns": tuple(value + DAY_NS - 1 for value in spot_open),
            "close": tuple(100.0 + index for index in range(len(spot_open))),
        }
    ).to_parquet(spot, index=False)
    raw = tmp_path / "spot-raw.csv"
    raw.write_bytes(b"immutable raw spot archive\n")

    perp = tmp_path / "perp.parquet"
    perp_open = tuple(range(REUSED_START_NS, REUSED_END_NS, FIVE_MINUTES_NS))
    pd.DataFrame(
        {
            "open_time_ns": perp_open,
            "close_time_ns": tuple(value + FIVE_MINUTES_NS - 1 for value in perp_open),
            "close": tuple(100.0 for _ in perp_open),
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
    assert payload["sources"]["BINANCE_PERPETUAL_BTCUSDT_5M"]["path"] == str(inputs.perp_5m_path)
    assert payload["sources"]["BINANCE_PERPETUAL_BTCUSDT_FUNDING"]["path"] == str(inputs.funding_path)
    assert payload["sources"]["BINANCE_PERPETUAL_BTCUSDT_5M"]["coverage"]["missing_count"] == 0
    assert payload["sources"]["BINANCE_PERPETUAL_BTCUSDT_FUNDING"]["coverage"]["missing_count"] == 0
    assert payload["accounting_kernel"]["schema_version"] == "accounting-kernel-identity.v1"
    assert payload["formula_sha256"].startswith("sha256:")
    assert payload["config_sha256"].startswith("sha256:")
    assert payload["source_set_sha256"].startswith("sha256:")
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
