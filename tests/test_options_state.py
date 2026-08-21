"""Contract tests for the observation-only point-in-time OptionsState."""

from __future__ import annotations

import dataclasses
import math
import unittest

from zenquant.data.options_state import (
    OptionsState,
    build_options_state,
    interpolate_25_delta_iv,
)

DAY_NS = 86_400 * 1_000_000_000
FOUR_HOURS_NS = 4 * 3_600 * 1_000_000_000


def _row(
    *,
    source_ns: int,
    dte: int,
    strike: float,
    option_type: str,
    iv_pct: float,
    oi: float = 100.0,
    spot: float = 100.0,
) -> dict[str, object]:
    return {
        "snapshot_ts_ns": source_ns,
        "currency": "BTC",
        "instrument_name": f"BTC-{dte}D-{strike:g}-{option_type[0].upper()}",
        "expiry_ts_ms": (source_ns + dte * DAY_NS) // 1_000_000,
        "expiry_date": "2026-09-01",
        "strike": strike,
        "option_type": option_type,
        "underlying_price": spot,
        "open_interest": oi,
        "volume_usd": 1_000.0,
        "mark_iv": iv_pct,
        "bid_price": 0.1,
        "ask_price": 0.2,
        "mid_price": 0.15,
        "underlying_index": "BTC",
        "settlement_period": "month",
    }


def _complete_chain(source_ns: int, *, spot: float = 100.0, skew_bump: float = 0.0) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for dte, base_iv in ((7, 50.0), (30, 60.0), (90, 70.0)):
        for strike in (70.0, 80.0, 90.0, 100.0, 110.0, 120.0, 130.0):
            put_bump = skew_bump if strike < spot else 0.0
            rows.append(
                _row(
                    source_ns=source_ns,
                    dte=dte,
                    strike=strike,
                    option_type="put",
                    iv_pct=base_iv + put_bump,
                    oi=300.0 if strike == 90.0 else 100.0,
                    spot=spot,
                )
            )
            rows.append(
                _row(
                    source_ns=source_ns,
                    dte=dte,
                    strike=strike,
                    option_type="call",
                    iv_pct=base_iv,
                    oi=400.0 if strike == 110.0 else 100.0,
                    spot=spot,
                )
            )
    return rows


class Black76DeltaTests(unittest.TestCase):
    def test_interpolates_iv_at_true_black76_25_delta(self) -> None:
        # Delta coordinates are calculated by the production Black-76 formula;
        # this contract verifies interpolation in delta space, not moneyness.
        selected = interpolate_25_delta_iv(
            (
                {"delta_abs": 0.20, "iv": 0.70, "strike": 120.0},
                {"delta_abs": 0.30, "iv": 0.80, "strike": 110.0},
            )
        )
        self.assertIsNotNone(selected)
        assert selected is not None
        self.assertAlmostEqual(selected.iv, 0.75)
        self.assertEqual(selected.method, "linear_delta_interpolation")

    def test_deribit_percent_iv_is_normalized_to_decimal(self) -> None:
        source_ns = 10 * DAY_NS
        cutoff_ns = source_ns + FOUR_HOURS_NS
        state = build_options_state(
            _complete_chain(source_ns),
            cutoff_time_ns=cutoff_ns,
            realized_vol_30d=0.45,
            realized_vol_source_timestamp_ns=cutoff_ns,
        )
        self.assertAlmostEqual(state.atm_iv_7d or 0.0, 0.50)
        self.assertAlmostEqual(state.atm_iv_30d or 0.0, 0.60)
        self.assertAlmostEqual(state.atm_iv_90d or 0.0, 0.70)
        self.assertTrue(0.0 < (state.put_iv_25d_30d or 0.0) < 2.0)
        self.assertTrue(0.0 < (state.call_iv_25d_30d or 0.0) < 2.0)

    def test_true_25_delta_rr_is_not_the_90_110_moneyness_proxy(self) -> None:
        source_ns = 12 * DAY_NS
        chain = _complete_chain(source_ns)
        for row in chain:
            if row["expiry_ts_ms"] != (source_ns + 30 * DAY_NS) // 1_000_000:
                continue
            if row["option_type"] == "put" and row["strike"] == 90.0:
                row["mark_iv"] = 70.0
            if row["option_type"] == "call" and row["strike"] == 110.0:
                row["mark_iv"] = 40.0
            if row["option_type"] == "call" and row["strike"] == 120.0:
                row["mark_iv"] = 80.0
        state = build_options_state(
            chain,
            cutoff_time_ns=source_ns + FOUR_HOURS_NS,
            realized_vol_30d=0.45,
            realized_vol_source_timestamp_ns=source_ns + FOUR_HOURS_NS,
        )
        self.assertIsNotNone(state.risk_reversal_25d_30d)
        self.assertIsNotNone(state.put_call_skew_25d_30d)
        self.assertIsNotNone(state.moneyness_skew_proxy_30d)
        self.assertAlmostEqual(
            state.risk_reversal_25d_30d or 0.0,
            -(state.put_call_skew_25d_30d or 0.0),
        )
        self.assertNotAlmostEqual(
            state.risk_reversal_25d_30d or 0.0,
            state.moneyness_skew_proxy_30d or 0.0,
            places=5,
        )


