from __future__ import annotations

from dataclasses import replace

import pytest

from zenquant.domain import ExecutionReport, PositionEpisode, Timeframe
from zenquant.historical.replay import HistoricalBacktest, HistoricalBacktestConfig, _FundingAccumulator, _TradeLedger
from zenquant.research.episode_ledger import PositionEpisodeLedger
from zenquant.research.gate_v3 import (
    GateSeverity,
    ProfitFactorStatus,
    calculate_episode_profit_factor,
    evaluate_cadence,
    evaluate_gate_v3,
    position_switch_count,
)


def _episode(index: int, net_pnl: float, *, side: int = 1) -> PositionEpisode:
    return PositionEpisode(
        episode_id=f"episode-{index}",
        symbol="BTCUSDT",
        side=side,
        opened_at_ns=index * 10,
        closed_at_ns=index * 10 + 1,
        entry_price=100.0,
        exit_price=100.0 + net_pnl,
        max_abs_qty=1.0,
        remaining_qty=0.0,
        gross_realized_pnl=net_pnl,
        fees=0.0,
        funding_pnl=0.0,
        net_pnl=net_pnl,
        mfe_pnl=max(0.0, net_pnl),
        mae_pnl=min(0.0, net_pnl),
        unrealized_pnl=0.0,
        fill_count=2,
        closed=True,
    )


def test_profit_factor_reports_insufficient_sample_without_closed_episodes() -> None:
    result = calculate_episode_profit_factor([])

    assert result.value is None
    assert result.status is ProfitFactorStatus.INSUFFICIENT_SAMPLE
    assert result.closed_episode_count == 0


def test_profit_factor_reports_no_loss_episodes_without_infinity() -> None:
    result = calculate_episode_profit_factor([_episode(1, 10.0), _episode(2, 5.0)])

    assert result.value is None
    assert result.status is ProfitFactorStatus.NO_LOSS_EPISODES
    assert result.gross_profit == pytest.approx(15.0)
    assert result.gross_loss == pytest.approx(0.0)


def test_profit_factor_uses_net_closed_episode_pnl_and_boundaries() -> None:
    episodes = [_episode(1, 10.0), _episode(2, -5.0), _episode(3, 0.0)]
    result = calculate_episode_profit_factor(episodes)

    assert result.value == pytest.approx(2.0)
    assert result.status is ProfitFactorStatus.DEFINED
    assert result.closed_episode_count == 3

    open_episode = replace(_episode(4, 1_000.0), closed=False, closed_at_ns=None, remaining_qty=1.0)
    assert calculate_episode_profit_factor([*episodes, open_episode]) == result


@pytest.mark.parametrize(
    ("switches_per_day", "expected"),
    [
        (0.0, GateSeverity.NONE),
        (1.0, GateSeverity.NONE),
        (1.000001, GateSeverity.SOFT),
        (2.0, GateSeverity.SOFT),
        (2.000001, GateSeverity.HARD),
        (5.0, GateSeverity.HARD),
        (5.000001, GateSeverity.HARD),
    ],
)
def test_normal_cadence_boundaries(switches_per_day: float, expected: GateSeverity) -> None:
    assert evaluate_cadence(switches_per_day).severity is expected


@pytest.mark.parametrize("policy", ["normal", "research", "emergency"])
def test_more_than_five_switches_per_day_is_hard_under_every_policy(policy: str) -> None:
    assert evaluate_cadence(5.000001, policy=policy).severity is GateSeverity.HARD


def test_position_switch_count_ignores_same_side_flat_reopen_and_counts_opposite_reopen() -> None:
    episodes = [
        _episode(1, 1.0, side=1),
        _episode(2, 1.0, side=1),
        _episode(3, 1.0, side=-1),
        _episode(4, 1.0, side=-1),
        _episode(5, 1.0, side=1),
    ]

    assert position_switch_count(episodes) == 2


def test_position_switch_count_preserves_input_order_for_same_timestamp_flips() -> None:
    episodes = [
        replace(_episode(1, 1.0, side=1), episode_id="z", opened_at_ns=10),
        replace(_episode(2, 1.0, side=-1), episode_id="a", opened_at_ns=10),
        replace(_episode(3, 1.0, side=1), episode_id="m", opened_at_ns=10),
    ]

    assert position_switch_count(episodes) == 2


@pytest.mark.parametrize(
    ("sides", "expected_switches", "expected_severity"),
    [
        ((1, 1), 0, GateSeverity.NONE),
        ((1, -1), 1, GateSeverity.NONE),
        ((1, -1, 1), 2, GateSeverity.SOFT),
        ((1, -1, 1, -1), 3, GateSeverity.HARD),
        ((1, -1, 1, -1, 1, -1, 1), 6, GateSeverity.HARD),
    ],
)
def test_gate_v3_cadence_uses_real_episode_side_transitions(
    sides: tuple[int, ...],
    expected_switches: int,
    expected_severity: GateSeverity,
) -> None:
    episodes = [_episode(index, 1.0, side=side) for index, side in enumerate(sides, start=1)]

    verdict = evaluate_gate_v3(episodes, elapsed_days=1.0)

    assert verdict.diagnostics["position_switch_count"] == expected_switches
    assert verdict.cadence.switches_per_day == pytest.approx(float(expected_switches))
    assert verdict.cadence.severity is expected_severity


