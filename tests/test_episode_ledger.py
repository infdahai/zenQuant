from __future__ import annotations

import json
from dataclasses import asdict

import pytest

from zenquant.domain import ExecutionReport
from zenquant.historical.replay import HistoricalBacktest, _TradeLedger
from zenquant.research.episode_ledger import (
    FundingAllocation,
    PositionEpisodeLedger,
    PriceObservation,
)


def _fill(
    *,
    side: int,
    qty: float,
    price: float,
    fee: float,
    ts_ns: int,
    symbol: str = "BTCUSDT",
) -> ExecutionReport:
    return ExecutionReport(
        client_order_id=f"order-{symbol}-{ts_ns}",
        symbol=symbol,
        side=side,
        status="FILLED",
        filled_qty=qty,
        avg_price=price,
        fee=fee,
        transaction_time_ns=ts_ns,
    )


def test_episode_tracks_add_partial_reduction_exit_and_excursions() -> None:
    ledger = PositionEpisodeLedger()

    ledger.on_fill(_fill(side=1, qty=1.0, price=100.0, fee=1.0, ts_ns=1))
    ledger.on_fill(_fill(side=1, qty=1.0, price=120.0, fee=1.0, ts_ns=2))
    ledger.observe_price(PriceObservation("BTCUSDT", 3, high=140.0, low=90.0, close=125.0))
    ledger.on_fill(_fill(side=-1, qty=0.5, price=130.0, fee=0.5, ts_ns=4))

    open_episode = ledger.open_episodes[0]
    assert open_episode.side == 1
    assert open_episode.entry_price == pytest.approx(110.0)
    assert open_episode.max_abs_qty == pytest.approx(2.0)
    assert open_episode.remaining_qty == pytest.approx(1.5)
    assert open_episode.gross_realized_pnl == pytest.approx(10.0)
    assert open_episode.mfe_pnl == pytest.approx(60.0)
    assert open_episode.mae_pnl == pytest.approx(-40.0)
    assert open_episode.closed is False

    ledger.on_fill(_fill(side=-1, qty=1.5, price=130.0, fee=1.5, ts_ns=5))

    assert ledger.open_episodes == ()
    episode = ledger.closed_episodes[0]
    assert episode.closed is True
    assert episode.closed_at_ns == 5
    assert episode.exit_price == pytest.approx(130.0)
    assert episode.gross_realized_pnl == pytest.approx(40.0)
    assert episode.fees == pytest.approx(4.0)
    assert episode.funding_pnl == pytest.approx(0.0)
    assert episode.net_pnl == pytest.approx(36.0)
    assert episode.fill_count == 4
    assert episode.add_count == 1
    assert episode.reduce_count == 2
    assert episode.holding_duration_ns == 4


def test_flip_splits_at_zero_and_allocates_fee_proportionally() -> None:
    ledger = PositionEpisodeLedger()
    ledger.on_fill(_fill(side=1, qty=2.0, price=100.0, fee=2.0, ts_ns=1))

    ledger.on_fill(_fill(side=-1, qty=3.0, price=90.0, fee=3.0, ts_ns=2))

    closed_long = ledger.closed_episodes[0]
    open_short = ledger.open_episodes[0]
    assert closed_long.side == 1
    assert closed_long.gross_realized_pnl == pytest.approx(-20.0)
    assert closed_long.fees == pytest.approx(4.0)
    assert closed_long.net_pnl == pytest.approx(-24.0)
    assert closed_long.closed_at_ns == 2
    assert closed_long.add_count == 0
    assert closed_long.reduce_count == 1
    assert closed_long.holding_duration_ns == 1
    assert open_short.side == -1
    assert open_short.entry_price == pytest.approx(90.0)
    assert open_short.remaining_qty == pytest.approx(1.0)
    assert open_short.fees == pytest.approx(1.0)
    assert open_short.opened_at_ns == 2

    ledger.on_fill(_fill(side=1, qty=1.0, price=80.0, fee=1.0, ts_ns=3))

    closed_short = ledger.closed_episodes[1]
    assert closed_short.gross_realized_pnl == pytest.approx(10.0)
    assert closed_short.fees == pytest.approx(2.0)
    assert closed_short.net_pnl == pytest.approx(8.0)


def test_funding_is_allocated_only_to_the_open_symbol_episode() -> None:
    ledger = PositionEpisodeLedger()
    ledger.on_fill(_fill(side=1, qty=1.0, price=100.0, fee=0.0, ts_ns=1))
    ledger.on_fill(_fill(side=-1, qty=2.0, price=50.0, fee=0.0, ts_ns=1, symbol="ETHUSDT"))

    ledger.allocate_funding(FundingAllocation("BTCUSDT", 2, pnl=-2.0))
    ledger.allocate_funding(FundingAllocation("ETHUSDT", 2, pnl=1.5))
    ledger.on_fill(_fill(side=-1, qty=1.0, price=110.0, fee=0.0, ts_ns=3))

    btc = ledger.closed_episodes[0]
    eth = ledger.open_episodes[0]
    assert btc.symbol == "BTCUSDT"
    assert btc.funding_pnl == pytest.approx(-2.0)
    assert btc.net_pnl == pytest.approx(8.0)
    assert eth.symbol == "ETHUSDT"
    assert eth.funding_pnl == pytest.approx(1.5)


