import tempfile
import unittest
from pathlib import Path

from zenquant.domain import Timeframe
from zenquant.historical.storage import HistoricalKlineRow, write_parquet_rows
from zenquant.research.historical_extension import build_historical_extension_candidate_contract


def _write_row(path: Path, timeframe: Timeframe, start_ns: int) -> None:
    write_parquet_rows(
        path,
        [
            HistoricalKlineRow(
                symbol="BTCUSDT",
                timeframe=timeframe.value,
                open_time_ns=start_ns,
                close_time_ns=start_ns + timeframe.seconds * 1_000_000_000 - 1_000_000,
                open=100.0,
                high=101.0,
                low=99.0,
                close=100.5,
                volume=1.0,
                trade_count=1,
            )
        ],
    )


class HistoricalExtensionContractTests(unittest.TestCase):
    def test_contract_marks_spot_history_as_context_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            spot_root = root / "binance_spot"
            futures_root = root / "binance"
            _write_row(
                spot_root / "klines" / "BTCUSDT" / "1d" / "data.parquet", Timeframe.TF_1D, 1_503_014_400_000_000_000
            )
            _write_row(
                spot_root / "klines" / "BTCUSDT" / "4h" / "data.parquet", Timeframe.TF_4H, 1_503_003_600_000_000_000
            )
            _write_row(
                spot_root / "klines" / "BTCUSDT" / "1h" / "data.parquet", Timeframe.TF_1H, 1_502_982_000_000_000_000
            )
            _write_row(
                futures_root / "klines" / "BTCUSDT" / "1h" / "data.parquet", Timeframe.TF_1H, 1_577_840_400_000_000_000
            )

            contract = build_historical_extension_candidate_contract(
                symbol="BTCUSDT",
                spot_root=spot_root,
                futures_root=futures_root,
            )

        self.assertEqual(contract.candidate_source_type, "spot")
        self.assertFalse(contract.layered_train_enabled)
        self.assertFalse(contract.formal_train_truth)
        self.assertEqual(contract.historical_extension_mode, "spot_context_only")
        self.assertEqual(contract.dev_truth_source, "futures_only")
        self.assertEqual(contract.test_truth_source, "futures_only")
        self.assertFalse(contract.mixed_era_declared)
        self.assertFalse(contract.full_unified_truth)
        self.assertTrue(contract.ready)
        self.assertIn("spot_perp_truth_boundary", contract.blocked_by)
        self.assertIn("full_unified_truth_blocked", contract.blocked_by)
        self.assertIn("spot_context_only_not_formal_train_truth", contract.spot_train_leg_blocked_by)
        self.assertEqual(contract.timeframes["1d"].timeframe, "1d")
        self.assertTrue(contract.timeframes["1h"].present)
        self.assertEqual(len(contract.train_truth_legs), 1)
        self.assertEqual(contract.train_truth_legs[0]["name"], "post_2020_futures_train")
        self.assertIn("pre_2020_regime_context", contract.allowed_uses)