def test_gate_v3_uses_episode_pf_and_keeps_fill_pf_diagnostic_only() -> None:
    episodes = [_episode(1, 10.0), _episode(2, -5.0)]

    passing = evaluate_gate_v3(
        episodes,
        switches_per_day=1.0,
        fill_profit_factor=0.1,
        action_profit_factor={"reduce": 0.2},
    )
    failing = evaluate_gate_v3(
        [_episode(1, 4.0), _episode(2, -5.0)],
        switches_per_day=1.0,
        fill_profit_factor=99.0,
        action_profit_factor={"reduce": 99.0},
    )

    assert passing.hard_failed is False
    assert passing.episode_profit_factor.value == pytest.approx(2.0)
    assert passing.diagnostics["fill_profit_factor"] == pytest.approx(0.1)
    assert failing.hard_failed is True
    assert "episode_profit_factor_below_minimum" in failing.hard_failures


def test_gate_v3_requires_two_closed_episodes_before_no_loss_pass() -> None:
    one_positive = evaluate_gate_v3([_episode(1, 5.0)], switches_per_day=0.0)
    one_positive_one_flat = evaluate_gate_v3(
        [_episode(1, 5.0), _episode(2, 0.0)],
        switches_per_day=0.0,
    )
    no_losses = evaluate_gate_v3([_episode(1, 5.0), _episode(2, 1.0)], switches_per_day=0.0)

    assert one_positive.hard_failed is True
    assert one_positive.episode_profit_factor.status is ProfitFactorStatus.NO_LOSS_EPISODES
    assert "episode_profit_factor_no_loss_sample_too_small" in one_positive.hard_failures
    assert one_positive_one_flat.hard_failed is False
    assert one_positive_one_flat.episode_profit_factor.status is ProfitFactorStatus.NO_LOSS_EPISODES
    assert no_losses.hard_failed is False
    assert no_losses.episode_profit_factor.status is ProfitFactorStatus.NO_LOSS_EPISODES


@pytest.mark.parametrize(
    "net_pnls",
    [
        (0.0,),
        (-5.0,),
        (-5.0, 0.0),
    ],
)
def test_closed_sample_without_gross_profit_reports_defined_zero(
    net_pnls: tuple[float, ...],
) -> None:
    episodes = [_episode(index, pnl) for index, pnl in enumerate(net_pnls, start=1)]
    result = calculate_episode_profit_factor(episodes)
    verdict = evaluate_gate_v3(episodes, switches_per_day=0.0)

    assert result.status is ProfitFactorStatus.DEFINED
    assert result.value == pytest.approx(0.0)
    assert result.closed_episode_count == len(net_pnls)
    assert verdict.hard_failed is True
    assert "episode_profit_factor_below_minimum" in verdict.hard_failures


def test_mixed_profit_and_loss_reports_ratio_without_global_sample_minimum() -> None:
    result = calculate_episode_profit_factor([_episode(1, 10.0), _episode(2, -4.0)])

    assert result.status is ProfitFactorStatus.DEFINED
    assert result.value == pytest.approx(2.5)


def test_gate_v3_reports_empty_sample_as_insufficient() -> None:
    insufficient = evaluate_gate_v3([], switches_per_day=0.0)

    assert insufficient.hard_failed is True
    assert "episode_profit_factor_insufficient_sample" in insufficient.hard_failures


def test_replay_exposes_episode_pf_without_replacing_fill_pf_diagnostic() -> None:
    replay = HistoricalBacktest.__new__(HistoricalBacktest)
    replay._config = HistoricalBacktestConfig(
        portfolio_symbol="BTCUSDT",
        starting_equity=100_000.0,
        execution_timeframe=Timeframe.TF_4H,
    )
    replay._risk_snapshots = []
    replay._reports = reports = []
    replay._trade_ledger = _TradeLedger()
    replay._episode_ledger = PositionEpisodeLedger()
    replay._funding_accumulator = _FundingAccumulator()
    replay.risk_overlay = type("Risk", (), {"equity": 100_000.0})()
    reports.extend(
        [
            ExecutionReport("1", "BTCUSDT", 1, "FILLED", 1.0, 100.0, 1.0, 1),
            ExecutionReport("2", "BTCUSDT", -1, "FILLED", 1.0, 110.0, 1.0, 2),
            ExecutionReport("3", "BTCUSDT", 1, "FILLED", 1.0, 100.0, 1.0, 3),
            ExecutionReport("4", "BTCUSDT", -1, "FILLED", 1.0, 95.0, 1.0, 4),
        ]
    )
    for report in reports:
        replay._trade_ledger.on_execution_report(report)
        replay._episode_ledger.on_fill(report)
    events = [
        {"open_time_ns": 0, "close_time_ns": 1, "event_timeframe": "4h"},
        {"open_time_ns": 2, "close_time_ns": 86_400_000_000_000, "event_timeframe": "4h"},
    ]

    metrics = replay._build_metrics(events)

    assert metrics.profit_factor == pytest.approx(2.0)
    assert metrics.episode_profit_factor == pytest.approx(8.0 / 7.0, abs=1e-6)
    assert metrics.episode_profit_factor_status == "DEFINED"
    assert metrics.closed_episode_count == 2
    assert metrics.open_episode_count == 0
    assert metrics.position_switch_count == 0
    assert metrics.position_switches_per_day == pytest.approx(0.0)
