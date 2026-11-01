"""Admission and restart refusal for the Python simulator-only execution path."""

import subprocess
import sys
from dataclasses import FrozenInstanceError, asdict, replace
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from zenquant.app.server import ZenQuantWebRuntime
from zenquant.domain import ExecutionOrder, TargetPosition, Timeframe
from zenquant.execution import ExecutionGateway, ExecutionKernel, ExecutionKernelConfig
from zenquant.execution.session import SimulationSession
from zenquant.infra import InMemoryJetStream, StreamConfig
from zenquant.risk import RiskOverlay
from zenquant.strategy import ARCStrategyEngine


def order(identity="attempt-1"):
    return ExecutionOrder(identity, "BTCUSDT_PERP", 1, 1.0, False, 8.0)


def target(quantity=1.0, cutoff=2):
    return TargetPosition(
        intent_id=f"target-{cutoff}",
        portfolio="arc",
        symbol="BTCUSDT_PERP",
        timeframe=Timeframe.TF_4H,
        cutoff_time_ns=cutoff,
        target_qty=quantity,
        target_leverage=abs(quantity),
        reduce_only=False,
        regime="TREND",
        regime_confidence=0.9,
        reason_codes=["TEST"],
    )


def test_external_reports_rejected_before_retention_or_any_consumer():
    bus = InMemoryJetStream()
    bus.add_stream(StreamConfig("execution", ["exec.>"]))
    risk = RiskOverlay(bus)
    strategy = ARCStrategyEngine(bus)
    persisted = []
    bus.subscribe("exec.order.report", lambda _, report: persisted.append(report))
    unsolicited = ExecutionGateway().submit_order(order(), mark_price=100, now_ns=1)
    with pytest.raises(ValueError, match="external execution reports"):
        bus.publish("exec.order.report", unsolicited)
    assert bus.get_stream_messages("execution") == []
    assert risk._actual_positions == {}
    assert strategy._positions == {}
    assert persisted == []


def test_duplicate_late_and_cumulative_reports_cannot_change_shared_positions():
    bus = InMemoryJetStream()
    risk = RiskOverlay(bus)
    strategy = ARCStrategyEngine(bus)
    kernel = ExecutionKernel(bus, ExecutionGateway(), risk, ExecutionKernelConfig(pyramid_enabled=False))
    reports = []
    bus.subscribe("exec.order.report", lambda _, report: reports.append(report))
    kernel._last_price["BTCUSDT_PERP"] = 100.0
    bus.publish("intent.target_position.final.BTCUSDT_PERP", target())
    before = (
        dict(kernel.positions),
        dict(risk._actual_positions),
        risk._cash,
        asdict(strategy._positions[("BTCUSDT_PERP", Timeframe.TF_4H)]),
    )
    for report in (
        reports[0],
        replace(reports[0]),
        replace(reports[0], filled_qty=0.2, transaction_time_ns=3),
        replace(reports[0], filled_qty=1.2, fee=2.0, transaction_time_ns=4),
        replace(reports[0], client_order_id="unknown", transaction_time_ns=0),
    ):
        with pytest.raises(ValueError, match="external execution reports"):
            bus.publish("exec.order.report", report)
        assert (
            dict(kernel.positions),
            dict(risk._actual_positions),
            risk._cash,
            asdict(strategy._positions[("BTCUSDT_PERP", Timeframe.TF_4H)]),
        ) == before
    assert len(reports) == 1
    with pytest.raises(FrozenInstanceError):
        reports[0].filled_qty = 999.0
    # Rejected external input does not turn a simulated position into an
    # uncertain exchange position. An ordinary approved reduction still works.
    bus.publish("intent.target_position.final.BTCUSDT_PERP", target(0.0, 3))
    assert kernel.positions["BTCUSDT_PERP"] == risk._actual_positions["BTCUSDT_PERP"] == 0.0


