import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from zenquant.research.attribution import build_attribution_precheck_verdict


def _load_module(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


CHOP_SCRIPT = _load_module("chop_neutral_edge_analysis_module", "scripts/analyze_chop_neutral_edge.py")


def _run(
    variant: str,
    *,
    turnover_per_day: float = 100.0,
    chop_pnl: float = 100.0,
    squeeze_pnl: float = -50.0,
    ledger: bool = True,
    changed_path: bool = False,
) -> dict:
    payload = {
        "variant": variant,
        "window_label": "2023-08",
        "metrics": {
            "sharpe": 1.0,
            "max_drawdown": 0.05,
            "net_pnl": chop_pnl + squeeze_pnl,
            "turnover_per_day": turnover_per_day,
            "fee_drag_pct": 0.02,
            "profit_factor": 1.1,
        },
    }
    if not ledger:
        return payload
    target_action = "entry" if not changed_path else "hold"
    execution_actions = ["add"] if not changed_path else []
    payload["decision_attribution_ledger"] = {
        "micro_state_summary": {
            "chop_neutral": {
                "bars": 1,
                "estimated_equity_pnl": chop_pnl,
                "fees": 1.0,
                "turnover_notional": 1000.0,
                "target_actions": {target_action: 1},
                "execution_actions": {action: 1 for action in execution_actions},
            },
            "squeeze_continuation": {
                "bars": 1,
                "estimated_equity_pnl": squeeze_pnl,
                "fees": 1.0,
                "turnover_notional": 500.0,
                "target_actions": {"flip": 1},
                "execution_actions": {"flip": 1},
            },
        },
        "top_reason_codes_by_micro_state": {
            "chop_neutral": {"PLAYBOOK_RANGE_LIGHT": 1},
        },
        "decision_rows": [
            {
                "cutoff_time_ns": 1,
                "micro_state": "chop_neutral",
                "target_action": target_action,
                "target_qty": 1.0,
                "estimated_equity_pnl": chop_pnl,
                "fees": 1.0,
                "turnover_notional": 1000.0,
                "execution_actions": execution_actions,
                "target_reason_codes": "PLAYBOOK_RANGE_LIGHT",
            }
        ],
    }
    return payload


def _payload(candidate: dict) -> dict:
    return {
        "window_reports": [
            _run("current", turnover_per_day=100.0, chop_pnl=100.0, squeeze_pnl=-50.0),
            candidate,
        ]
    }


class ChopNeutralEdgeTests(unittest.TestCase):
    def test_precheck_rejects_profitable_chop_path_change(self) -> None:
        verdict = build_attribution_precheck_verdict(
            _payload(_run("candidate", turnover_per_day=90.0, chop_pnl=100.0, squeeze_pnl=-50.0, changed_path=True)),
            candidate_name="candidate",
        )

        self.assertFalse(verdict["candidate_existence_allowed"])
        self.assertIn("profitable_chop_neutral_path_changed", verdict["rejection_reasons"])

    def test_precheck_rejects_turnover_above_current(self) -> None:
        verdict = build_attribution_precheck_verdict(
            _payload(_run("candidate", turnover_per_day=120.0, chop_pnl=100.0, squeeze_pnl=-50.0)),
            candidate_name="candidate",
        )

        self.assertFalse(verdict["candidate_existence_allowed"])
        self.assertIn("turnover_not_below_current", verdict["rejection_reasons"])

    def test_precheck_rejects_squeeze_improvement_that_damages_chop(self) -> None:
        verdict = build_attribution_precheck_verdict(
            _payload(_run("candidate", turnover_per_day=90.0, chop_pnl=80.0, squeeze_pnl=-10.0)),
            candidate_name="candidate",
        )

        self.assertFalse(verdict["candidate_existence_allowed"])
        self.assertIn("chop_neutral_pnl_damaged", verdict["rejection_reasons"])
        self.assertIn("squeeze_improved_but_chop_damaged", verdict["rejection_reasons"])

    def test_precheck_fails_closed_when_attribution_is_missing(self) -> None:
        verdict = build_attribution_precheck_verdict(
            _payload(_run("candidate", turnover_per_day=90.0, chop_pnl=100.0, squeeze_pnl=-50.0, ledger=False)),
            candidate_name="candidate",
        )

        self.assertFalse(verdict["candidate_existence_allowed"])
        self.assertIn("attribution_ledger_missing", verdict["rejection_reasons"])

    def test_precheck_allows_candidate_that_preserves_chop_and_turnover(self) -> None:
        verdict = build_attribution_precheck_verdict(
            _payload(_run("candidate", turnover_per_day=90.0, chop_pnl=110.0, squeeze_pnl=-40.0)),
            candidate_name="candidate",
        )

        self.assertTrue(verdict["candidate_existence_allowed"])
        self.assertEqual(verdict["terminal_recommendation"], "open_candidate_existence_gate")

    def test_cli_writes_chop_neutral_edge_outputs_without_training_flags(self) -> None:
        artifact = {"comparison_payload": _payload(_run("candidate", turnover_per_day=120.0))}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path = root / "comparison_seed_123.json"
            input_path.write_text(json.dumps(artifact), encoding="utf-8")
            output_dir = root / "out"
            argv = [
                "analyze_chop_neutral_edge.py",
                "--input",
                str(input_path),
                "--candidate",
                "candidate",
                "--output-dir",
                str(output_dir),
            ]
            with patch.object(sys, "argv", argv):
                CHOP_SCRIPT.main()

            result_path = output_dir / "chop_neutral_edge.json"
            self.assertTrue(result_path.exists())
            payload = json.loads(result_path.read_text(encoding="utf-8"))

        self.assertFalse(payload["optuna_search_executed"])
        self.assertFalse(payload["full_training_allowed"])
        self.assertFalse(payload["candidate_existence_allowed"])


if __name__ == "__main__":
    unittest.main()
