import json
import tempfile
import unittest
from pathlib import Path

from zenquant.research.deribit_large_option_admission import (
    build_deribit_large_option_flow_admission_review,
    write_deribit_large_option_flow_admission_review,
)


class DeribitLargeOptionAdmissionTests(unittest.TestCase):
    def test_admission_review_promotes_only_to_research_candidate(self) -> None:
        payload = build_deribit_large_option_flow_admission_review(
            audit_payload=_audit_payload(train_observations=700, admissible=2, blocked_by=None),
            audit_path=Path("audit.json"),
        )

        self.assertEqual(payload["tier_decision"], "tier2_research_candidate")
        self.assertTrue(payload["research_ready"])
        self.assertTrue(payload["admit_as_secondary"])
        self.assertFalse(payload["admit_as_primary"])
        self.assertEqual(payload["role"], "state_context_pressure_diagnostic")
        self.assertTrue(payload["allowed_in_training_observation"])
        self.assertTrue(payload["allowed_in_candidate_explanation"])
        self.assertFalse(payload["direction_vote_allowed"])
        self.assertFalse(payload["intent_aggregation_allowed"])
        self.assertFalse(payload["position_sizing_allowed"])
        self.assertFalse(payload["strategy_wiring_allowed"])
        self.assertFalse(payload["risk_overlay_wiring_allowed"])
        self.assertEqual(payload["next_action"], "freeze_research_feature_set_for_pressure_context_replay")
        self.assertEqual(payload["top_feature_candidates"][0]["name"], "large_put_contracts_24h_z")

    def test_admission_review_blocks_when_train_observations_are_thin(self) -> None:
        payload = build_deribit_large_option_flow_admission_review(
            audit_payload=_audit_payload(train_observations=100, admissible=2, blocked_by=None),
            audit_path=Path("audit.json"),
        )

        self.assertFalse(payload["research_ready"])
        self.assertEqual(payload["blocked_by"], "insufficient_train_observations")
        self.assertEqual(payload["tier_decision"], "tier1_observation_only")

    def test_write_review_uses_loader_compatible_filename(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            audit_path = root / "audit.json"
            audit_path.write_text(json.dumps(_audit_payload(train_observations=700, admissible=1)), encoding="utf-8")

            result = write_deribit_large_option_flow_admission_review(
                audit_path=audit_path,
                output_dir=root,
            )
            written = json.loads((root / "deribit_large_option_flow_admission_review.json").read_text(encoding="utf-8"))
            md_exists = (root / "deribit_large_option_flow_admission_review.md").exists()

        self.assertEqual(result["tier_decision"], "tier2_research_candidate")
        self.assertEqual(written["source_name"], "deribit_large_option_flow")
        self.assertTrue(md_exists)


def _audit_payload(
    *,
    train_observations: int,
    admissible: int,
    blocked_by: str | None = None,
) -> dict:
    return {
        "features_path": "features.parquet",
        "feature_coverage": {"feature_bar_count": train_observations},
        "summary": {
            "source_row_count": 10_000,
            "selected_large_row_count": 600,
            "train_aligned_observations": train_observations,
            "admissible_factor_target_count": admissible,
            "blocked_by": blocked_by,
        },
        "factors": [
            {
                "name": "large_put_contracts_24h_z",
                "target": "forward_abs_return_1d",
                "observations": train_observations,
                "rolling_rank_ic_mean": 0.31,
                "ablation_delta": 0.22,
                "hit_rate": 0.71,
                "signal_sign": 1.0,
                "max_abs_factor_correlation": 0.42,
                "blocked_by": None,
            },
            {
                "name": "large_contracts_24h_z",
                "target": "forward_abs_return_1d",
                "observations": train_observations,
                "rolling_rank_ic_mean": 0.5,
                "ablation_delta": 0.1,
                "hit_rate": 0.75,
                "signal_sign": 1.0,
                "max_abs_factor_correlation": 0.9,
                "blocked_by": "high_factor_correlation",
            },
        ],
    }


if __name__ == "__main__":
    unittest.main()