@pytest.mark.parametrize(
    "changes",
    [
        {"client_order_id": "wrong"},
        {"symbol": "OTHER"},
        {"side": -1},
        {"filled_qty": float("nan")},
        {"filled_qty": -1.0},
        {"filled_qty": 2.0},
        {"avg_price": 0.0},
        {"fee": float("inf")},
        {"fee": -1.0},
        {"status": "UNKNOWN"},
        {"status": "REJECTED"},
    ],
)
def test_invalid_response_is_quarantined_before_fanout(changes):
    bus = InMemoryJetStream()
    risk = RiskOverlay(bus)
    gateway = ExecutionGateway()
    original = gateway.submit_order(order(), mark_price=100, now_ns=1)
    gateway.submit_order = Mock(return_value=replace(original, **changes))
    session = SimulationSession(bus, gateway)
    reports = []
    bus.subscribe("exec.order.report", lambda _, report: reports.append(report))
    with pytest.raises(ValueError, match="invalid or mismatched"):
        session.submit(order(), mark_price=100, now_ns=1)
    assert session.blocked
    assert risk._actual_positions == {}
    assert reports == []
    with pytest.raises(RuntimeError, match="blocked"):
        session.submit(order("attempt-2"), mark_price=100, now_ns=2)
    assert gateway.submit_order.call_count == 1


def test_only_builtin_simulators_can_submit():
    class ExternalGateway(ExecutionGateway):
        submit_order = Mock(side_effect=AssertionError("must not call external adapter"))

    gateway = ExternalGateway()
    with pytest.raises(TypeError, match="built-in simulation"):
        SimulationSession(InMemoryJetStream(), gateway)
    gateway.submit_order.assert_not_called()


def test_order_identity_is_unique_and_cannot_be_reapplied():
    bus = InMemoryJetStream()
    risk = RiskOverlay(bus)
    session = SimulationSession(bus, ExecutionGateway())
    session.submit(order(), mark_price=100, now_ns=1)
    with pytest.raises(ValueError, match="identity must be unique"):
        session.submit(order(), mark_price=100, now_ns=2)
    assert risk._actual_positions == {"BTCUSDT_PERP": 1.0}
    assert session.blocked
    with pytest.raises(RuntimeError, match="already claimed"):
        SimulationSession(bus, ExecutionGateway())


@pytest.mark.parametrize("outcome", ["filled", "timeout", "fanout_error"])
def test_used_session_refuses_restart_even_after_clean_fill(tmp_path, outcome):
    path = tmp_path / "session.lock"
    bus = InMemoryJetStream()
    gateway = ExecutionGateway()
    session = SimulationSession(bus, gateway, path)
    if outcome == "timeout":
        gateway.submit_order = Mock(side_effect=TimeoutError("unknown outcome"))
    if outcome == "fanout_error":
        bus.subscribe("exec.order.report", Mock(side_effect=RuntimeError("consumer failed")))
    if outcome == "filled":
        session.submit(order(), mark_price=100, now_ns=1)
    else:
        with pytest.raises((TimeoutError, RuntimeError)):
            session.submit(order(), mark_price=100, now_ns=1)
        assert session.blocked
    assert path.read_text().startswith("zenquant-simulation-v1")
    with pytest.raises(RuntimeError, match="cannot resume"):
        SimulationSession(InMemoryJetStream(), ExecutionGateway(), path)
    # A different explicitly selected path is a fresh empty simulated account.
    SimulationSession(InMemoryJetStream(), ExecutionGateway(), tmp_path / "new-account.lock")


def test_session_marker_survives_process_exit_during_submission(tmp_path):
    path = tmp_path / "crashed.lock"
    script = """
import os, sys
from pathlib import Path
from zenquant.domain import ExecutionOrder
from zenquant.execution import ExecutionGateway
from zenquant.execution.session import SimulationSession
from zenquant.infra import InMemoryJetStream
gateway = ExecutionGateway()
gateway.submit_order = lambda *a, **kw: os._exit(23)
session = SimulationSession(InMemoryJetStream(), gateway, Path(sys.argv[1]))
session.submit(ExecutionOrder('attempt', 'BTCUSDT_PERP', 1, 1.0, False, 8.0), mark_price=100)
"""
    result = subprocess.run([sys.executable, "-c", script, str(path)], cwd=Path(__file__).resolve().parents[1])
    assert result.returncode == 23
    with pytest.raises(RuntimeError, match="cannot resume"):
        SimulationSession(InMemoryJetStream(), ExecutionGateway(), path)


