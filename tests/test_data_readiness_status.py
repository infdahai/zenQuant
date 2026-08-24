import importlib.util
import json
import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd

from zenquant.data.deribit_block_flow import DeribitBlockFlowRow, save_block_flow_rows, write_deribit_block_flow_status
from zenquant.data.deribit_status import write_deribit_status
from zenquant.data.liquidation_pipeline import (
    LiquidationEvent,
    aggregate_liquidation_events,
    write_aggregated_liquidations,
)
from zenquant.historical import HistoricalDatasetLayout, HistoricalKlineRow, write_parquet_rows


def _load_module(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


DATA_READINESS = _load_module("data_readiness_status_module", "scripts/data_readiness_status.py")


def _ts(year: int, month: int, day: int, hour: int) -> int:
    return int(datetime(year, month, day, hour, tzinfo=UTC).timestamp() * 1_000_000_000)


DAY_NS = 86_400_000_000_000
TREND_WARMUP_START_NS = _ts(2025, 10, 8, 0)
TREND_REUSED_START_NS = _ts(2026, 1, 1, 0)
TREND_REUSED_END_NS = _ts(2026, 8, 24, 0)


def _write_trend_spot(
    path: Path,
    *,
    missing_open_ns: tuple[int, ...] = (),
    duplicate_open_ns: int | None = None,
    post_end_count: int = 0,
    pre_start_count: int = 0,
    close: float = 100_000.0,
) -> Path:
    opens = [
        value for value in range(TREND_WARMUP_START_NS, TREND_REUSED_END_NS, DAY_NS) if value not in missing_open_ns
    ]
    opens.extend(TREND_REUSED_END_NS + index * DAY_NS for index in range(post_end_count))
    opens.extend(TREND_WARMUP_START_NS - (index + 1) * DAY_NS for index in range(pre_start_count))
    if duplicate_open_ns is not None:
        opens.append(duplicate_open_ns)
    frame = pd.DataFrame(
        {
            "open_time_ns": opens,
            "close_time_ns": [value + DAY_NS - 1 for value in opens],
            "close": [close] * len(opens),
        }
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(path, index=False)
    return path


def _write_deribit_snapshot(path: Path, ts: datetime) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    source_ns = int(ts.timestamp() * 1_000_000_000)
    rows = []
    for dte in (7, 30, 90):
        for option_type in ("call", "put"):
            rows.append(
                {
                    "snapshot_ts_ns": source_ns,
                    "expiry_ts_ms": int((ts + timedelta(days=dte)).timestamp() * 1_000),
                    "strike": 100_000.0,
                    "option_type": option_type,
                    "underlying_price": 100_000.0,
                    "open_interest": 100.0,
                    "mark_iv": 60.0,
                }
            )
    pq.write_table(pa.Table.from_pylist(rows), path)


class DataReadinessStatusTests(unittest.TestCase):
    def _empty_roots(self, root: Path) -> dict[str, Path | str]:
        return {
            "binance_root": root / "binance",
            "symbol": "BTCUSDT",
            "deribit_root": root / "deribit",
            "deribit_block_flow_root": root / "block_flow",
            "hyperliquid_root": root / "hyperliquid",
            "merged_root": root / "merged",
        }

    def test_trend_readiness_reports_separate_warmup_and_reused_gaps(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            warmup_gap = TREND_WARMUP_START_NS + DAY_NS
            reused_gap = TREND_REUSED_START_NS + 2 * DAY_NS
            spot_path = _write_trend_spot(
                root / "spot.parquet",
                missing_open_ns=(warmup_gap, reused_gap),
            )

            payload = DATA_READINESS.build_readiness_payload(
                **self._empty_roots(root),
                trend_spot_path=spot_path,
            )

        status = payload["trend_research_status"]
        self.assertEqual(status["status"], "INSUFFICIENT_DATA_SPOT_DAILY_GAPS")
        self.assertEqual(status["fallback"], "NONE")
        self.assertEqual(status["warmup_missing_open_ns"], [warmup_gap])
        self.assertEqual(status["warmup_missing_count"], 1)
        self.assertEqual(status["reused_missing_open_ns"], [reused_gap])
        self.assertEqual(status["reused_missing_count"], 1)
        self.assertEqual(status["missing_open_ns"], [warmup_gap, reused_gap])
        self.assertEqual(status["missing_count"], 2)
        self.assertIn("trend_research:INSUFFICIENT_DATA_SPOT_DAILY_GAPS", payload["overall_blockers"])

    def test_trend_readiness_requires_exact_frozen_daily_corpus(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            spot_path = _write_trend_spot(root / "spot.parquet")

            payload = DATA_READINESS.build_readiness_payload(
                **self._empty_roots(root),
                trend_spot_path=spot_path,
            )

        status = payload["trend_research_status"]
        self.assertEqual(status["status"], "READY")
        self.assertTrue(status["ready"])
        self.assertEqual(status["warmup_missing_count"], 0)
        self.assertEqual(status["reused_missing_count"], 0)
        self.assertEqual(status["duplicate_open_ns"], [])
        self.assertEqual(status["post_end_count"], 0)
        self.assertEqual(status["first_cutoff_ns"], TREND_WARMUP_START_NS + DAY_NS)
        self.assertEqual(status["last_cutoff_ns"], TREND_REUSED_END_NS)
        self.assertEqual(status["next_action"], "freeze_trend_research_inputs")
        markdown = DATA_READINESS.build_markdown(payload)
        self.assertIn("## Trend Research Spot Daily", markdown)
        self.assertIn("- status: `READY`", markdown)

    def test_trend_readiness_fails_closed_for_stale_post_end_and_malformed_data(self) -> None:
        cases = (
            (
                "stale",
                {"missing_open_ns": (TREND_REUSED_END_NS - DAY_NS,)},
                "INSUFFICIENT_DATA_SPOT_DAILY_STALE",
            ),
            (
                "post_end",
                {"post_end_count": 1},
                "INSUFFICIENT_DATA_SPOT_DAILY_POST_END",
            ),
        )
        for name, kwargs, expected in cases:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                spot_path = _write_trend_spot(root / "spot.parquet", **kwargs)
                payload = DATA_READINESS.build_readiness_payload(
                    **self._empty_roots(root),
                    trend_spot_path=spot_path,
                )
                self.assertEqual(payload["trend_research_status"]["status"], expected)
                self.assertFalse(payload["trend_research_status"]["ready"])

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            malformed = root / "malformed.parquet"
            pd.DataFrame({"wrong": [1]}).to_parquet(malformed, index=False)
            payload = DATA_READINESS.build_readiness_payload(
                **self._empty_roots(root),
                trend_spot_path=malformed,
            )
        self.assertEqual(
            payload["trend_research_status"]["status"],
            "INSUFFICIENT_DATA_SPOT_DAILY_MALFORMED",
        )
        self.assertEqual(payload["trend_research_status"]["fallback"], "NONE")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            invalid_close = _write_trend_spot(root / "invalid-close.parquet", close=float("nan"))
            payload = DATA_READINESS.build_readiness_payload(
                **self._empty_roots(root),
                trend_spot_path=invalid_close,
            )
        self.assertEqual(
            payload["trend_research_status"]["status"],
            "INSUFFICIENT_DATA_SPOT_DAILY_MALFORMED",
        )

    def test_trend_readiness_rejects_rows_before_exact_warmup_start(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            spot_path = _write_trend_spot(root / "spot.parquet", pre_start_count=1)

            payload = DATA_READINESS.build_readiness_payload(
                **self._empty_roots(root),
                trend_spot_path=spot_path,
            )

        status = payload["trend_research_status"]
        self.assertEqual(status["status"], "INSUFFICIENT_DATA_SPOT_DAILY_PRE_START")
        self.assertEqual(status["pre_start_count"], 1)
        self.assertEqual(status["next_action"], "crop_and_renormalize_trend_spot_daily")
        self.assertFalse(status["ready"])
        self.assertEqual(payload["trend_research_status"]["fallback"], "NONE")

    def test_omitted_trend_path_preserves_existing_overall_blockers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = DATA_READINESS.build_readiness_payload(**self._empty_roots(root))

        self.assertEqual(payload["trend_research_status"]["status"], "NOT_CONFIGURED")
        self.assertNotIn("trend_research:NOT_CONFIGURED", payload["overall_blockers"])

    def test_explicit_missing_trend_path_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = DATA_READINESS.build_readiness_payload(
                **self._empty_roots(root),
                trend_spot_path=root / "missing.parquet",
            )

        status = payload["trend_research_status"]
        self.assertEqual(status["status"], "INSUFFICIENT_DATA_SPOT_DAILY_MISSING")
        self.assertEqual(status["fallback"], "NONE")
        self.assertFalse(status["ready"])
        self.assertEqual(status["warmup_missing_count"], 85)
        self.assertEqual(status["reused_missing_count"], 235)
        self.assertEqual(status["missing_count"], 320)
        self.assertIn("trend_research:INSUFFICIENT_DATA_SPOT_DAILY_MISSING", payload["overall_blockers"])

    def test_trend_repair_runbook_uses_exact_normalization_and_registration_paths(self) -> None:
        runbook = Path("docs/FORWARD_COLLECTION_AUTOMATION.md").read_text(encoding="utf-8")

        self.assertIn("--start-open-date 2025-10-08", runbook)
        self.assertIn("--end-open-date-exclusive 2026-08-24", runbook)
        self.assertIn("--timeframes 1d", runbook)
        self.assertIn(
            "--spot-daily-output-path data/derived/trend_research/20260824/BTCUSDT/1d/data.parquet",
            runbook,
        )
        self.assertIn(
            "--trend-spot-path data/derived/trend_research/20260824/BTCUSDT/1d/data.parquet",
            runbook,
        )
        self.assertIn("--spot-1d data/derived/trend_research/20260824/BTCUSDT/1d/data.parquet", runbook)
        self.assertIn("--output-dir config/research/registrations", runbook)
        self.assertIn("config/research/registrations/time_space_shadow_execution_v1.json", runbook)
        self.assertNotIn("--output-dir results/trend_research_execution/20260824", runbook)

    def test_build_payload_marks_partial_and_blocked_states(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            binance_root = root / "binance"
            deribit_root = root / "deribit"
            deribit_block_flow_root = root / "block_flow"
            hyperliquid_root = root / "hyperliquid"
            merged_root = root / "merged"

            layout = HistoricalDatasetLayout(root_dir=binance_root)
            kline_dir = layout.kline_path("BTCUSDT", "4h", "2024-01")
            write_parquet_rows(
                kline_dir,
                [
                    HistoricalKlineRow(
                        symbol="BTCUSDT",
                        timeframe="4h",
                        open_time_ns=_ts(2024, 1, 1, 0),
                        close_time_ns=_ts(2024, 1, 1, 4),
                        open=40000.0,
                        high=40100.0,
                        low=39900.0,
                        close=40050.0,
                        volume=100.0,
                        trade_count=10,
                    )
                ],
            )

            recent = datetime.now(UTC)
            target = deribit_root / "BTC" / recent.strftime("%Y-%m")
            target.mkdir(parents=True, exist_ok=True)
            for index in range(5):
                ts = recent - timedelta(hours=index * 4)
                _write_deribit_snapshot(
                    target / f"options_chain_{ts.strftime('%Y%m%dT%H%M%SZ')}.parquet",
                    ts,
                )
            write_deribit_status(deribit_root)
            save_block_flow_rows(
                [
                    DeribitBlockFlowRow(
                        ts_ns=int(recent.timestamp() * 1_000_000_000),
                        trade_id="t1",
                        block_trade_id=None,
                        combo_id=None,
                        combo_trade_id=None,
                        instrument_name="BTC-24APR26-72000-C",
                        option_type="c",
                        expiry_ts_ms=1776988800000,
                        strike=72000.0,
                        direction="buy",
                        amount=3.0,
                        price=0.0525,
                        index_price=66930.31,
                        mark_price=0.05253883,
                        iv=45.91,
                        liquidity=None,
                        is_block_trade=False,
                        is_combo_leg=False,
                        leg_count=1,
                        source="public_option_trade",
                    )
                ],
                deribit_block_flow_root,
            )
            write_deribit_block_flow_status(deribit_block_flow_root)

            hyperliquid_root.mkdir(parents=True, exist_ok=True)
            (hyperliquid_root / "_collector_status.json").write_text(
                json.dumps(
                    {
                        "last_success_ts": recent.isoformat(),
                        "recent_trades": 12,
                        "proxy_buckets": 0,
                        "blocked_by": None,
                        "detail": "recent collector ok",
                    }
                ),
                encoding="utf-8",
            )
            (hyperliquid_root / "_backfill_status.json").write_text(
                json.dumps(
                    {
                        "last_success_ts": recent.isoformat(),
                        "proxy_buckets": 4,
                        "blocked_by": "hyperliquid_public_history_limit",
                        "detail": "limited backfill",
                        "promotion_ready": False,
                    }
                ),
                encoding="utf-8",
            )

            merged_buckets = aggregate_liquidation_events(
                [
                    LiquidationEvent(
                        ts_ns=_ts(2024, 1, 1, 1),
                        symbol="BTCUSDT",
                        side="SELL",
                        price=40000.0,
                        qty=1.0,
                        usd_value=40000.0,
                        source="hyperliquid_public",
                        trade_time_ms=_ts(2024, 1, 1, 1) // 1_000_000,
                    )
                ]
            )
            write_aggregated_liquidations(merged_root, merged_buckets)

            payload = DATA_READINESS.build_readiness_payload(
                binance_root=binance_root,
                symbol="BTCUSDT",
                deribit_root=deribit_root,
                deribit_block_flow_root=deribit_block_flow_root,
                hyperliquid_root=hyperliquid_root,
                merged_root=merged_root,
            )

        self.assertEqual(payload["deribit_status"]["status"], "partial")
        self.assertEqual(payload["deribit_status"]["next_action"], "keep_collector_running")
        self.assertEqual(payload["deribit_block_flow_status"]["status"], "completed")
        self.assertEqual(payload["binance_liquidation_status"]["blocked_by"], "missing_liquidation_history")
        self.assertEqual(payload["binance_liquidation_status"]["next_action"], "ingest_binance_liquidation_history")
        self.assertTrue(payload["hyperliquid_status"]["backfill_ready_min"])
        self.assertIn("days_covered", payload["hyperliquid_status"])
        self.assertIn("coverage_ratio_estimate", payload["hyperliquid_status"])
        self.assertTrue(payload["merged_liquidation_status"]["research_ready_min"])
        self.assertFalse(payload["merged_liquidation_status"]["promotion_ready"])
        self.assertIn("coverage_ratio", payload["merged_liquidation_status"])
        self.assertIn("next_action", payload["merged_liquidation_status"])
        self.assertIn("merged_liquidation_not_promotion_ready", payload["overall_blockers"])


if __name__ == "__main__":
    unittest.main()
