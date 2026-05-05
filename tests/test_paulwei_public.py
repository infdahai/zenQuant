from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from zenquant.research.comparison import paulwei_mature_style_variant_specs, public_archive_variant_specs
from zenquant.research.paulwei_public import (
    build_ahr999_alignment,
    build_ahr999_execution_guidance_review,
    build_behavior_profile,
    build_execution_research_review,
    build_paulwei_dataset_contract,
    build_promotion_eligibility_review,
    build_strict_execution_learning_review,
    build_tranche_behavior_profile,
    build_variant_comparison_review,
    ingest_paulwei_public_dataset,
    write_baseline_mapping,
)


def _write_csv(path: Path, rows: list[dict]) -> None:
    frame = pd.DataFrame(rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False)


def _write_spot_daily(path: Path, days: int = 260) -> None:
    start = pd.Timestamp("2022-01-01T00:00:00Z")
    rows = []
    for i in range(days):
        ts = start + pd.Timedelta(days=i)
        close = 20_000 + i * 50
        rows.append(
            {
                "open": close - 10,
                "high": close + 25,
                "low": close - 25,
                "close": close,
                "close_time_ns": int((ts + pd.Timedelta(days=1) - pd.Timedelta(milliseconds=1)).value),
                "open_time_ns": int(ts.value),
            }
        )
    frame = pd.DataFrame(rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(path, index=False)


def _write_mvrv(path: Path, days: int = 260) -> None:
    start = pd.Timestamp("2022-01-01T00:00:00Z")
    rows = []
    for i in range(days):
        ts = start + pd.Timedelta(days=i)
        rows.append({"ts_ns": int(ts.value), "value": 0.8 + i * 0.002})
    frame = pd.DataFrame(rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(path, index=False)


def _build_public_source(root: Path) -> None:
    _write_csv(
        root / "api-v1-execution-tradeHistory.csv",
        [
            {
                "timestamp": "2022-02-01T00:00:00Z",
                "execType": "Trade",
                "symbol": "XBTUSD",
                "side": "Buy",
                "lastQty": 100,
                "lastPx": 40000,
                "foreignNotional": -100,
                "orderID": "o1",
                "execID": "e1",
            },
            {
                "timestamp": "2022-02-01T04:00:00Z",
                "execType": "Trade",
                "symbol": "XBTUSD",
                "side": "Buy",
                "lastQty": 50,
                "lastPx": 40500,
                "foreignNotional": -50,
                "orderID": "o2",
                "execID": "e2",
            },
            {
                "timestamp": "2022-02-01T08:00:00Z",
                "execType": "Trade",
                "symbol": "XBTUSD",
                "side": "Sell",
                "lastQty": 30,
                "lastPx": 41000,
                "foreignNotional": 30,
                "orderID": "o3",
                "execID": "e3",
            },
            {
                "timestamp": "2022-02-02T00:00:00Z",
                "execType": "Trade",
                "symbol": "XBTUSD",
                "side": "Sell",
                "lastQty": 120,
                "lastPx": 42000,
                "foreignNotional": 120,
                "orderID": "o4",
                "execID": "e4",
            },
            {
                "timestamp": "2022-02-02T06:00:00Z",
                "execType": "Trade",
                "symbol": "XBTUSD",
                "side": "Buy",
                "lastQty": 40,
                "lastPx": 41500,
                "foreignNotional": -40,
                "orderID": "o5",
                "execID": "e5",
            },
        ],
    )
    _write_csv(root / "api-v1-order.csv", [{"timestamp": "2022-02-01T00:00:00Z", "symbol": "XBTUSD"}])
    _write_csv(root / "api-v1-user-walletHistory.csv", [{"timestamp": "2022-02-01T00:00:00Z", "walletBalance": 1}])
    _write_csv(root / "api-v1-position.snapshot.csv", [{"timestamp": "2022-02-03T00:00:00Z", "currentQty": 0}])
    _write_csv(
        root / "api-v1-user-wallet.snapshot-all.csv",
        [{"timestamp": "2022-02-03T00:00:00Z", "currency": "XBt", "amount": 1}],
    )
    _write_csv(
        root / "api-v1-user-margin.snapshot-all.csv",
        [{"timestamp": "2022-02-03T00:00:00Z", "currency": "XBt", "walletBalance": 1}],
    )
    _write_csv(
        root / "derived-equity-curve.csv",
        [
            {"timestamp": "2022-02-01T00:00:00Z", "adjustedWealthXBT": 1.0},
            {"timestamp": "2022-02-02T00:00:00Z", "adjustedWealthXBT": 0.85},
            {"timestamp": "2022-02-03T00:00:00Z", "adjustedWealthXBT": 1.1},
        ],
    )
    (root / "manifest.json").write_text(json.dumps({"generated_at": "2026-01-01T00:00:00Z"}), encoding="utf-8")


class PaulWeiPublicResearchTests(unittest.TestCase):
    def test_ingest_and_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source"
            output = Path(tmp) / "output"
            results = Path(tmp) / "results"
            source.mkdir(parents=True, exist_ok=True)
            _build_public_source(source)

            ingest_paulwei_public_dataset(source_root=source, output_root=output)
            contract = build_paulwei_dataset_contract(data_root=output)

            self.assertTrue(contract["ready"])
            self.assertEqual(contract["source_type"], "external_public_account_archive")
            self.assertEqual(contract["venue"], "bitmex")
            self.assertFalse(contract["admit_as_primary_truth"])
            self.assertEqual(contract["missing_files"], [])

    def test_behavior_profile_and_mapping(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_root = Path(tmp) / "paulwei"
            spot_root = Path(tmp) / "spot"
            output = Path(tmp) / "results"
            _build_public_source(data_root)
            _write_spot_daily(spot_root / "klines" / "BTCUSDT" / "1d" / "data.parquet")

            profile, episodes = build_behavior_profile(data_root=data_root, spot_root=spot_root)
            mapping_info = write_baseline_mapping(behavior_profile=profile, output_dir=output)
            mapping = json.loads((output / "baseline_mapping.json").read_text(encoding="utf-8"))

            self.assertGreaterEqual(len(episodes), 2)
            self.assertIn("btc_notional_share", profile)
            self.assertIn("drawdown_profile", profile)
            self.assertEqual(mapping["mapping_confidence"], "medium")
            self.assertIn("derived_execution_rules", mapping)
            self.assertGreaterEqual(mapping["execution_config_overrides"]["pyramid_short_add_pct"], 0.10)
            self.assertTrue(Path(mapping_info["json_path"]).exists())

    def test_tranche_behavior_profile_separates_orders_fills_and_episodes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_root = Path(tmp) / "paulwei"
            _build_public_source(data_root)
            _write_csv(
                data_root / "api-v1-order.csv",
                [
                    {
                        "timestamp": "2022-02-01T00:00:00Z",
                        "ordStatus": "Filled",
                        "ordType": "Limit",
                        "symbol": "XBTUSD",
                        "side": "Buy",
                        "orderQty": 100,
                        "cumQty": 100,
                        "orderID": "o1",
                    },
                    {
                        "timestamp": "2022-02-01T04:00:00Z",
                        "ordStatus": "Filled",
                        "ordType": "Limit",
                        "symbol": "XBTUSD",
                        "side": "Buy",
                        "orderQty": 50,
                        "cumQty": 50,
                        "orderID": "o2",
                    },
                    {
                        "timestamp": "2022-02-01T08:00:00Z",
                        "ordStatus": "Filled",
                        "ordType": "Limit",
                        "symbol": "XBTUSD",
                        "side": "Sell",
                        "orderQty": 30,
                        "cumQty": 30,
                        "orderID": "o3",
                    },
                    {
                        "timestamp": "2022-02-02T00:00:00Z",
                        "ordStatus": "Filled",
                        "ordType": "Limit",
                        "symbol": "XBTUSD",
                        "side": "Sell",
                        "orderQty": 120,
                        "cumQty": 120,
                        "orderID": "o4",
                    },
                    {
                        "timestamp": "2022-02-02T06:00:00Z",
                        "ordStatus": "Filled",
                        "ordType": "Limit",
                        "symbol": "XBTUSD",
                        "side": "Buy",
                        "orderQty": 40,
                        "cumQty": 40,
                        "orderID": "o5",
                    },
                    {
                        "timestamp": "2022-02-02T08:00:00Z",
                        "ordStatus": "Canceled",
                        "ordType": "Limit",
                        "symbol": "XBTUSD",
                        "side": "Buy",
                        "orderQty": 60,
                        "cumQty": 0,
                        "orderID": "o6",
                    },
                ],
            )

            profile = build_tranche_behavior_profile(
                data_root=data_root,
                mature_start_at="2022-02-01T00:00:00+00:00",
            )
            full_window = profile["windows"][0]
            order_profile = full_window["order_fill_profile"]
            btc_profile = full_window["btc_tranche_profile"]

            self.assertEqual(order_profile["orders"], 6)
            self.assertEqual(order_profile["trade_execution_rows"], 5)
            self.assertEqual(order_profile["orders_without_trade_exec"], 1)
            self.assertEqual(order_profile["zero_fill_orders"], 1)
            self.assertEqual(btc_profile["position_episode_count"], 2)
            self.assertEqual(btc_profile["action_fill_counts"], {"add": 1, "entry": 2, "exit": 1, "reduce": 1})
            self.assertEqual(btc_profile["median_fill_rows_per_episode"], 2.5)
            self.assertEqual(btc_profile["median_entry_add_fill_rows_per_episode"], 1.5)
            self.assertEqual(btc_profile["median_reduce_exit_fill_rows_per_episode"], 1.0)

    def test_ahr999_alignment_and_public_archive_variant_specs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_root = Path(tmp) / "paulwei"
            spot_root = Path(tmp) / "spot"
            macro_root = Path(tmp) / "macro"
            output = Path(tmp) / "results"
            _build_public_source(data_root)
            _write_spot_daily(spot_root / "klines" / "BTCUSDT" / "1d" / "data.parquet")
            _write_mvrv(macro_root / "btc_mvrv" / "data.parquet")

            profile, _ = build_behavior_profile(data_root=data_root, spot_root=spot_root)
            write_baseline_mapping(behavior_profile=profile, output_dir=output)
            alignment = build_ahr999_alignment(data_root=data_root, spot_root=spot_root, macro_root=macro_root)
            variants = public_archive_variant_specs(mapping_path=output / "baseline_mapping.json")
            mature_variants = paulwei_mature_style_variant_specs(mapping_path=output / "baseline_mapping.json")

            self.assertTrue(alignment["ready"])
            self.assertIn("correlation_to_mvrv", alignment)
            self.assertEqual(variants[-1].name, "paulwei_public_archive_baseline")
            self.assertEqual(mature_variants[-1].name, "paulwei_mature_style_baseline")
            self.assertEqual(len(variants), 3)
            self.assertEqual(len(mature_variants), 3)

    def test_committed_paulwei_research_baseline_configs_load_as_variants(self) -> None:
        public_variants = public_archive_variant_specs(
            mapping_path=Path("config/research_baselines/paulwei_public_archive_2020_05_2026_04.json")
        )
        mature_variants = paulwei_mature_style_variant_specs(
            mapping_path=Path("config/research_baselines/paulwei_mature_style_2021_05_28.json")
        )

        self.assertEqual(public_variants[-1].name, "paulwei_public_archive_baseline")
        self.assertEqual(mature_variants[-1].name, "paulwei_mature_style_baseline")
        self.assertGreaterEqual(public_variants[-1].execution_config.pyramid_short_add_pct, 0.10)
        self.assertGreaterEqual(mature_variants[-1].execution_config.pyramid_short_add_pct, 0.10)

    def test_execution_guidance_and_promotion_reviews(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_root = Path(tmp) / "paulwei"
            spot_root = Path(tmp) / "spot"
            macro_root = Path(tmp) / "macro"
            output = Path(tmp) / "results"
            output.mkdir(parents=True, exist_ok=True)
            _build_public_source(data_root)
            _write_spot_daily(spot_root / "klines" / "BTCUSDT" / "1d" / "data.parquet")
            _write_mvrv(macro_root / "btc_mvrv" / "data.parquet")

            dataset_contract = build_paulwei_dataset_contract(data_root=data_root)
            dataset_contract_path = output / "paulwei_dataset_contract.json"
            dataset_contract_path.write_text(json.dumps(dataset_contract), encoding="utf-8")
            profile, _ = build_behavior_profile(data_root=data_root, spot_root=spot_root)
            behavior_profile_path = output / "behavior_profile.json"
            behavior_profile_path.write_text(json.dumps(profile), encoding="utf-8")
            write_baseline_mapping(behavior_profile=profile, output_dir=output)
            mapping = json.loads((output / "baseline_mapping.json").read_text(encoding="utf-8"))
            alignment = build_ahr999_alignment(data_root=data_root, spot_root=spot_root, macro_root=macro_root)
            ahr_alignment_path = output / "ahr999_alignment.json"
            ahr_alignment_path.write_text(json.dumps(alignment), encoding="utf-8")

            execution_review = build_execution_research_review(
                dataset_contract=dataset_contract,
                behavior_profile=profile,
                mapping=mapping,
                dataset_contract_path=dataset_contract_path,
                behavior_profile_path=behavior_profile_path,
                mapping_path=output / "baseline_mapping.json",
            )
            ahr_guidance = build_ahr999_execution_guidance_review(
                alignment=alignment,
                dataset_contract_path=dataset_contract_path,
                dataset_contract_id=dataset_contract.get("artifact_id"),
                behavior_profile_path=behavior_profile_path,
                behavior_profile_id=profile.get("artifact_id"),
                mapping_path=output / "baseline_mapping.json",
                mapping_id=mapping.get("artifact_id"),
                ahr_alignment_path=ahr_alignment_path,
            )
            comparison_payload = {
                "windows": [
                    {"label": "dev"},
                    {"label": "2023-08"},
                    {"label": "2024-11 DD"},
                ],
                "window_reports": [
                    {"variant": "current", "window_label": "dev", "metrics": {"sharpe": 4.0, "max_drawdown": 0.10}},
                    {"variant": "current", "window_label": "2023-08", "metrics": {"sharpe": 4.2, "max_drawdown": 0.05}},
                    {
                        "variant": "current",
                        "window_label": "2024-11 DD",
                        "metrics": {"sharpe": 5.0, "max_drawdown": 0.03},
                    },
                    {
                        "variant": "paulwei_baseline",
                        "window_label": "dev",
                        "metrics": {"sharpe": 4.0, "max_drawdown": 0.10},
                    },
                    {
                        "variant": "paulwei_baseline",
                        "window_label": "2023-08",
                        "metrics": {"sharpe": 4.0, "max_drawdown": 0.05},
                    },
                    {
                        "variant": "paulwei_baseline",
                        "window_label": "2024-11 DD",
                        "metrics": {"sharpe": 4.8, "max_drawdown": 0.04},
                    },
                    {
                        "variant": "paulwei_public_archive_baseline",
                        "window_label": "dev",
                        "metrics": {"sharpe": 4.3, "max_drawdown": 0.11},
                    },
                    {
                        "variant": "paulwei_public_archive_baseline",
                        "window_label": "2023-08",
                        "metrics": {"sharpe": 4.1, "max_drawdown": 0.05},
                    },
                    {
                        "variant": "paulwei_public_archive_baseline",
                        "window_label": "2024-11 DD",
                        "metrics": {"sharpe": 5.2, "max_drawdown": 0.031},
                    },
                ],
            }
            comparison_payload_path = output / "variant_comparison_payload.json"
            comparison_payload_path.write_text(json.dumps(comparison_payload), encoding="utf-8")
            variant_review = build_variant_comparison_review(
                comparison_payload=comparison_payload,
                mapping=mapping,
                dataset_contract_path=dataset_contract_path,
                dataset_contract_id=dataset_contract.get("artifact_id"),
                behavior_profile_path=behavior_profile_path,
                behavior_profile_id=profile.get("artifact_id"),
                mapping_path=output / "baseline_mapping.json",
                comparison_run_path=comparison_payload_path,
            )
            promotion_review = build_promotion_eligibility_review(
                variant_review=variant_review,
                mapping=mapping,
                dataset_contract_path=dataset_contract_path,
                dataset_contract_id=dataset_contract.get("artifact_id"),
                behavior_profile_path=behavior_profile_path,
                behavior_profile_id=profile.get("artifact_id"),
                mapping_path=output / "baseline_mapping.json",
                ahr_guidance_path=ahr_alignment_path,
                ahr_guidance_id=ahr_guidance.get("artifact_id"),
                comparison_run_path=comparison_payload_path,
            )

            self.assertIn("inventory_build_profile", execution_review)
            self.assertIn("zone_guidance", ahr_guidance)
            self.assertEqual(
                variant_review["recommendation"]["best_research_baseline"],
                "paulwei_public_archive_baseline",
            )
            self.assertEqual(variant_review["baseline_tiers"]["gate_baseline"], "current")
            self.assertEqual(variant_review["baseline_tiers"]["stretch_baseline"], "paulwei_public_archive_baseline")
            self.assertTrue(variant_review["baseline_tiers"]["current_and_legacy_same_tier"])
            self.assertTrue(promotion_review["promotion_readiness"])

    def test_strict_execution_learning_review_keeps_public_baseline_research_only_on_drawdown_blocker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_root = Path(tmp) / "paulwei"
            spot_root = Path(tmp) / "spot"
            macro_root = Path(tmp) / "macro"
            output = Path(tmp) / "results"
            output.mkdir(parents=True, exist_ok=True)
            _build_public_source(data_root)
            _write_spot_daily(spot_root / "klines" / "BTCUSDT" / "1d" / "data.parquet")
            _write_mvrv(macro_root / "btc_mvrv" / "data.parquet")

            dataset_contract = build_paulwei_dataset_contract(data_root=data_root)
            dataset_contract_path = output / "paulwei_dataset_contract.json"
            dataset_contract_path.write_text(json.dumps(dataset_contract), encoding="utf-8")
            profile, _ = build_behavior_profile(data_root=data_root, spot_root=spot_root)
            behavior_profile_path = output / "behavior_profile.json"
            behavior_profile_path.write_text(json.dumps(profile), encoding="utf-8")
            write_baseline_mapping(behavior_profile=profile, output_dir=output)
            mapping = json.loads((output / "baseline_mapping.json").read_text(encoding="utf-8"))
            alignment = build_ahr999_alignment(data_root=data_root, spot_root=spot_root, macro_root=macro_root)
            ahr_alignment_path = output / "ahr999_alignment.json"
            ahr_alignment_path.write_text(json.dumps(alignment), encoding="utf-8")

            execution_review = build_execution_research_review(
                dataset_contract=dataset_contract,
                behavior_profile=profile,
                mapping=mapping,
                dataset_contract_path=dataset_contract_path,
                behavior_profile_path=behavior_profile_path,
                mapping_path=output / "baseline_mapping.json",
            )
            ahr_guidance = build_ahr999_execution_guidance_review(
                alignment=alignment,
                dataset_contract_path=dataset_contract_path,
                dataset_contract_id=dataset_contract.get("artifact_id"),
                behavior_profile_path=behavior_profile_path,
                behavior_profile_id=profile.get("artifact_id"),
                mapping_path=output / "baseline_mapping.json",
                mapping_id=mapping.get("artifact_id"),
                ahr_alignment_path=ahr_alignment_path,
            )
            comparison_payload = {
                "windows": [{"label": "dev"}, {"label": "2023-08"}, {"label": "2024-11 DD"}],
                "window_reports": [
                    {"variant": "current", "window_label": "dev", "metrics": {"sharpe": 4.0, "max_drawdown": 0.10}},
                    {"variant": "current", "window_label": "2023-08", "metrics": {"sharpe": 4.2, "max_drawdown": 0.05}},
                    {
                        "variant": "current",
                        "window_label": "2024-11 DD",
                        "metrics": {"sharpe": 5.0, "max_drawdown": 0.03},
                    },
                    {
                        "variant": "paulwei_baseline",
                        "window_label": "dev",
                        "metrics": {"sharpe": 4.0, "max_drawdown": 0.10},
                    },
                    {
                        "variant": "paulwei_baseline",
                        "window_label": "2023-08",
                        "metrics": {"sharpe": 4.0, "max_drawdown": 0.05},
                    },
                    {
                        "variant": "paulwei_baseline",
                        "window_label": "2024-11 DD",
                        "metrics": {"sharpe": 4.8, "max_drawdown": 0.04},
                    },
                    {
                        "variant": "paulwei_public_archive_baseline",
                        "window_label": "dev",
                        "metrics": {"sharpe": 4.3, "max_drawdown": 0.145},
                    },
                    {
                        "variant": "paulwei_public_archive_baseline",
                        "window_label": "2023-08",
                        "metrics": {"sharpe": 4.4, "max_drawdown": 0.047},
                    },
                    {
                        "variant": "paulwei_public_archive_baseline",
                        "window_label": "2024-11 DD",
                        "metrics": {"sharpe": 6.5, "max_drawdown": 0.032},
                    },
                ],
            }
            comparison_payload_path = output / "variant_comparison_payload.json"
            comparison_payload_path.write_text(json.dumps(comparison_payload), encoding="utf-8")
            variant_review = build_variant_comparison_review(
                comparison_payload=comparison_payload,
                mapping=mapping,
                dataset_contract_path=dataset_contract_path,
                dataset_contract_id=dataset_contract.get("artifact_id"),
                behavior_profile_path=behavior_profile_path,
                behavior_profile_id=profile.get("artifact_id"),
                mapping_path=output / "baseline_mapping.json",
                comparison_run_path=comparison_payload_path,
            )
            promotion_review = build_promotion_eligibility_review(
                variant_review=variant_review,
                mapping=mapping,
                dataset_contract_path=dataset_contract_path,
                dataset_contract_id=dataset_contract.get("artifact_id"),
                behavior_profile_path=behavior_profile_path,
                behavior_profile_id=profile.get("artifact_id"),
                mapping_path=output / "baseline_mapping.json",
                ahr_guidance_path=ahr_alignment_path,
                ahr_guidance_id=ahr_guidance.get("artifact_id"),
                comparison_run_path=comparison_payload_path,
            )
            strict_review = build_strict_execution_learning_review(
                execution_review=execution_review,
                variant_review=variant_review,
                promotion_review=promotion_review,
                ahr_guidance_review=ahr_guidance,
                dataset_contract_path=dataset_contract_path,
                dataset_contract_id=dataset_contract.get("artifact_id"),
                behavior_profile_path=behavior_profile_path,
                behavior_profile_id=profile.get("artifact_id"),
                mapping_path=output / "baseline_mapping.json",
                mapping_id=mapping.get("artifact_id"),
                ahr_guidance_path=output / "ahr999_alignment.json",
                comparison_run_path=comparison_payload_path,
            )

            self.assertEqual(
                strict_review["public_baseline_pk_conclusion"]["primary_blocker"],
                "drawdown_outside_current_band",
            )
            self.assertTrue(strict_review["next_action_review"]["keep_research_only"])
            self.assertIn(
                "inventory_build_profile", {item["module"] for item in strict_review["strict_learning_modules"]}
            )
            self.assertFalse(strict_review["next_action_review"]["ready_for_authoritative_candidate_review"])