def test_marker_write_failure_and_competing_sessions_prevent_submission(tmp_path):
    gateway = ExecutionGateway()
    gateway.submit_order = Mock()
    path = tmp_path / "used.lock"
    first = SimulationSession(InMemoryJetStream(), gateway, path)
    competing = SimulationSession(InMemoryJetStream(), gateway, path)
    with (
        patch("zenquant.execution.session.os.fsync", side_effect=OSError("disk failure")),
        pytest.raises(OSError, match="disk failure"),
    ):
        first.submit(order(), mark_price=100)
    with pytest.raises(FileExistsError):
        competing.submit(order(), mark_price=100)
    gateway.submit_order.assert_not_called()
    assert first.blocked and competing.blocked
    with pytest.raises(RuntimeError, match="cannot resume"):
        SimulationSession(InMemoryJetStream(), ExecutionGateway(), path)


def test_live_runtime_uses_durable_refusal_boundary(tmp_path):
    path = tmp_path / "live.lock"
    runtime = ZenQuantWebRuntime(runtime_mode="live", paper_session_path=path)
    runtime._initialize_trading_chain()
    runtime.execution_kernel._last_price["BTCUSDT_PERP"] = 100.0
    runtime.bus.publish("intent.target_position.final.BTCUSDT_PERP", target())
    restarted = ZenQuantWebRuntime(runtime_mode="live", paper_session_path=path)
    with pytest.raises(RuntimeError, match="cannot resume"):
        restarted._initialize_trading_chain()


@pytest.mark.parametrize("close_method", ["flip", "flatten", "reduce"])
def test_legacy_partial_close_keeps_actual_inventory_and_does_not_reverse(close_method):
    bus = InMemoryJetStream()
    risk = RiskOverlay(bus)
    strategy = ARCStrategyEngine(bus)
    gateway = ExecutionGateway()
    kernel = ExecutionKernel(bus, gateway, risk, ExecutionKernelConfig(pyramid_enabled=False))
    kernel._last_price["BTCUSDT_PERP"] = 100.0
    bus.publish("intent.target_position.final.BTCUSDT_PERP", target())
    original = gateway.submit_order

    def partial(order, **pricing):
        report = original(order, **pricing)
        return replace(report, status="PARTIALLY_FILLED", filled_qty=0.4, fee=0.1)

    gateway.submit_order = Mock(side_effect=partial)
    if close_method == "flip":
        kernel._execute_flip("BTCUSDT_PERP", 1.0, target(-1.0, 3), 100.0)
    elif close_method == "flatten":
        kernel._execute_flatten_only("BTCUSDT_PERP", 1.0, target(0.0, 3), 100.0)
    else:
        kernel._execute_reduce_only_qty("BTCUSDT_PERP", 1.0, 1.0, target(0.0, 3), 100.0, suffix="test")
    assert gateway.submit_order.call_count == 1
    assert kernel.positions["BTCUSDT_PERP"] == risk._actual_positions["BTCUSDT_PERP"] == 0.6
    assert strategy._positions[("BTCUSDT_PERP", Timeframe.TF_4H)].current_qty == 0.6


def test_order_is_immutable_before_gateway_submission():
    bus = InMemoryJetStream()
    risk = RiskOverlay(bus)
    strategy = ARCStrategyEngine(bus)
    gateway = ExecutionGateway()
    gateway.submit_order = Mock(wraps=gateway.submit_order)
    kernel = ExecutionKernel(bus, gateway, risk, ExecutionKernelConfig(pyramid_enabled=False))

    def try_mutation(_, submitted):
        with pytest.raises(FrozenInstanceError):
            submitted.side = -1

    bus.subscribe("exec.order.submit", try_mutation)
    kernel._last_price["BTCUSDT_PERP"] = 100.0
    bus.publish("intent.target_position.final.BTCUSDT_PERP", target())
    assert gateway.submit_order.call_args.args[0].side == 1
    assert kernel.positions["BTCUSDT_PERP"] == risk._actual_positions["BTCUSDT_PERP"] == 1.0
    position = strategy._positions[("BTCUSDT_PERP", Timeframe.TF_4H)]
    assert position.direction * position.current_qty == 1.0