class OptionsStateCalculationTests(unittest.TestCase):
    def test_computes_iv_rv_term_oi_and_normalized_gex(self) -> None:
        source_ns = 20 * DAY_NS
        state = build_options_state(
            _complete_chain(source_ns),
            cutoff_time_ns=source_ns + FOUR_HOURS_NS,
            realized_vol_30d=0.45,
            realized_vol_source_timestamp_ns=source_ns + FOUR_HOURS_NS,
        )
        self.assertAlmostEqual(state.iv_minus_rv_30d or 0.0, 0.15)
        self.assertAlmostEqual(state.term_slope_7d_30d or 0.0, 0.10)
        self.assertAlmostEqual(state.term_slope_30d_90d or 0.0, 0.10)
        self.assertGreater(state.strike_oi_concentration, 0.0)
        self.assertGreater(state.expiry_oi_concentration, 0.0)
        self.assertTrue(-1.0 <= state.normalized_net_gex <= 1.0)
        self.assertEqual(state.gex_normalization, "net_gex/total_absolute_gex")
        if state.gamma_wall_distance is not None:
            self.assertTrue(math.isfinite(state.gamma_wall_distance))

    def test_computes_24h_asof_change_and_joint_label(self) -> None:
        source_ns = 30 * DAY_NS
        history = _complete_chain(source_ns - DAY_NS, spot=95.0, skew_bump=1.0)
        current = _complete_chain(source_ns, spot=100.0, skew_bump=5.0)
        state = build_options_state(
            current,
            cutoff_time_ns=source_ns + FOUR_HOURS_NS,
            realized_vol_30d=0.50,
            realized_vol_source_timestamp_ns=source_ns + FOUR_HOURS_NS,
            history_24h_rows=history,
        )
        self.assertAlmostEqual(state.spot_return_24h or 0.0, 100.0 / 95.0 - 1.0)
        self.assertGreater(state.skew_change_24h or 0.0, 0.0)
        self.assertEqual(state.spot_skew_joint_label, "SPOT_UP_SKEW_STEEPENING")

    def test_complete_stressed_surface_has_deterministic_shadow_label(self) -> None:
        source_ns = 35 * DAY_NS
        state = build_options_state(
            _complete_chain(source_ns, spot=90.0, skew_bump=12.0),
            cutoff_time_ns=source_ns + FOUR_HOURS_NS,
            realized_vol_30d=0.50,
            realized_vol_source_timestamp_ns=source_ns + FOUR_HOURS_NS,
            history_24h_rows=_complete_chain(source_ns - DAY_NS, spot=100.0),
        )
        self.assertEqual(state.trend_quality, "STRESS")
        self.assertEqual(state.shadow_risk_multiplier, 0.50)

    def test_state_is_immutable_roundtrips_and_has_stable_identity(self) -> None:
        source_ns = 40 * DAY_NS
        kwargs = {
            "cutoff_time_ns": source_ns + FOUR_HOURS_NS,
            "realized_vol_30d": 0.50,
            "realized_vol_source_timestamp_ns": source_ns + FOUR_HOURS_NS,
        }
        first = build_options_state(_complete_chain(source_ns), **kwargs)
        second = build_options_state(list(reversed(_complete_chain(source_ns))), **kwargs)
        self.assertEqual(first, second)
        self.assertEqual(first.options_state_id, second.options_state_id)
        self.assertEqual(OptionsState.from_dict(first.to_dict()), first)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            first.trend_quality = "STRESS"  # type: ignore[misc]

    def test_identity_binds_raw_chain_and_24h_source_identity(self) -> None:
        source_ns = 45 * DAY_NS
        current = _complete_chain(source_ns)
        history = _complete_chain(source_ns - DAY_NS, spot=99.0)
        first = build_options_state(
            current,
            cutoff_time_ns=source_ns + FOUR_HOURS_NS,
            realized_vol_30d=0.50,
            realized_vol_source_timestamp_ns=source_ns + FOUR_HOURS_NS,
            history_24h_rows=history,
        )
        changed = [dict(row) for row in current]
        changed[0]["volume_usd"] = 99_999.0
        second = build_options_state(
            changed,
            cutoff_time_ns=source_ns + FOUR_HOURS_NS,
            realized_vol_30d=0.50,
            realized_vol_source_timestamp_ns=source_ns + FOUR_HOURS_NS,
            history_24h_rows=history,
        )
        self.assertNotEqual(first.data_identity, second.data_identity)
        self.assertNotEqual(first.options_state_id, second.options_state_id)
        self.assertEqual(first.history_24h_source_timestamp_ns, source_ns - DAY_NS)
        changed_history = [dict(row) for row in history]
        changed_history[0]["volume_usd"] = 88_888.0
        third = build_options_state(
            current,
            cutoff_time_ns=source_ns + FOUR_HOURS_NS,
            realized_vol_30d=0.50,
            realized_vol_source_timestamp_ns=source_ns + FOUR_HOURS_NS,
            history_24h_rows=changed_history,
        )
        self.assertNotEqual(first.history_24h_data_identity, third.history_24h_data_identity)
        self.assertNotEqual(first.options_state_id, third.options_state_id)

    def test_old_history_is_not_mislabeled_as_24h_change(self) -> None:
        source_ns = 48 * DAY_NS
        state = build_options_state(
            _complete_chain(source_ns),
            cutoff_time_ns=source_ns + FOUR_HOURS_NS,
            realized_vol_30d=0.50,
            realized_vol_source_timestamp_ns=source_ns + FOUR_HOURS_NS,
            history_24h_rows=_complete_chain(source_ns - 3 * DAY_NS, spot=80.0),
        )
        self.assertIsNone(state.spot_return_24h)
        self.assertIsNone(state.skew_change_24h)
        self.assertEqual(state.history_24h_source_timestamp_ns, 0)
        self.assertIn("OPTIONS_24H_HISTORY_MISSING", state.quality_flags)

    def test_stale_or_malformed_chain_fails_closed_to_unknown_neutral(self) -> None:
        source_ns = 50 * DAY_NS
        malformed = _complete_chain(source_ns)
        for row in malformed:
            row["mark_iv"] = None
        state = build_options_state(
            malformed,
            cutoff_time_ns=source_ns + 2 * DAY_NS,
            realized_vol_30d=0.50,
            realized_vol_source_timestamp_ns=source_ns + 2 * DAY_NS,
        )
        self.assertEqual(state.trend_quality, "UNKNOWN")
        self.assertEqual(state.shadow_risk_multiplier, 1.0)
        self.assertTrue(state.is_stale)
        self.assertIn("OPTIONS_STALE", state.quality_flags)
        self.assertIn("OPTIONS_MALFORMED_IV", state.quality_flags)
        self.assertIn("OPTIONS_GEX_UNAVAILABLE", state.quality_flags)

    def test_requires_confirmed_4h_cutoff_and_rejects_future_source(self) -> None:
        source_ns = 60 * DAY_NS
        with self.assertRaisesRegex(ValueError, "confirmed 4h"):
            build_options_state(
                _complete_chain(source_ns),
                cutoff_time_ns=source_ns + 1,
                realized_vol_30d=0.50,
                realized_vol_source_timestamp_ns=source_ns,
            )
        with self.assertRaisesRegex(ValueError, "after cutoff"):
            build_options_state(
                _complete_chain(source_ns + FOUR_HOURS_NS),
                cutoff_time_ns=source_ns,
                realized_vol_30d=0.50,
                realized_vol_source_timestamp_ns=source_ns,
            )
        with self.assertRaisesRegex(ValueError, "realized volatility.*after cutoff"):
            build_options_state(
                _complete_chain(source_ns),
                cutoff_time_ns=source_ns + FOUR_HOURS_NS,
                realized_vol_30d=0.50,
                realized_vol_source_timestamp_ns=source_ns + 2 * FOUR_HOURS_NS,
            )

    def test_rejects_non_btc_chain(self) -> None:
        source_ns = 64 * DAY_NS
        chain = _complete_chain(source_ns)
        chain[0]["currency"] = "ETH"
        with self.assertRaisesRegex(ValueError, "BTC-only"):
            build_options_state(
                chain,
                cutoff_time_ns=source_ns + FOUR_HOURS_NS,
                realized_vol_30d=0.50,
                realized_vol_source_timestamp_ns=source_ns + FOUR_HOURS_NS,
            )


if __name__ == "__main__":
    unittest.main()
