"""Synthetic coverage of COT availability checks across research consumers."""

from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from zenquant.data.cot_store import cot_data_available
from zenquant.historical import HistoricalBacktestConfig
from zenquant.research.data_coverage_audit import DataSourceSpec, audit_source, build_data_coverage_audit
from zenquant.research.framework_search import FrameworkEvaluator
from zenquant.research.signal_combination import _signal_data_dependency
from zenquant.research.splits import default_btc_split_spec
from zenquant.research.swarm.agents import DataAdmissionAgent
from zenquant.research.swarm.contracts import SourceCandidate


def _write(path: Path, *, raw: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    timestamp = "position_as_of_date_ns" if raw else "report_date_ns"
    pq.write_table(pa.table({timestamp: [1_600_000_000_000_000_000], "asset_mgr_net": [42]}), path)


def _candidate(path: str) -> SourceCandidate:
    return SourceCandidate(
        name="custom",
        provider="free",
        source_type="positioning",
        role="state",
        symbol_scope=("BTC",),
        time_granularity="weekly",
        free_tier=True,
        pit_safe=True,
        local_cache_path=path,
        regime_assumption="test",
        behavioral_hypothesis="test",
        failure_cases=(),
        correlation_group="positioning",
        prior_rank_ic=0.0,
    )


@pytest.mark.parametrize("raw", [False, True])
def test_cot_consumers_use_compatible_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, raw: bool) -> None:
    monkeypatch.chdir(tmp_path)
    path = Path("data/raw/cftc/cot_btc.parquet")
    _write(path, raw=raw)
    assert cot_data_available(path) is (not raw)
    dependency = _signal_data_dependency(
        "cme_positioning_state_signal", macro_root=Path("macro"), data_root=Path("data/raw")
    )
    assert dependency[1] == ("missing" if raw else "ready")
    audit = build_data_coverage_audit(data_root=Path("data/raw"))
    source = next(item for item in audit["sources"] if item["source_id"] == "cme_cot_btc")
    assert source["training_integration"]["integration_status"] == (
        "missing" if raw else "research_or_context_available"
    )
    assert (
        source["combined_coverage"]["start_at"] is None
        if raw
        else source["parquet"][0]["timestamp_column"] == "report_date_ns"
    )
    evaluator = FrameworkEvaluator(HistoricalBacktestConfig(root_dir=tmp_path / "empty"))
    assert evaluator.training_input_contract["state_feature_sources"]["cme_positioning_in_state_features"] is (not raw)
    review = DataAdmissionAgent().review([_candidate(str(path))], repo_root=tmp_path)[0]
    assert review.blocked_by == "cot_publication_time_unavailable"


def test_mixed_physical_fragments_are_not_admitted(tmp_path: Path) -> None:
    dataset = tmp_path / "cot.parquet"
    _write(dataset / "a.parquet", raw=False)
    _write(dataset / "b.parquet", raw=True)
    assert not cot_data_available(dataset)
    review = DataAdmissionAgent().review([_candidate("cot.parquet")], repo_root=tmp_path)[0]
    assert review.blocked_by == "cot_publication_time_unavailable"


def test_hive_marker_and_mismatched_legacy_fragments_are_rejected(tmp_path: Path) -> None:
    hive = tmp_path / "hive.parquet"
    _write(hive / "position_as_of_date_ns=123" / "part.parquet", raw=False)
    assert not cot_data_available(hive)
    review = DataAdmissionAgent().review([_candidate(hive.name)], repo_root=tmp_path)[0]
    assert review.blocked_by == "cot_publication_time_unavailable"

    mixed = tmp_path / "mixed.parquet"
    _write(mixed / "a.parquet", raw=False)
    pq.write_table(pa.table({"report_date_ns": [1_600_000_000_000_000_000], "dealer_net": [7]}), mixed / "b.parquet")
    assert not cot_data_available(mixed)


def test_timestamp_only_and_configured_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    default = Path("data/raw/cftc/cot_btc.parquet")
    _write(default, raw=False)
    custom = Path("custom/cftc/cot_btc.parquet")
    custom.parent.mkdir(parents=True)
    pq.write_table(pa.table({"report_date_ns": [1_600_000_000_000_000_000]}), custom)
    assert not cot_data_available(custom)
    assert (
        _signal_data_dependency("cme_positioning_state_signal", macro_root=Path("macro"), data_root=Path("custom"))[1]
        == "ready"
    )
    evaluator = FrameworkEvaluator(HistoricalBacktestConfig(root_dir=tmp_path / "empty", cot_path=custom))
    assert evaluator.training_input_contract["state_feature_sources"]["cme_positioning_in_state_features"]
    _write(default, raw=True)
    _write(custom, raw=False)
    assert (
        _signal_data_dependency("cme_positioning_state_signal", macro_root=Path("macro"), data_root=Path("custom"))[1]
        == "missing"
    )
    evaluator = FrameworkEvaluator(HistoricalBacktestConfig(root_dir=tmp_path / "empty", cot_path=custom))
    assert not evaluator.training_input_contract["state_feature_sources"]["cme_positioning_in_state_features"]