@pytest.mark.parametrize("side", [-1, 1])
def test_subminimum_fills_accumulate_and_reduction_retains_signed_residual(side):
    bus = InMemoryJetStream()
    risk = RiskOverlay(bus)
    strategy = ARCStrategyEngine(bus)
    gateway = ExecutionGateway()
    kernel = ExecutionKernel(bus, gateway, risk, ExecutionKernelConfig(pyramid_enabled=False))
    kernel._last_price["BTCUSDT_PERP"] = 100.0
    original = gateway.submit_order
    fills = iter([0.00005, 0.00005, 0.000025])

    def partial(order, **pricing):
        return replace(original(order, **pricing), status="PARTIALLY_FILLED", filled_qty=next(fills), fee=0.0)

    gateway.submit_order = partial
    for cutoff in (2, 3):
        bus.publish("intent.target_position.final.BTCUSDT_PERP", target(side * 0.001, cutoff))
        position = strategy._positions[("BTCUSDT_PERP", Timeframe.TF_4H)]
        assert position.direction * position.current_qty == risk._actual_positions["BTCUSDT_PERP"]
        assert kernel.positions["BTCUSDT_PERP"] == risk._actual_positions["BTCUSDT_PERP"]
    current = side * 0.0001
    assert kernel.positions["BTCUSDT_PERP"] == current
    kernel._execute_reduce_only_qty("BTCUSDT_PERP", current, abs(current), target(0.0, 4), 100.0, suffix="test")
    expected = side * 0.000075
    position = strategy._positions[("BTCUSDT_PERP", Timeframe.TF_4H)]
    assert kernel.positions["BTCUSDT_PERP"] == risk._actual_positions["BTCUSDT_PERP"] == expected
    assert position.direction * position.current_qty == expected


def test_signed_quantity_roundoff_rule_preserves_real_small_positions():
    import math

    from zenquant.domain.quantity import add_filled_quantity

    for sign in (-1, 1):
        quantity = 0.0
        for fill in (sign * 1.0, -sign * 0.999835, -sign * 0.000165):
            quantity = add_filled_quantity(quantity, fill)
        assert quantity == 0.0
        assert add_filled_quantity(sign * 0.0001, -sign * 0.000025) == sign * 0.000075
        assert add_filled_quantity(0.0, sign * 1e-16) == sign * 1e-16
        for near_one in (math.nextafter(1.0, 0.0), math.nextafter(1.0, math.inf)):
            assert add_filled_quantity(sign * 1.0, -sign * near_one) == 0.0


def test_zero_and_opposing_roundoff_route_identically():
    import math

    from zenquant.domain.quantity import add_filled_quantity

    traces = []
    for cancellation in (1.0, math.nextafter(1.0, 0.0), math.nextafter(1.0, math.inf)):
        bus = InMemoryJetStream()
        risk = RiskOverlay(bus)
        kernel = ExecutionKernel(bus, ExecutionGateway(), risk)
        kernel._positions["BTCUSDT_PERP"] = add_filled_quantity(1.0, -cancellation)
        kernel._last_price["BTCUSDT_PERP"] = 100.0
        orders = []
        bus.subscribe("exec.order.submit", lambda _, submitted, output=orders: output.append(asdict(submitted)))
        bus.publish("intent.target_position.final.BTCUSDT_PERP", target(-10.0))
        traces.append(orders)
    assert traces[0] == traces[1] == traces[2]
    assert traces[0][0]["quantity"] == 1.0


