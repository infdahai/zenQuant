from __future__ import annotations

import importlib
from pathlib import Path

import pytest


def _write_kernel_fixture(root: Path, *, fee_operator: str = "+") -> None:
    domain = root / "zenquant/domain"
    risk = root / "zenquant/risk"
    execution = root / "zenquant/execution"
    domain.mkdir(parents=True, exist_ok=True)
    risk.mkdir(parents=True, exist_ok=True)
    execution.mkdir(parents=True, exist_ok=True)
    (domain / "accounting.py").write_text(
        "def validate_execution_fee(fee):\n"
        "    if fee < 0:\n"
        "        raise ValueError('fee')\n\n"
        "def execution_report_identity(report):\n"
        "    return report.symbol, report.client_order_id, report.transaction_time_ns\n",
        encoding="utf-8",
    )
    (risk / "guard.py").write_text(
        "class RiskOverlay:\n"
        "    def _on_mark_price(self, payload):\n"
        "        self._last_price[payload.symbol] = payload.mark_price\n"
        "        self._revalue_equity()\n\n"
        "    def prime_indicator(self, pack):\n"
        "        self._last_price[pack.symbol] = pack.last_price\n"
        "        self._revalue_equity()\n\n"
        "    def _on_execution_report(self, payload):\n"
        "        return self.apply_execution_report(payload)\n\n"
        "    def apply_execution_report(self, payload):\n"
        f"        self._cash = self._cash - payload.notional {fee_operator} payload.fee\n"
        "        self._revalue_equity()\n\n"
        "    def apply_fee(self, fee):\n"
        "        self._cash -= fee\n"
        "        self._revalue_equity()\n\n"
        "    def _revalue_equity(self):\n"
        "        self._equity = self._cash + sum(self._positions.values())\n",
        encoding="utf-8",
    )
    (execution / "router.py").write_text(
        "class ExecutionKernel:\n"
        "    def _on_target(self, report):\n"
        "        self._apply_report_accounting(report)\n\n"
        "    def _apply_report_accounting(self, report):\n"
        "        self._risk_overlay.apply_execution_report(report)\n",
        encoding="utf-8",
    )


def test_accounting_kernel_identity_changes_with_unified_transition_semantics(tmp_path: Path) -> None:
    identity_module = importlib.import_module("zenquant.research.accounting_kernel_identity")
    _write_kernel_fixture(tmp_path, fee_operator="+")
    baseline = identity_module.build_accounting_kernel_identity(repository_root=tmp_path)
    _write_kernel_fixture(tmp_path, fee_operator="-")
    changed = identity_module.build_accounting_kernel_identity(repository_root=tmp_path)

    assert baseline["schema_version"] == "accounting-kernel-identity.v1"
    assert baseline["sha256"].startswith("sha256:")
    assert baseline["sha256"] != changed["sha256"]
    assert "zenquant/risk/guard.py::RiskOverlay.apply_execution_report" in {
        component["identity"] for component in baseline["components"]
    }


def test_accounting_kernel_identity_fails_if_unified_transition_is_missing(tmp_path: Path) -> None:
    identity_module = importlib.import_module("zenquant.research.accounting_kernel_identity")
    _write_kernel_fixture(tmp_path)
    guard = tmp_path / "zenquant/risk/guard.py"
    guard.write_text(
        guard.read_text(encoding="utf-8").replace("    def apply_execution_report", "    def removed_transition"),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=r"RiskOverlay\.apply_execution_report"):
        identity_module.build_accounting_kernel_identity(repository_root=tmp_path)


def test_current_accounting_kernel_identity_includes_runtime_transition() -> None:
    identity_module = importlib.import_module("zenquant.research.accounting_kernel_identity")

    identity = identity_module.build_accounting_kernel_identity()

    assert "zenquant/risk/guard.py::RiskOverlay.apply_execution_report" in {
        component["identity"] for component in identity["components"]
    }
