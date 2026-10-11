"""Gate F must propagate evaluator failures without replaying historical data."""

from __future__ import annotations

import runpy
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import zenquant.historical as historical
import zenquant.research.comparison as comparison
from zenquant.app import check
from zenquant.research.comparison import WindowSpec


@pytest.mark.parametrize(
    ("sharpes", "expected_code"),
    [
        pytest.param([2.0, -1.0, 2.0, 5.0], 1, id="one-overfit-window-blocks-even-with-positive-average"),
        pytest.param([2.0, 1.5, 2.0, 3.0], 0, id="healthy-windows-pass"),
        pytest.param([2.0, 0.1], 0, id="degraded-without-overfit-preserves-existing-threshold"),
        pytest.param([], 1, id="empty-windows-fail-closed"),
    ],
)
def test_gate_f_propagates_walk_forward_cli_exit(
    monkeypatch: pytest.MonkeyPatch, sharpes: list[float], expected_code: int
) -> None:
    windows = [
        WindowSpec(label=f"window_{i}", start_ns=1_704_067_200_000_000_000, end_ns=1_719_792_000_000_000_000)
        for i in range(len(sharpes) // 2)
    ]
    monkeypatch.setattr(comparison, "default_walk_forward_windows", Mock(return_value=windows))
    # Construction loads datasets, so replace the entire backtest boundary.
    # The evaluator's window verdicts, CLI, and harness wrapper remain real.
    backtests = [
        SimpleNamespace(run=lambda sharpe=sharpe: SimpleNamespace(metrics=SimpleNamespace(sharpe=sharpe, net_pnl=0.0)))
        for sharpe in sharpes
    ]
    monkeypatch.setattr(historical, "HistoricalBacktest", Mock(side_effect=backtests))
    exit_codes: list[int] = []

    def run_cli(cmd: list[str], *, cwd: str) -> subprocess.CompletedProcess:
        with monkeypatch.context() as cli_patch:
            cli_patch.setattr(sys, "argv", cmd[1:])
            cli_patch.setattr(sys, "path", sys.path.copy())
            try:
                runpy.run_path(str(Path(cwd) / cmd[1]), run_name="__main__")
            except SystemExit as exc:
                code = exc.code
            else:
                code = 0
        exit_codes.append(code)
        return subprocess.CompletedProcess(cmd, code)

    # Run the actual script entrypoint in-process so the test stays offline.
    monkeypatch.setattr(check.subprocess, "run", run_cli)

    passed = check.gate_f()

    assert exit_codes == [expected_code]
    assert passed is (expected_code == 0)