@pytest.mark.parametrize("next_price", [90.0, 110.0])
def test_final_cap_applies_to_pyramid_dca_and_repeated_same_bar_targets(next_price):
    bus = InMemoryJetStream()
    risk = RiskOverlay(bus)
    kernel = ExecutionKernel(bus, ExecutionGateway(), risk)
    orders = []
    bus.subscribe("exec.order.submit", lambda _, submitted: orders.append(submitted))
    kernel._last_price["BTCUSDT_PERP"] = 100.0
    bus.publish("intent.target_position.final.BTCUSDT_PERP", target(10.0, 2))
    assert orders[0].quantity == 1.0
    kernel._last_price["BTCUSDT_PERP"] = next_price
    bus.publish("intent.target_position.final.BTCUSDT_PERP", target(10.0, 2))
    assert len(orders) == 1
    bus.publish("intent.target_position.final.BTCUSDT_PERP", target(10.0, 3))
    assert len(orders) == 2
    assert orders[-1].quantity <= 1.0
    assert kernel.positions["BTCUSDT_PERP"] == risk._actual_positions["BTCUSDT_PERP"]


def test_flip_budget_and_full_close_open_fees_are_consistent():
    for cap, expected_position, expected_reports in ((1.0, 0.0, 2), (2.0, -0.2, 3)):
        bus = InMemoryJetStream()
        risk = RiskOverlay(bus)
        strategy = ARCStrategyEngine(bus)
        kernel = ExecutionKernel(
            bus,
            ExecutionGateway(),
            risk,
            ExecutionKernelConfig(pyramid_enabled=False, max_trade_qty_per_bar=cap),
        )
        reports = []
        bus.subscribe("exec.order.report", lambda _, report, output=reports: output.append(report))
        kernel._last_price["BTCUSDT_PERP"] = 100.0
        bus.publish("intent.target_position.final.BTCUSDT_PERP", target(1.0, 2))
        bus.publish("intent.target_position.final.BTCUSDT_PERP", target(-1.0, 3))
        assert len(reports) == expected_reports
        assert sum(report.filled_qty for report in reports if report.transaction_time_ns == 3) <= cap
        assert kernel.positions["BTCUSDT_PERP"] == risk._actual_positions["BTCUSDT_PERP"] == expected_position
        position = strategy._positions.get(("BTCUSDT_PERP", Timeframe.TF_4H))
        assert (position.direction * position.current_qty if position else 0.0) == expected_position
        assert risk._cash == pytest.approx(
            100_000.0 - sum(report.side * report.filled_qty * report.avg_price + report.fee for report in reports)
        )


@pytest.mark.parametrize("side", [-1, 1])
@pytest.mark.parametrize("path", ["normal", "flatten", "flip", "helper"])
def test_reduce_only_rounds_down_against_actual_partial_inventory(side, path):
    bus = InMemoryJetStream()
    risk = RiskOverlay(bus)
    strategy = ARCStrategyEngine(bus)
    gateway = ExecutionGateway()
    kernel = ExecutionKernel(
        bus, gateway, risk, ExecutionKernelConfig(pyramid_enabled=False, gradual_exit_enabled=False)
    )
    kernel._last_price["BTCUSDT_PERP"] = 100.0
    original = gateway.submit_order
    gateway.submit_order = lambda submitted, **pricing: replace(
        original(submitted, **pricing), status="PARTIALLY_FILLED", filled_qty=0.0001507, fee=0.0
    )
    bus.publish("intent.target_position.final.BTCUSDT_PERP", target(float(side), 2))
    gateway.submit_order = Mock(wraps=original)
    current = side * 0.0001507
    close_target = target(0.0, 3)
    if path == "normal":
        bus.publish("intent.target_position.final.BTCUSDT_PERP", replace(close_target, reduce_only=True))
    elif path == "flatten":
        kernel._execute_flatten_only("BTCUSDT_PERP", current, close_target, 100.0)
    elif path == "flip":
        kernel._execute_flip("BTCUSDT_PERP", current, target(-float(side), 3), 100.0)
    else:
        kernel._execute_reduce_only_qty("BTCUSDT_PERP", current, abs(current), close_target, 100.0, suffix="partial")
    assert gateway.submit_order.call_count == 1
    assert gateway.submit_order.call_args.args[0].quantity == 0.00015
    expected = side * 0.0000007
    position = strategy._positions[("BTCUSDT_PERP", Timeframe.TF_4H)]
    assert kernel.positions["BTCUSDT_PERP"] == risk._actual_positions["BTCUSDT_PERP"] == expected
    assert position.direction * position.current_qty == expected


