import importlib.util
import json
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from zenquant.historical import HistoricalBacktestConfig
from zenquant.historical.perturbation import PerturbationConfig
from zenquant.research import comparison


def _load_module(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


ROBUSTNESS_SCRIPT = _load_module(
    "behavior_candidate_robustness_check_module",
    "scripts/check_behavior_candidate_robustness.py",
)


def _report(
    variant: str,
    window_label: str,
    *,
    sharpe: float,
    max_drawdown: float,
    net_pnl: float,
    turnover_per_day: float = 100.0,
    fee_drag_pct: float = 0.02,
    ledger: bool = True,
) -> dict:
    payload = {
        "variant": variant,
        "window_label": window_label,
        "metrics": {
            "sharpe": sharpe,
            "max_drawdown": max_drawdown,
            "net_pnl": net_pnl,
            "turnover_per_day": turnover_per_day,
            "fee_drag_pct": fee_drag_pct,
            "profit_factor": 1.2,
        },
    }
    if ledger:
        payload["decision_attribution_ledger"] = {
            "micro_state_summary": {"squeeze_continuation": {"estimated_equity_pnl": net_pnl}},
            "top_reason_codes_by_micro_state": {},
        }
    return payload


def _seed_payload(
    seed: int,
    *,
    candidate_hostile_sharpe: float = 2.75,
    candidate_dev_drawdown: float = 0.12,
    candidate_hostile_drawdown: float = 0.07,
    candidate_turnover: float = 90.0,
    candidate_fee_drag: float = 0.021,
    candidate_hostile_pnl: float = 120.0,
    ledger: bool = True,
) -> dict:
    candidate = ROBUSTNESS_SCRIPT.DEFAULT_CANDIDATE
    return {
        "seed": seed,
        "comparison_payload": {
            "window_reports": [
                _report(
                    "current",
                    "dev",
                    sharpe=3.0,
                    max_drawdown=0.12,
                    net_pnl=100.0,
                    turnover_per_day=100.0,
                    fee_drag_pct=0.02,
                    ledger=ledger,
                ),
                _report("current", "2023-08", sharpe=2.8, max_drawdown=0.08, net_pnl=100.0, ledger=ledger),
                _report(
                    "paulwei_baseline",
                    "dev",
                    sharpe=2.9,
                    max_drawdown=0.13,
                    net_pnl=90.0,
                    turnover_per_day=105.0,
                    fee_drag_pct=0.021,
                    ledger=ledger,
                ),
                _report(
                    "paulwei_baseline",
                    "2023-08",
                    sharpe=2.7,
                    max_drawdown=0.09,
                    net_pnl=80.0,
                    ledger=ledger,
                ),
                _report(
                    candidate,
                    "dev",
                    sharpe=2.9,
                    max_drawdown=candidate_dev_drawdown,
                    net_pnl=110.0,
                    turnover_per_day=candidate_turnover,
                    fee_drag_pct=candidate_fee_drag,
                    ledger=ledger,
                ),
                _report(
                    candidate,
                    "2023-08",
                    sharpe=candidate_hostile_sharpe,
                    max_drawdown=candidate_hostile_drawdown,
                    net_pnl=candidate_hostile_pnl,
                    turnover_per_day=candidate_turnover,
                    fee_drag_pct=candidate_fee_drag,
                    ledger=ledger,
                ),
            ],
        },
    }


class BehaviorCandidateRobustnessTests(unittest.TestCase):
    def test_robustness_gate_allows_four_of_five_seed_passes(self) -> None:
        seed_payloads = [_seed_payload(seed) for seed in (42, 123, 456, 789)]
        seed_payloads.append(_seed_payload(1024, candidate_hostile_sharpe=2.6))

        verdict = ROBUSTNESS_SCRIPT.build_robustness_verdict(seed_payloads)

        self.assertTrue(verdict["robustness_gate_passed"])
        self.assertEqual(verdict["passing_seeds"], 4)
        self.assertEqual(verdict["terminal_recommendation"], "robustness_gate_passed_open_frozen_acceptance_review")
        self.assertFalse(verdict["full_training_allowed"])

    def test_robustness_gate_rejects_three_of_five_seed_passes(self) -> None:
        seed_payloads = [_seed_payload(seed) for seed in (42, 123, 456)]
        seed_payloads.extend(
            [
                _seed_payload(789, candidate_hostile_sharpe=2.6),
                _seed_payload(1024, candidate_hostile_sharpe=2.5),
            ]
        )

        verdict = ROBUSTNESS_SCRIPT.build_robustness_verdict(seed_payloads)

        self.assertFalse(verdict["robustness_gate_passed"])
        self.assertEqual(verdict["passing_seeds"], 3)
        self.assertIn("insufficient_seed_passes", verdict["rejection_reasons"])

    def test_seed_result_rejects_twenty_pct_drawdown_and_missing_attribution(self) -> None:
        seed_payload = _seed_payload(42, candidate_dev_drawdown=0.21, ledger=False)

        verdict = ROBUSTNESS_SCRIPT.build_robustness_verdict([seed_payload])
        seed_result = verdict["seed_results"][0]

        self.assertFalse(seed_result["passed"])
        self.assertIn("drawdown_exceeds_20pct_research_cap", seed_result["rejection_reasons"])
        self.assertIn("attribution_ledger_missing", seed_result["rejection_reasons"])
        self.assertIn("attribution_ledger_missing", verdict["rejection_reasons"])

    def test_seed_result_rejects_negative_hostile_window_increase(self) -> None:
        seed_payload = _seed_payload(42, candidate_hostile_sharpe=-0.1)

        verdict = ROBUSTNESS_SCRIPT.build_robustness_verdict([seed_payload])

        seed_result = verdict["seed_results"][0]
        self.assertFalse(seed_result["passed"])
        self.assertIn("negative_hostile_windows_increased", seed_result["rejection_reasons"])
        self.assertIn("worst_hostile_sharpe_regressed_beyond_tolerance", seed_result["rejection_reasons"])

    def test_comparison_cache_key_includes_perturbation(self) -> None:
        base = HistoricalBacktestConfig(root_dir=Path("data/raw/binance"), symbol="BTCUSDT")
        key_a = comparison._comparison_context_key(replace(base, perturbation=PerturbationConfig(seed=42)))
        key_b = comparison._comparison_context_key(replace(base, perturbation=PerturbationConfig(seed=123)))

        self.assertNotEqual(key_a, key_b)

    def test_main_writes_robustness_output_without_training_flags(self) -> None:
        fake_payload = _seed_payload(42)["comparison_payload"]
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "results"
            argv = [
                "check_behavior_candidate_robustness.py",
                "--output-dir",
                str(out),
            ]
            with (
                patch.object(sys, "argv", argv),
                patch.object(ROBUSTNESS_SCRIPT, "compare_variants", return_value=fake_payload),
            ):
                ROBUSTNESS_SCRIPT.main()

            result_path = out / ROBUSTNESS_SCRIPT.DEFAULT_CANDIDATE / "robustness.json"
            self.assertTrue(result_path.exists())
            payload = json.loads(result_path.read_text(encoding="utf-8"))

        self.assertFalse(payload["optuna_search_executed"])
        self.assertFalse(payload["full_training_executed"])
        self.assertIn("seed_results", payload)


if __name__ == "__main__":
    unittest.main()