def test_macro_state_reports_mixed_legacy_fragments(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    path = Path("data/raw/cftc/cot_btc.parquet")
    _write(path / "a.parquet", raw=False)
    pq.write_table(pa.table({"report_date_ns": [1_600_000_000_000_000_001], "dealer_net": [7]}), path / "b.parquet")
    assert not cot_data_available(path)
    assert (
        _signal_data_dependency("cme_positioning_state_signal", macro_root=Path("macro"), data_root=Path("custom"))[1]
        == "ready"
    )
    evaluator = FrameworkEvaluator(HistoricalBacktestConfig(root_dir=tmp_path / "empty"))
    assert evaluator.training_input_contract["state_feature_sources"]["cme_positioning_in_state_features"]


def test_coverage_glob_rejects_any_incompatible_file(tmp_path: Path) -> None:
    _write(tmp_path / "legacy.parquet", raw=False)
    _write(tmp_path / "raw.parquet", raw=True)
    spec = DataSourceSpec(
        source_id="cme_cot_btc",
        display_name="COT",
        source_type="positioning",
        parquet_globs=(str(tmp_path / "*.parquet"),),
        csv_globs=(),
        training_role="slow_context_only",
        formal_train_enabled=False,
        allowed_uses=(),
    )
    result = audit_source(spec, split_spec=default_btc_split_spec())
    assert result["parquet"][0]["file_count"] == 2
    assert result["parquet"][0]["error"] == "cot_unavailable_or_incompatible"
    assert result["combined_coverage"]["start_at"] is None
    (tmp_path / "raw.parquet").unlink()
    pq.write_table(
        pa.table({"report_date_ns": [1_600_000_000_000_000_000], "dealer_net": [7]}), tmp_path / "different.parquet"
    )
    assert cot_data_available(tmp_path / "legacy.parquet")
    assert cot_data_available(tmp_path / "different.parquet")
    result = audit_source(spec, split_spec=default_btc_split_spec())
    assert result["parquet"][0]["error"] == "cot_unavailable_or_incompatible"
    assert result["combined_coverage"]["start_at"] is None


def test_swarm_checks_legacy_report_date_and_ignores_unrelated_marker(tmp_path: Path) -> None:
    recent = tmp_path / "recent.parquet"
    pq.write_table(pa.table({"report_date_ns": [1_770_000_000_000_000_000], "asset_mgr_net": [42]}), recent)
    assert (
        DataAdmissionAgent().review([_candidate(recent.name)], repo_root=tmp_path)[0].blocked_by
        == "cot_publication_time_unavailable"
    )
    unrelated = tmp_path / "other.parquet"
    pq.write_table(pa.table({"position_as_of_date_ns": [1_600_000_000_000_000_000], "value": [1]}), unrelated)
    assert DataAdmissionAgent().review([_candidate(unrelated.name)], repo_root=tmp_path)[0].status == "admitted"


@pytest.mark.parametrize("date_field", ["report_date_ns", "position_as_of_date_ns"])
@pytest.mark.parametrize("partition_field", ["date", "position"])
def test_swarm_blocks_cot_hive_dataset(tmp_path: Path, date_field: str, partition_field: str) -> None:
    dataset = tmp_path / "cot_dataset"
    date = 1_600_000_000_000_000_000
    if partition_field == "date":
        partition = dataset / f"{date_field}={date}"
        columns = {"asset_mgr_net": [42]}
    else:
        partition = dataset / "asset_mgr_net=42"
        columns = {date_field: [date]}
    partition.mkdir(parents=True)
    pq.write_table(pa.table(columns), partition / "part.parquet")
    assert DataAdmissionAgent().review([_candidate(dataset.name)], repo_root=tmp_path)[0].blocked_by == (
        "cot_publication_time_unavailable"
    )


def test_swarm_checks_each_fragment_without_combining_unrelated_schemas(tmp_path: Path) -> None:
    dataset = tmp_path / "mixed"
    dataset.mkdir()
    pq.write_table(
        pa.table({"value": [1], "position_as_of_date_ns": [1_600_000_000_000_000_000]}), dataset / "a.parquet"
    )
    pq.write_table(pa.table({"value": [2], "asset_mgr_net": [42]}), dataset / "b.parquet")
    assert DataAdmissionAgent().review([_candidate(dataset.name)], repo_root=tmp_path)[0].status == "admitted"
    pq.write_table(
        pa.table({"position_as_of_date_ns": [1_600_000_000_000_000_000], "asset_mgr_net": [42]}),
        dataset / "b.parquet",
    )
    assert DataAdmissionAgent().review([_candidate(dataset.name)], repo_root=tmp_path)[0].blocked_by == (
        "cot_publication_time_unavailable"
    )


def test_other_parquet_source_is_unchanged(tmp_path: Path) -> None:
    path = tmp_path / "other.parquet"
    pd.DataFrame({"ts_ns": [1_600_000_000_000_000_000], "value": [1]}).to_parquet(path)
    review = DataAdmissionAgent().review([_candidate(path.name)], repo_root=tmp_path)[0]
    assert review.status == "admitted"
