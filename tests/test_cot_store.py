"""Unit tests for CotDataStore point-in-time lookups."""

import io
import shutil
import subprocess
import tempfile
import threading
import unittest
import zipfile
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

WEEK_NS = 7 * 24 * 3600 * 1_000_000_000


def _write_cot_parquet(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pydict({k: [r[k] for r in records] for k in records[0]})
    pq.write_table(table, path)


def _make_cot_row(
    week: int,
    asset_mgr_net: int = 1000,
    leveraged_net: int = -500,
    dealer_net: int = 200,
    total_oi: int = 50000,
) -> dict:
    return {
        "report_date_ns": week * WEEK_NS,
        "market_name": "BITCOIN - CHICAGO MERCANTILE EXCHANGE",
        "asset_mgr_long": max(0, asset_mgr_net),
        "asset_mgr_short": max(0, -asset_mgr_net),
        "asset_mgr_net": asset_mgr_net,
        "leveraged_long": max(0, leveraged_net),
        "leveraged_short": max(0, -leveraged_net),
        "leveraged_net": leveraged_net,
        "dealer_long": max(0, dealer_net),
        "dealer_short": max(0, -dealer_net),
        "dealer_net": dealer_net,
        "total_oi": total_oi,
    }


class CotStoreTests(unittest.TestCase):
    def test_empty_store(self) -> None:
        from zenquant.data import CotDataStore, CotStoreConfig

        store = CotDataStore(CotStoreConfig(cot_path=Path("/nonexistent")))
        store.load()
        self.assertFalse(store.loaded)
        snap = store.lookup(WEEK_NS * 10)
        self.assertEqual(snap.asset_mgr_net, 0)

    def test_point_in_time(self) -> None:
        from zenquant.data import CotDataStore, CotStoreConfig

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "cot.parquet"
            records = [
                _make_cot_row(1, asset_mgr_net=1000, leveraged_net=-500),
                _make_cot_row(2, asset_mgr_net=1500, leveraged_net=-800),
                _make_cot_row(3, asset_mgr_net=2000, leveraged_net=-200),
            ]
            _write_cot_parquet(path, records)

            store = CotDataStore(CotStoreConfig(cot_path=path))
            store.load()
            self.assertTrue(store.loaded)

            # Before any data
            snap = store.lookup(0)
            self.assertEqual(snap.asset_mgr_net, 0)

            # Between week 1 and 2
            snap = store.lookup(int(1.5 * WEEK_NS))
            self.assertEqual(snap.asset_mgr_net, 1000)
            self.assertEqual(snap.leveraged_net, -500)

            # After week 3
            snap = store.lookup(4 * WEEK_NS)
            self.assertEqual(snap.asset_mgr_net, 2000)

    def test_staleness_flag(self) -> None:
        from zenquant.data import CotDataStore, CotStoreConfig

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "cot.parquet"
            _write_cot_parquet(path, [_make_cot_row(1)])

            store = CotDataStore(CotStoreConfig(cot_path=path))
            store.load()

            # Within staleness
            snap = store.lookup(int(1.5 * WEEK_NS))
            self.assertNotIn("COT_STALE", snap.quality_flags)

            # Beyond staleness (>7 days)
            snap = store.lookup(10 * WEEK_NS)
            self.assertIn("COT_STALE", snap.quality_flags)

    def test_z_score_extreme_positioning(self) -> None:
        from zenquant.data import CotDataStore, CotStoreConfig

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "cot.parquet"
            # 20 normal weeks + 1 extreme
            records = []
            for i in range(20):
                records.append(
                    _make_cot_row(
                        i + 1,
                        asset_mgr_net=1000 + (i % 5) * 100,
                        leveraged_net=-500 + (i % 3) * 50,
                    )
                )
            # Extreme crowding
            records.append(_make_cot_row(21, asset_mgr_net=10000, leveraged_net=-5000))
            _write_cot_parquet(path, records)

            store = CotDataStore(CotStoreConfig(cot_path=path))
            store.load()

            snap = store.lookup(22 * WEEK_NS)
            self.assertGreater(snap.asset_mgr_z, 2.0)


def test_raw_cot_schema_is_rejected_with_hive_report_date(tmp_path: Path) -> None:
    from zenquant.data import CotDataStore, CotStoreConfig

    partition = tmp_path / f"report_date_ns={WEEK_NS}"
    raw = _make_cot_row(1)
    raw["position_as_of_date_ns"] = raw.pop("report_date_ns")
    path = partition / "cot.parquet"
    _write_cot_parquet(path, [raw])
    assert {"report_date_ns", "position_as_of_date_ns"} <= set(pq.read_table(path).column_names)

    for candidate in (path, tmp_path):
        store = CotDataStore(CotStoreConfig(cot_path=candidate))
        store.load()
        assert not store.loaded
        assert store.lookup(WEEK_NS).asset_mgr_net == 0


@pytest.mark.parametrize("raw_first", [False, True])
@pytest.mark.parametrize("hive", [False, True])
def test_mixed_legacy_and_raw_fragments_are_rejected(tmp_path: Path, raw_first: bool, hive: bool) -> None:
    from zenquant.data import CotDataStore, CotStoreConfig

    legacy = _make_cot_row(1)
    raw = _make_cot_row(2)
    raw["position_as_of_date_ns"] = raw.pop("report_date_ns")
    directory = tmp_path
    if hive:
        directory = tmp_path / f"report_date_ns={WEEK_NS}"
        legacy.pop("report_date_ns")
    _write_cot_parquet(directory / ("b.parquet" if raw_first else "a.parquet"), [legacy])
    _write_cot_parquet(directory / ("a.parquet" if raw_first else "b.parquet"), [raw])

    dataset = pq.ParquetDataset(tmp_path)
    assert len(dataset.fragments) == 2
    if not raw_first:
        assert "position_as_of_date_ns" not in dataset.schema.names
    store = CotDataStore(CotStoreConfig(cot_path=tmp_path))
    store.load()
    assert not store.loaded
    assert store.lookup(2 * WEEK_NS).asset_mgr_net == 0


def test_failed_conversion_does_not_leave_partial_state(tmp_path: Path) -> None:
    from zenquant.data import CotDataStore, CotStoreConfig

    path = tmp_path / "cot.parquet"
    _write_cot_parquet(path, [_make_cot_row(1, asset_mgr_net=123)])
    store = CotDataStore(CotStoreConfig(cot_path=path))
    store.load()
    assert store.lookup(WEEK_NS).asset_mgr_net == 123

    first = _make_cot_row(1)
    second = _make_cot_row(2)
    first["dealer_net"] = "200"
    second["dealer_net"] = "invalid"
    _write_cot_parquet(path, [first, second])
    store.load()
    assert not store.loaded
    assert store.lookup(2 * WEEK_NS).asset_mgr_net == 0
    assert store.lookup(10 * WEEK_NS).quality_flags == []

    _write_cot_parquet(path, [_make_cot_row(2, asset_mgr_net=456)])
    store.load()
    assert store.loaded
    assert store.lookup(2 * WEEK_NS).asset_mgr_net == 456


@pytest.mark.skipif(shutil.which("go") is None, reason="Go toolchain is unavailable")
def test_go_cot_parquet_is_not_available_to_python_store(tmp_path: Path) -> None:
    from zenquant.data import CotDataStore, CotStoreConfig

    year = datetime.now(UTC).year
    report_date = datetime(year, 1, 6, tzinfo=UTC)
    report_ns = int(report_date.timestamp()) * 1_000_000_000
    content = (
        "Market_and_Exchange_Names,Report_Date_as_YYYY-MM-DD,Asset_Mgr_Positions_Long_All,"
        "Asset_Mgr_Positions_Short_All,Lev_Money_Positions_Long_All,Lev_Money_Positions_Short_All,"
        "Dealer_Positions_Long_All,Dealer_Positions_Short_All,Open_Interest_All\n"
        f'"BITCOIN - CHICAGO MERCANTILE EXCHANGE",{year}-01-06,"1,250",400,30,70,5,9,2000\n'
    )
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("FinFutYY.txt", content)
    payload = archive.getvalue()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != f"/reports/fut_fin_txt_{year}.zip":
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        subprocess.run(
            [
                "go",
                "run",
                "./cmd/zenquant",
                "download-cot",
                "--years",
                "1",
                "--base-url",
                f"http://127.0.0.1:{server.server_port}/reports",
                "--output-root",
                str(tmp_path / "output"),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=120,
        )
    finally:
        server.shutdown()
        thread.join()
        server.server_close()

    (path,) = (tmp_path / "output").glob("cot-run-*/cot_btc.parquet")
    table = pq.read_table(path)
    assert "report_date_ns" not in table.column_names
    assert table.to_pylist() == [
        {
            "position_as_of_date_ns": report_ns,
            "market_name": "BITCOIN - CHICAGO MERCANTILE EXCHANGE",
            "asset_mgr_long": 1250,
            "asset_mgr_short": 400,
            "asset_mgr_net": 850,
            "leveraged_long": 30,
            "leveraged_short": 70,
            "leveraged_net": -40,
            "dealer_long": 5,
            "dealer_short": 9,
            "dealer_net": -4,
            "total_oi": 2000,
        }
    ]
    legacy_path = tmp_path / "legacy.parquet"
    _write_cot_parquet(legacy_path, [_make_cot_row(1)])
    config = CotStoreConfig(cot_path=legacy_path)
    store = CotDataStore(config)
    store.load()
    assert store.loaded and store.lookup(report_ns).asset_mgr_net == 1000
    for candidate in (path, path.parent):
        config.cot_path = candidate
        store.load()
        assert not store.loaded
        assert store.lookup(report_ns).asset_mgr_net == 0
        assert store.lookup(report_ns + 10 * WEEK_NS).asset_mgr_net == 0


if __name__ == "__main__":
    unittest.main()
