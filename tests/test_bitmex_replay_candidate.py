import tempfile
import unittest
from pathlib import Path

from zenquant.domain import Timeframe
from zenquant.historical.storage import HistoricalKlineRow, write_parquet_rows
from zenquant.research.bitmex_replay_candidate import (
    build_bitmex_replay_candidate_contract,
    write_bitmex_replay_candidate_contract,
)


def _write_bitmex_row(path: Path, timeframe: Timeframe, start_ns: int) -> None:
    write_parquet_rows(
        path,
        [
            HistoricalKlineRow(
                symbol="XBTUSD",
                timeframe=timeframe.value,
                open_time_ns=start_ns,
                close_time_ns=start_ns + timeframe.seconds * 1_000_000_000,
                open=1000.0,
                high=1010.0,
                low=990.0,
                close=1005.0,
                volume=1.0,
                trade_count=10,
            )
        ],
    )


class BitmexReplayCandidateTests(unittest.TestCase):
    def test_contract_keeps_bitmex_as_candidate_not_formal_truth(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "raw"
            _write_bitmex_row(
                root / "bitmex" / "klines" / "XBTUSD" / "4h" / "2017-01.parquet",
                Timeframe.TF_4H,
                1_483_228_800_000_000_000,
            )
            _write_bitmex_row(
                root / "bitmex" / "klines" / "XBTUSD" / "4h" / "2020-01.parquet",
                Timeframe.TF_4H,
                1_577_836_800_000_000_000,
            )

            contract = build_bitmex_replay_candidate_contract(data_root=root)

        self.assertTrue(contract.replay_base_candidate)
        self.assertFalse(contract.formal_train_truth)
        self.assertFalse(contract.promotion_ready)
        self.assertTrue(contract.local_coverage_ready)
        self.assertIn("do_not_mix_binance_spot_as_execution_truth", contract.blocked_by)
        self.assertIn("silent_merge_with_binance_spot_context", contract.forbidden_uses)
        self.assertEqual(contract.archive_key_pattern, "data/trade/YYYYMMDD.csv.gz")

    def test_contract_marks_missing_local_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            contract = build_bitmex_replay_candidate_contract(data_root=Path(tmp) / "raw")

        self.assertFalse(contract.local_coverage_ready)
        self.assertIn("local_2017_2020_coverage_missing", contract.blocked_by)

    def test_write_contract_creates_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            contract = build_bitmex_replay_candidate_contract(data_root=root / "raw")
            result = write_bitmex_replay_candidate_contract(contract, output_dir=root / "out")

            json_path = Path(result["json_path"])
            md_path = Path(result["md_path"])
            self.assertTrue(json_path.exists())
            self.assertTrue(md_path.exists())
            self.assertFalse(result["promotion_ready"])


if __name__ == "__main__":
    unittest.main()
