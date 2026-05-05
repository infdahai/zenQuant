import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from zenquant.research.deribit_large_option_factor_audit import (
    build_deribit_large_option_flow_audit,
    build_large_option_flow_features,
    load_btc_4h_prices,
    load_deribit_block_flow_rows,
)


class DeribitLargeOptionFactorAuditTests(unittest.TestCase):
    def test_build_features_assigns_large_flow_to_next_bar_close(self) -> None:
        prices = _price_frame("2026-04-17T00:00:00+00:00", 12)
        flow = _flow_frame(
            [
                {
                    "ts": "2026-04-17T03:30:00+00:00",
                    "instrument_name": "BTC-24APR26-72000-C",
                    "option_type": "c",
                    "amount": 50.0,
                    "price": 0.05,
                    "index_price": 70_000.0,
                    "direction": "buy",
                    "is_block_trade": True,
                    "is_combo_leg": False,
                },
                {
                    "ts": "2026-04-17T03:40:00+00:00",
                    "instrument_name": "BTC-24APR26-68000-P",
                    "option_type": "p",
                    "amount": 10.0,
                    "price": 0.03,
                    "index_price": 70_000.0,
                    "direction": "sell",
                    "is_block_trade": True,
                    "is_combo_leg": False,
                },
            ]
        )

        features = build_large_option_flow_features(
            flow=flow,
            prices=prices,
            min_amount=25.0,
            min_underlying_notional_usd=None,
            require_block_trade=True,
            rolling_window=6,
        )

        target = features[features["bar_close_ts"] == pd.Timestamp("2026-04-17T04:00:00+00:00")]
        self.assertEqual(len(target), 1)
        self.assertEqual(target.iloc[0]["large_contracts_4h"], 50.0)
        self.assertEqual(target.iloc[0]["large_call_put_contract_imbalance_4h"], 1.0)
        self.assertEqual(features.attrs["selected_large_row_count"], 1)

    def test_audit_blocks_when_large_flow_has_no_train_overlap(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            block_root = root / "block_flow"
            price_root = root / "prices"
            _write_flow(block_root, _flow_frame([_large_flow_payload("2026-04-17T03:30:00+00:00")]))
            _write_prices(price_root, _price_frame("2020-01-01T00:00:00+00:00", 24))

            payload = build_deribit_large_option_flow_audit(
                block_flow_root=block_root,
                btc_4h_dir=price_root,
                currency="BTC",
                train_start="2020-01-01T00:00:00+00:00",
                train_end="2020-01-05T00:00:00+00:00",
                min_amount=25.0,
                min_underlying_notional_usd=None,
                require_block_trade=False,
                rolling_window=6,
                corr_threshold=0.95,
                min_train_observations=10,
                min_abs_rank_ic=0.0,
                min_abs_ablation_delta=0.0,
            )

            self.assertEqual(payload["summary"]["blocked_by"], "no_train_aligned_large_option_flow_factor")
            self.assertEqual(payload["summary"]["next_action"], "backfill_deribit_block_flow_before_factor_admission")

    def test_features_do_not_fill_zero_flow_across_large_collection_gaps(self) -> None:
        prices = pd.concat(
            [
                _price_frame("2023-08-01T00:00:00+00:00", 12),
                _price_frame("2026-04-17T00:00:00+00:00", 12),
            ],
            ignore_index=True,
        )
        flow = _flow_frame(
            [
                _large_flow_payload("2023-08-01T03:30:00+00:00"),
                _large_flow_payload("2026-04-17T03:30:00+00:00"),
            ]
        )

        features = build_large_option_flow_features(
            flow=flow,
            prices=prices,
            min_amount=25.0,
            min_underlying_notional_usd=None,
            require_block_trade=False,
            rolling_window=6,
        )

        self.assertEqual(len(features.attrs["coverage_segments"]), 2)
        gap_rows = features[
            (features["bar_close_ts"] > pd.Timestamp("2023-08-03T00:00:00+00:00"))
            & (features["bar_close_ts"] < pd.Timestamp("2026-04-17T00:00:00+00:00"))
        ]
        self.assertTrue(gap_rows.empty)

    def test_audit_produces_factor_target_rows_on_overlapping_train_window(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            block_root = root / "block_flow"
            price_root = root / "prices"
            prices = _price_frame("2026-04-17T00:00:00+00:00", 80)
            flows = []
            for idx in range(20):
                ts = pd.Timestamp("2026-04-17T00:30:00+00:00") + pd.Timedelta(hours=8 * idx)
                flows.append(_large_flow_payload(ts.isoformat(), amount=25.0 + idx))
            _write_flow(block_root, _flow_frame(flows))
            _write_prices(price_root, prices)

            payload = build_deribit_large_option_flow_audit(
                block_flow_root=block_root,
                btc_4h_dir=price_root,
                currency="BTC",
                train_start="2026-04-17T00:00:00+00:00",
                train_end="2026-04-29T23:59:59+00:00",
                min_amount=25.0,
                min_underlying_notional_usd=None,
                require_block_trade=False,
                rolling_window=10,
                corr_threshold=0.99,
                min_train_observations=20,
                min_abs_rank_ic=0.0,
                min_abs_ablation_delta=0.0,
                feature_output_path=root / "features.parquet",
            )

            self.assertGreater(payload["summary"]["train_aligned_observations"], 0)
            self.assertGreater(payload["summary"]["factor_target_audit_count"], 0)
            self.assertEqual(set(payload["targets"]), set(_expected_targets()))
            self.assertTrue((root / "features.parquet").exists())

    def test_loaders_read_expected_partition_shapes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            block_root = root / "block_flow"
            price_root = root / "prices"
            _write_flow(block_root, _flow_frame([_large_flow_payload("2026-04-17T03:30:00+00:00")]))
            _write_prices(price_root, _price_frame("2026-04-17T00:00:00+00:00", 12))

            flow = load_deribit_block_flow_rows(block_root, "BTC")
            prices = load_btc_4h_prices(price_root)

            self.assertEqual(len(flow), 1)
            self.assertEqual(len(prices), 12)


def _expected_targets() -> list[str]:
    return [
        "forward_abs_return_4h",
        "forward_abs_return_1d",
        "trend_continuation_4h",
        "trend_continuation_1d",
    ]


def _price_frame(start: str, periods: int) -> pd.DataFrame:
    ts = pd.date_range(start=start, periods=periods, freq="4h", tz="UTC")
    close = 70_000.0 + np.sin(np.arange(periods) / 3.0) * 500.0 + np.arange(periods) * 20.0
    return pd.DataFrame(
        {
            "close_time_ns": ts.astype("int64"),
            "close": close,
            "bar_close_ts": ts,
        }
    )


def _flow_frame(rows: list[dict[str, object]]) -> pd.DataFrame:
    payload = []
    for idx, row in enumerate(rows):
        ts = pd.Timestamp(row["ts"])
        expiry = pd.Timestamp("2026-04-24T00:00:00+00:00")
        payload.append(
            {
                "ts_ns": int(ts.value),
                "trade_id": f"trade-{idx}",
                "block_trade_id": f"block-{idx}" if row.get("is_block_trade", False) else None,
                "combo_id": None,
                "combo_trade_id": None,
                "instrument_name": row["instrument_name"],
                "option_type": row["option_type"],
                "expiry_ts_ms": int(expiry.timestamp() * 1000),
                "strike": 72_000.0,
                "direction": row["direction"],
                "amount": row["amount"],
                "price": row["price"],
                "index_price": row["index_price"],
                "mark_price": row["price"],
                "iv": 45.0,
                "liquidity": None,
                "is_block_trade": row["is_block_trade"],
                "is_combo_leg": row["is_combo_leg"],
                "leg_count": 1,
                "source": "public_option_trade",
            }
        )
    return pd.DataFrame(payload)


def _large_flow_payload(ts: str, amount: float = 50.0) -> dict[str, object]:
    return {
        "ts": ts,
        "instrument_name": "BTC-24APR26-72000-C",
        "option_type": "c",
        "amount": amount,
        "price": 0.05,
        "index_price": 70_000.0,
        "direction": "buy",
        "is_block_trade": True,
        "is_combo_leg": False,
    }


def _write_flow(root: Path, frame: pd.DataFrame) -> None:
    path = root / "BTC" / "2026-04" / "data.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(path, index=False)


def _write_prices(root: Path, frame: pd.DataFrame) -> None:
    root.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(root / "data.parquet", index=False)


if __name__ == "__main__":
    unittest.main()