def test_open_episode_diagnostics_include_realized_and_unrealized_state() -> None:
    ledger = PositionEpisodeLedger()
    ledger.on_fill(_fill(side=1, qty=2.0, price=100.0, fee=2.0, ts_ns=1))
    ledger.on_fill(_fill(side=-1, qty=1.0, price=110.0, fee=1.0, ts_ns=2))
    ledger.observe_price(PriceObservation("BTCUSDT", 3, high=115.0, low=105.0, close=108.0))

    episode = ledger.open_episodes[0]
    assert episode.remaining_qty == pytest.approx(1.0)
    assert episode.gross_realized_pnl == pytest.approx(10.0)
    assert episode.unrealized_pnl == pytest.approx(8.0)
    assert episode.net_pnl == pytest.approx(7.0)
    assert episode.closed_at_ns is None


def test_ledger_ignores_unfilled_reports_and_rejects_per_symbol_time_reversal() -> None:
    ledger = PositionEpisodeLedger()
    rejected = _fill(side=1, qty=1.0, price=100.0, fee=1.0, ts_ns=2)
    rejected.status = "REJECTED"
    ledger.on_fill(rejected)
    assert ledger.episodes == ()

    ledger.on_fill(_fill(side=1, qty=1.0, price=100.0, fee=0.0, ts_ns=2))
    with pytest.raises(ValueError, match="chronological"):
        ledger.on_fill(_fill(side=1, qty=1.0, price=101.0, fee=0.0, ts_ns=1))


def test_replay_report_collection_feeds_legacy_and_episode_diagnostics() -> None:
    replay = HistoricalBacktest.__new__(HistoricalBacktest)
    replay._reports = []
    replay._trade_ledger = _TradeLedger()
    replay._episode_ledger = PositionEpisodeLedger()
    report = _fill(side=1, qty=1.0, price=100.0, fee=1.0, ts_ns=1)

    replay._collect_report("exec.order.report", report)

    assert replay._trade_ledger.position_qty == pytest.approx(1.0)
    assert replay._episode_ledger.position_qty("BTCUSDT") == pytest.approx(1.0)


def test_replay_allocates_each_funding_settlement_before_new_close_fills() -> None:
    replay = HistoricalBacktest.__new__(HistoricalBacktest)
    replay._config = type("Config", (), {"portfolio_symbol": "BTCUSDT"})()
    replay._episode_ledger = PositionEpisodeLedger()
    replay._episode_funding_time_ns = 0
    replay._episode_ledger.on_fill(_fill(side=1, qty=1.0, price=100.0, fee=0.0, ts_ns=1))
    row = {"close_time_ns": 3, "high": 120.0, "low": 90.0, "close": 110.0}
    funding_row = {"funding_time_ns": 2, "funding_rate": 0.01}

    replay._update_episode_market_accounting(row, funding_row)
    replay._update_episode_market_accounting(row, funding_row)

    episode = replay._episode_ledger.open_episodes[0]
    assert episode.funding_pnl == pytest.approx(-1.1)
    assert episode.mfe_pnl == pytest.approx(20.0)
    assert episode.mae_pnl == pytest.approx(-10.0)


def test_excursions_include_realized_pnl_after_partial_reduction() -> None:
    ledger = PositionEpisodeLedger()
    ledger.on_fill(_fill(side=1, qty=2.0, price=100.0, fee=0.0, ts_ns=1))
    ledger.on_fill(_fill(side=-1, qty=1.0, price=110.0, fee=0.0, ts_ns=2))

    ledger.observe_price(PriceObservation("BTCUSDT", 3, high=120.0, low=80.0, close=105.0))

    episode = ledger.open_episodes[0]
    assert episode.gross_realized_pnl == pytest.approx(10.0)
    assert episode.mfe_pnl == pytest.approx(30.0)
    assert episode.mae_pnl == pytest.approx(-10.0)


def test_exact_flatten_with_float_dust_does_not_open_phantom_flip() -> None:
    ledger = PositionEpisodeLedger()
    ledger.on_fill(_fill(side=1, qty=0.3, price=100.0, fee=0.0, ts_ns=1))

    ledger.on_fill(_fill(side=-1, qty=0.1 + 0.2, price=101.0, fee=0.0, ts_ns=2))

    assert len(ledger.closed_episodes) == 1
    assert ledger.open_episodes == ()


def test_ledger_counts_only_side_changes_across_flips_and_flat_reopens() -> None:
    ledger = PositionEpisodeLedger()
    ledger.on_fill(_fill(side=1, qty=1.0, price=100.0, fee=0.0, ts_ns=1))
    ledger.on_fill(_fill(side=-1, qty=1.0, price=101.0, fee=0.0, ts_ns=2))
    ledger.on_fill(_fill(side=1, qty=1.0, price=102.0, fee=0.0, ts_ns=3))
    ledger.on_fill(_fill(side=-1, qty=2.0, price=103.0, fee=0.0, ts_ns=4))

    assert [episode.side for episode in ledger.episodes] == [1, 1, -1]
    assert ledger.position_switch_count == 1


def test_episode_ledger_is_deterministically_json_serializable() -> None:
    ledger = PositionEpisodeLedger()
    ledger.on_fill(_fill(side=1, qty=1.0, price=100.0, fee=1.0, ts_ns=1))
    ledger.on_fill(_fill(side=1, qty=1.0, price=105.0, fee=1.0, ts_ns=2))
    ledger.on_fill(_fill(side=-1, qty=2.0, price=110.0, fee=2.0, ts_ns=3))

    first = json.dumps([asdict(episode) for episode in ledger.episodes], sort_keys=True)
    second = json.dumps([asdict(episode) for episode in ledger.episodes], sort_keys=True)

    assert first == second
    assert '"add_count": 1' in first
    assert '"reduce_count": 1' in first
    assert '"holding_duration_ns": 2' in first
