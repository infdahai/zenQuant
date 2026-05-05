import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from zenquant.research.deribit_large_option_independence import (
    build_deribit_large_option_flow_independence_audit,
    write_deribit_large_option_flow_independence_audit,
)


class DeribitLargeOptionIndependenceTests(unittest.TestCase):
    def test_independence_allows_low_baseline_correlation(self) -> None:
        features = _features(correlated=False, rows=600)
        payload = build_deribit_large_option_flow_independence_audit(
            admission_payload=_admission_payload(research_ready=True),
            features_path=_write_features(features),
        )

        self.assertEqual(payload["role"], "state_context_pressure_diagnostic")
        self.assertFalse(payload["allowed_in_state_features"])
        self.assertTrue(payload["allowed_in_pressure_context"])
        self.assertTrue(payload["allowed_in_candidate_explanation"])
        self.assertFalse(payload["direction_vote_allowed"])
        self.assertFalse(payload["intent_aggregation_allowed"])
        self.assertFalse(payload["risk_overlay_wiring_allowed"])
        self.assertIsNone(payload["blocked_by"])
        self.assertEqual(payload["usable_candidate_count"], 1)

    def test_independence_blocks_high_baseline_correlation(self) -> None:
        features = _features(correlated=True, rows=600)
        payload = build_deribit_large_option_flow_independence_audit(
            admission_payload=_admission_payload(research_ready=True),
            features_path=_write_features(features),
        )

        self.assertFalse(payload["allowed_in_state_features"])
        self.assertFalse(payload["allowed_in_pressure_context"])
        self.assertEqual(payload["blocked_by"], "high_baseline_state_correlation")

    def test_write_independence_audit_uses_loader_compatible_filename(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            features_path = root / "features.parquet"
            _features(correlated=False, rows=600).to_parquet(features_path, index=False)
            admission_path = root / "admission.json"
            admission = _admission_payload(research_ready=True)
            admission["features_path"] = str(features_path)
            admission_path.write_text(json.dumps(admission), encoding="utf-8")

            result = write_deribit_large_option_flow_independence_audit(
                admission_path=admission_path,
                output_dir=root,
            )
            written = json.loads((root / "deribit_large_option_flow_independence_audit.json").read_text())

        self.assertFalse(result["allowed_in_state_features"])
        self.assertTrue(result["allowed_in_pressure_context"])
        self.assertEqual(written["source_name"], "deribit_large_option_flow")


def _admission_payload(*, research_ready: bool) -> dict:
    return {
        "source_name": "deribit_large_option_flow",
        "symbol": "BTCUSDT",
        "research_ready": research_ready,
        "features_path": "features.parquet",
        "top_feature_candidates": [
            {"name": "large_put_contracts_24h_z", "target": "forward_abs_return_1d"},
            {"name": "large_put_contracts_24h_z", "target": "forward_abs_return_4h"},
        ],
    }


def _features(*, correlated: bool, rows: int) -> pd.DataFrame:
    baseline = pd.Series(range(rows), dtype=float)
    factor = baseline if correlated else pd.Series([(idx * 37) % rows for idx in range(rows)], dtype=float)
    return pd.DataFrame(
        {
            "bar_close_ts": pd.date_range("2023-01-01", periods=rows, freq="4h", tz="UTC"),
            "large_put_contracts_24h_z": factor,
            "price_return_24h_z": baseline,
            "realized_vol_24h_z": baseline[::-1].reset_index(drop=True),
        }
    )


def _write_features(frame: pd.DataFrame) -> Path:
    path = Path(tempfile.mkdtemp()) / "features.parquet"
    frame.to_parquet(path, index=False)
    return path


if __name__ == "__main__":
    unittest.main()
