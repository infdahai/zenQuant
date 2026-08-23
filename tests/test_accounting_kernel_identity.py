from __future__ import annotations

import importlib
import json
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
        "    def __init__(self, bus, starting_equity):\n"
        "        self._bus = bus\n"
        "        self._cash = starting_equity\n"
        "        self._bus.subscribe('exec.order.report', self._on_execution_report)\n\n"
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
        "    def __init__(self, bus):\n"
        "        self._bus = bus\n"
        "        self._bus.subscribe('intent.target_position.final.>', self._on_target)\n\n"
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
    assert "zenquant/risk/guard.py::<module>" in {component["identity"] for component in baseline["components"]}


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


def test_accounting_kernel_identity_changes_with_risk_initialization_semantics(tmp_path: Path) -> None:
    identity_module = importlib.import_module("zenquant.research.accounting_kernel_identity")
    _write_kernel_fixture(tmp_path)
    baseline = identity_module.build_accounting_kernel_identity(repository_root=tmp_path)
    guard = tmp_path / "zenquant/risk/guard.py"
    guard.write_text(
        guard.read_text(encoding="utf-8").replace(
            "self._cash = starting_equity",
            "self._cash = starting_equity * 2",
        ),
        encoding="utf-8",
    )

    changed = identity_module.build_accounting_kernel_identity(repository_root=tmp_path)

    assert baseline["sha256"] != changed["sha256"]


def test_accounting_kernel_identity_changes_with_router_subscription_semantics(tmp_path: Path) -> None:
    identity_module = importlib.import_module("zenquant.research.accounting_kernel_identity")
    _write_kernel_fixture(tmp_path)
    baseline = identity_module.build_accounting_kernel_identity(repository_root=tmp_path)
    router = tmp_path / "zenquant/execution/router.py"
    router.write_text(
        router.read_text(encoding="utf-8").replace(
            "intent.target_position.final.>",
            "intent.target_position.shadow.>",
        ),
        encoding="utf-8",
    )

    changed = identity_module.build_accounting_kernel_identity(repository_root=tmp_path)

    assert baseline["sha256"] != changed["sha256"]


def test_accounting_kernel_identity_ignores_formatting_and_comments(tmp_path: Path) -> None:
    identity_module = importlib.import_module("zenquant.research.accounting_kernel_identity")
    _write_kernel_fixture(tmp_path)
    baseline = identity_module.build_accounting_kernel_identity(repository_root=tmp_path)

    for relative_path in (
        Path("zenquant/domain/accounting.py"),
        Path("zenquant/risk/guard.py"),
        Path("zenquant/execution/router.py"),
    ):
        source_path = tmp_path / relative_path
        source = source_path.read_text(encoding="utf-8")
        source_path.write_text(
            "# Formatting and comments are not runtime semantics.\n\n"
            + source.replace("fee < 0", "fee<0").replace(
                "self._cash = starting_equity",
                "self._cash=starting_equity",
            ),
            encoding="utf-8",
        )

    formatted = identity_module.build_accounting_kernel_identity(repository_root=tmp_path)

    assert baseline == formatted


def test_current_accounting_kernel_identity_covers_complete_runtime_modules() -> None:
    identity_module = importlib.import_module("zenquant.research.accounting_kernel_identity")

    identity = identity_module.build_accounting_kernel_identity()

    assert {component["identity"] for component in identity["components"]} == {
        "zenquant/domain/accounting.py::<module>",
        "zenquant/risk/guard.py::<module>",
        "zenquant/execution/router.py::<module>",
    }


def test_current_accounting_kernel_identity_matches_frozen_rerun_contract() -> None:
    identity_module = importlib.import_module("zenquant.research.accounting_kernel_identity")
    contract = json.loads(Path("config/research/legacy_accounting_rerun_v2.json").read_text(encoding="utf-8"))

    assert contract["accounting_kernel_sha256"] == identity_module.build_accounting_kernel_identity()["sha256"]
