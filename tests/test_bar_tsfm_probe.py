import tempfile
import unittest
from pathlib import Path

import pandas as pd

from zenquant.research.bar_tsfm_probe import (
    BarTSFMProbeConfig,
    build_probe_frame,
    run_bar_tsfm_probe,
)


def _build_5m_fixture(path: Path, *, windows: int = 32) -> None:
    rows: list[dict[str, float | int]] = []
    start_ms = int(pd.Timestamp("2020-01-01", tz="UTC").timestamp() * 1_000)
    price = 10_000.0
    for idx in range(windows * 48):
        window_idx = idx // 48
        phase = idx % 48
        open_time = start_ms + idx * 5 * 60 * 1_000
        drift = 0.0004 if window_idx % 4 in {0, 1} else -0.0003
        shock = 0.0015 if phase in {8, 9, 10} and window_idx % 5 == 0 else 0.0
        open_price = price
        close_price = price * (1.0 + drift + shock)
        high = max(open_price, close_price) * 1.0005
        low = min(open_price, close_price) * 0.9995
        volume = 100.0 + float(phase) + (50.0 if shock else 0.0)
        taker_buy = volume * (0.65 if drift > 0 else 0.35)
        rows.append(
            {
                "open_time": open_time,
                "open": open_price,
                "high": high,
                "low": low,
                "close": close_price,
                "volume": volume,
                "close_time": open_time + 5 * 60 * 1_000 - 1,
                "quote_volume": volume * close_price,
                "trade_count": int(20 + phase),
                "taker_buy_base": taker_buy,
                "taker_buy_quote": taker_buy * close_price,
                "ignore": 0,
            }
        )
        price = close_price
    pd.DataFrame(rows).to_parquet(path, index=False)


class BarTSFMProbeTests(unittest.TestCase):
    def test_probe_frame_builds_train_and_dev_windows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "5m.parquet"
            _build_5m_fixture(path)
            config = BarTSFMProbeConfig(
                kline_path=path,
                train_start="2020-01-01",
                train_end="2020-01-04",
                dev_start="2020-01-05",
                dev_end="2020-01-07",
            )
            frame = build_probe_frame(config)

        self.assertFalse(frame.empty)
        self.assertIn("seq_000", frame.columns)
        self.assertIn("realized_vol_5m", frame.columns)
        self.assertGreater(int((frame["split"] == "train").sum()), 0)
        self.assertGreater(int((frame["split"] == "dev").sum()), 0)

    def test_probe_report_keeps_direction_and_test_out_of_scope(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "5m.parquet"
            _build_5m_fixture(path, windows=72)
            config = BarTSFMProbeConfig(
                kline_path=path,
                train_start="2020-01-01",
                train_end="2020-01-08",
                dev_start="2020-01-09",
                dev_end="2020-01-12",
                pca_components=4,
            )
            payload = run_bar_tsfm_probe(config)

        self.assertFalse(payload["split_policy"]["test_accessed"])
        self.assertIn("direction_vote", payload["forbidden_uses"])
        self.assertEqual(payload["embedding_policy"], "sequence_pca_proxy_not_true_tsfm")
        self.assertFalse(payload["tsfm_embedding_status"]["requested"])
        self.assertFalse(payload["integration_ready"])
        self.assertGreaterEqual(len(payload["label_reviews"]), 3)


if __name__ == "__main__":
    unittest.main()
