from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from zenquant.research.signal_combination import (
    build_markdown_audit,
    build_signal_candidate_inventory,
    build_signal_combination_audit,
    infer_signal_role,
)


class SignalCombinationAuditTests(unittest.TestCase):
    def test_infer_signal_role_uses_expected_grouping(self) -> None:
        self.assertEqual(infer_signal_role("multi_momentum_signal"), "direction")
        self.assertEqual(infer_signal_role("options_vol_signal"), "state")
        self.assertEqual(infer_signal_role("etf_flow_state_signal"), "state")
        self.assertEqual(infer_signal_role("liquidity_signal"), "execution_modifier")

    def test_audit_normalizes_direction_weights_and_flags_redundancy(self) -> None:
        forward_returns = [0.01, 0.02, 0.015, -0.01, -0.02, -0.015, 0.02, 0.01, -0.01, 0.015] * 10
        signal_series = {
            "multi_momentum_signal": list(forward_returns),
            "cvd_signal": [value * 0.95 for value in forward_returns],
            "options_vol_signal": [0.2 if idx % 2 == 0 else -0.2 for idx, _ in enumerate(forward_returns)],
            "liquidity_signal": [0.1 if idx % 3 == 0 else 0.0 for idx, _ in enumerate(forward_returns)],
        }
        audit = build_signal_combination_audit(
            symbol="BTCUSDT",
            profile_path=Path("config/profiles/default.toml"),
            signal_series=signal_series,
            baseline_weights={
                "multi_momentum_signal": 0.45,
                "cvd_signal": 0.25,
                "options_vol_signal": 0.15,
                "liquidity_signal": 0.20,
            },
            forward_returns=forward_returns,
            corr_threshold=0.7,
            rolling_window=20,
        )

        direction_weights = [
            entry.normalized_final_research_weight for entry in audit.signals if entry.role == "direction"
        ]
        self.assertAlmostEqual(sum(direction_weights), 1.0, places=3)
        self.assertTrue(
            any(pair["left"] == "cvd_signal" or pair["right"] == "cvd_signal" for pair in audit.redundant_pairs)
        )
        self.assertGreaterEqual(audit.summary["same_role_redundant_pair_count"], 1)
        options_entry = next(entry for entry in audit.signals if entry.name == "options_vol_signal")
        self.assertEqual(options_entry.recommended_weight_adjustment, "research_only")
        self.assertFalse(options_entry.allowed_in_direction_search)
        self.assertTrue(options_entry.allowed_in_state_layer)
        momentum_entry = next(entry for entry in audit.signals if entry.name == "multi_momentum_signal")
        self.assertNotEqual(momentum_entry.recommended_weight_adjustment, "disable")
        self.assertTrue(momentum_entry.allowed_in_direction_search)
        self.assertEqual(momentum_entry.search_bound_policy, "narrow")

    def test_markdown_audit_contains_summary(self) -> None:
        forward_returns = [0.01, -0.01, 0.02, -0.02, 0.01] * 20
        audit = build_signal_combination_audit(
            symbol="BTCUSDT",
            profile_path=Path("config/profiles/default.toml"),
            signal_series={"multi_momentum_signal": list(forward_returns)},
            baseline_weights={"multi_momentum_signal": 0.45},
            forward_returns=forward_returns,
            rolling_window=20,
        )
        markdown = build_markdown_audit(audit)
        self.assertIn("# Signal Independence Audit", markdown)
        self.assertIn("multi_momentum_signal", markdown)
        self.assertIn("direction_layer_clean", markdown)

    def test_cross_role_overlap_is_reported_separately(self) -> None:
        forward_returns = [0.01, -0.01, 0.02, -0.02, 0.01] * 20
        shared = list(forward_returns)
        audit = build_signal_combination_audit(
            symbol="BTCUSDT",
            profile_path=Path("config/profiles/default.toml"),
            signal_series={
                "crowding_signal": shared,
                "funding_alpha_signal": shared,
            },
            baseline_weights={
                "crowding_signal": 0.10,
                "funding_alpha_signal": 0.10,
            },
            forward_returns=forward_returns,
            corr_threshold=0.7,
            rolling_window=20,
        )
        self.assertEqual(audit.summary["same_role_redundant_pair_count"], 0)
        self.assertEqual(audit.summary["cross_role_overlap_pair_count"], 1)
        self.assertEqual(audit.cross_role_overlap_pairs[0]["left_role"], "direction")
        self.assertEqual(audit.cross_role_overlap_pairs[0]["right_role"], "state")

    def test_candidate_inventory_lists_disabled_candidates(self) -> None:
        inventory = build_signal_candidate_inventory(
            symbol="BTCUSDT",
            profile_path=Path("config/profiles/default.toml"),
            plugin_root=Path("zenquant/plugins"),
        )
        names = {item.name: item for item in inventory.candidates}
        self.assertIn("vacuum_signal", names)
        self.assertFalse(names["vacuum_signal"].enabled_in_default)
        self.assertEqual(names["cross_asset_flow_signal"].audit_status, "research_candidate")


class SignalCombinationSerializationTests(unittest.TestCase):
    def test_json_payload_is_serializable(self) -> None:
        forward_returns = [0.01, -0.01, 0.02, -0.02, 0.01] * 20
        audit = build_signal_combination_audit(
            symbol="BTCUSDT",
            profile_path=Path("config/profiles/default.toml"),
            signal_series={"multi_momentum_signal": list(forward_returns)},
            baseline_weights={"multi_momentum_signal": 0.45},
            forward_returns=forward_returns,
            rolling_window=20,
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "audit.json"
            path.write_text(json.dumps(audit.summary, indent=2), encoding="utf-8")
            payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertIn("active_signal_count", payload)