def test_pyramid_progress_changes_only_with_actual_fills():
    bus = InMemoryJetStream()
    risk = RiskOverlay(bus)
    gateway = ExecutionGateway()
    kernel = ExecutionKernel(bus, gateway, risk)
    kernel._last_price["BTCUSDT_PERP"] = 100.0
    original = gateway.submit_order
    gateway.submit_order = lambda submitted, **pricing: replace(
        original(submitted, **pricing), status="PARTIALLY_FILLED", filled_qty=0.4, fee=0.0
    )
    bus.publish("intent.target_position.final.BTCUSDT_PERP", target(10.0, 2))
    assert kernel._pyramid_filled_pct["BTCUSDT_PERP"] == 0.04
    gateway.submit_order = Mock(wraps=original)
    kernel._last_price["BTCUSDT_PERP"] = 110.0
    bus.publish("intent.target_position.final.BTCUSDT_PERP", target(10.0, 2))
    assert gateway.submit_order.call_args.args[0].quantity == 0.6
    assert kernel._pyramid_filled_pct["BTCUSDT_PERP"] == 0.1
    for _ in range(3):
        bus.publish("intent.target_position.final.BTCUSDT_PERP", target(10.0, 2))
    assert gateway.submit_order.call_count == 1
    assert kernel._pyramid_filled_pct["BTCUSDT_PERP"] == 0.1
    bus.publish("intent.target_position.final.BTCUSDT_PERP", target(10.0, 3))
    assert kernel._pyramid_filled_pct["BTCUSDT_PERP"] == 0.2
    tranches = kernel._inventory_tranches["BTCUSDT_PERP"]
    gateway.submit_order = lambda submitted, **pricing: replace(
        original(submitted, **pricing), status="REJECTED", filled_qty=0.0, fee=0.0, avg_price=0.0
    )
    bus.publish("intent.target_position.final.BTCUSDT_PERP", target(10.0, 4))
    assert kernel._pyramid_filled_pct["BTCUSDT_PERP"] == 0.2
    assert kernel._inventory_tranches["BTCUSDT_PERP"] == tranches
    assert kernel.positions["BTCUSDT_PERP"] == risk._actual_positions["BTCUSDT_PERP"] == 2.0


def test_standalone_paper_entrypoint_refuses_restart_before_gateway(tmp_path, monkeypatch):
    import asyncio

    from scripts.paper_trade import run_paper_trading

    import zenquant.execution
    from zenquant.execution import SimulatedGateway

    monkeypatch.chdir(tmp_path)
    real_kernel = ExecutionKernel

    class EndBeforeNetworking(Exception):
        pass

    def construct_and_submit(*args, **kwargs):
        kernel = real_kernel(*args, **kwargs)
        kernel._last_price["BTCUSDT_PERP"] = 100.0
        kwargs["bus"].publish("intent.target_position.final.BTCUSDT_PERP", target())
        raise EndBeforeNetworking

    original = SimulatedGateway.submit_order
    monkeypatch.setattr(zenquant.execution, "ExecutionKernel", construct_and_submit)
    with patch.object(SimulatedGateway, "submit_order", autospec=True, side_effect=original) as submit:
        with pytest.raises(EndBeforeNetworking):
            asyncio.run(run_paper_trading())
        assert submit.call_count == 1
        with pytest.raises(RuntimeError, match="cannot resume"):
            asyncio.run(run_paper_trading())
        assert submit.call_count == 1
