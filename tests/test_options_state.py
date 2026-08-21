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
    def test_realistic_nanosecond_source_boundaries_are_lossless(self) -> None:
        cutoff_ns = 1_728_000_000_000_000_000
        accepted = build_options_state(
            _complete_chain(cutoff_ns),
            cutoff_time_ns=cutoff_ns,
            realized_vol_30d=0.50,
            realized_vol_source_timestamp_ns=cutoff_ns,
        )
        self.assertEqual(accepted.cutoff_time_ns, cutoff_ns)
        self.assertEqual(accepted.source_timestamp_ns, cutoff_ns)

        for offset_ns in (1, 32, 64, 127, 128):
            with (
                self.subTest(offset_ns=offset_ns),
                self.assertRaisesRegex(ValueError, "source timestamp is after cutoff"),
            ):
                build_options_state(
                    _complete_chain(cutoff_ns + offset_ns),
                    cutoff_time_ns=cutoff_ns,
                    realized_vol_30d=0.50,
                    realized_vol_source_timestamp_ns=cutoff_ns,
                )

    def test_integral_timestamp_scalars_and_exact_strings_are_accepted(self) -> None:
        import numpy as np

        cutoff_ns = 1_728_000_000_000_000_000
        chain = _complete_chain(cutoff_ns)
        for row in chain:
            row["snapshot_ts_ns"] = str(cutoff_ns)
            row["expiry_ts_ms"] = np.int64(row["expiry_ts_ms"])
        state = build_options_state(
            chain,
            cutoff_time_ns=np.int64(cutoff_ns),
            realized_vol_30d=0.50,
            realized_vol_source_timestamp_ns=str(cutoff_ns),
        )
        self.assertEqual(state.cutoff_time_ns, cutoff_ns)
        self.assertEqual(state.source_timestamp_ns, cutoff_ns)
        self.assertEqual(state.selected_expiry_ts_ms_30d, 1_730_592_000_000)

    def test_invalid_cutoff_timestamp_types_are_programmer_errors(self) -> None:
        cutoff_ns = 1_728_000_000_000_000_000
        for invalid in (
            True,
            float(cutoff_ns),
            math.nan,
            math.inf,
            "1728000000000000000.0",
            "nan",
            2**63,
        ):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(ValueError, "confirmed 4h cutoff"):
                build_options_state(
                    _complete_chain(cutoff_ns),
                    cutoff_time_ns=invalid,  # type: ignore[arg-type]
                    realized_vol_30d=0.50,
                    realized_vol_source_timestamp_ns=cutoff_ns,
                )

    def test_invalid_source_and_expiry_timestamps_degrade_to_unknown(self) -> None:
        cutoff_ns = 1_728_000_000_000_000_000
        timestamp_mutations = (
            ("snapshot_ts_ns", True, "SOURCE_TIMESTAMP"),
            ("snapshot_ts_ns", float(cutoff_ns), "SOURCE_TIMESTAMP"),
            ("snapshot_ts_ns", math.nan, "SOURCE_TIMESTAMP"),
            ("snapshot_ts_ns", math.inf, "SOURCE_TIMESTAMP"),
            ("snapshot_ts_ns", "1728000000000000000.5", "SOURCE_TIMESTAMP"),
            ("snapshot_ts_ns", 2**63, "SOURCE_TIMESTAMP"),
            ("expiry_ts_ms", 1_730_592_000_000.0, "MALFORMED_EXPIRY"),
            ("expiry_ts_ms", "1730592000000.5", "MALFORMED_EXPIRY"),
            ("expiry_ts_ms", 2**63, "MALFORMED_EXPIRY"),
        )
        for field, invalid, expected_flag in timestamp_mutations:
            with self.subTest(field=field, invalid=invalid):
                chain = _complete_chain(cutoff_ns)
                chain[0][field] = invalid
                state = build_options_state(
                    chain,
                    cutoff_time_ns=cutoff_ns,
                    realized_vol_30d=0.50,
                    realized_vol_source_timestamp_ns=cutoff_ns,
                )
                self.assertEqual(state.trend_quality, "UNKNOWN")
                self.assertEqual(state.shadow_risk_multiplier, 1.0)
                self.assertTrue(any(expected_flag in flag for flag in state.quality_flags))

    def test_history_one_nanosecond_newer_than_24h_target_is_not_used(self) -> None:
        current_ns = 1_728_000_000_000_000_000
        history_ns = current_ns - DAY_NS + 1
        state = build_options_state(
            _complete_chain(current_ns, spot=110.0),
            cutoff_time_ns=current_ns,
            realized_vol_30d=0.50,
            realized_vol_source_timestamp_ns=current_ns,
            history_24h_rows=_complete_chain(history_ns, spot=100.0),
        )
        self.assertEqual(state.history_24h_source_timestamp_ns, 0)
        self.assertIsNone(state.spot_return_24h)
        self.assertIn("OPTIONS_24H_HISTORY_MISSING", state.quality_flags)

    def test_realized_vol_source_one_nanosecond_after_cutoff_is_rejected(self) -> None:
        cutoff_ns = 1_728_000_000_000_000_000
        with self.assertRaisesRegex(ValueError, "realized volatility source is after cutoff"):
            build_options_state(
                _complete_chain(cutoff_ns),
                cutoff_time_ns=cutoff_ns,
                realized_vol_30d=0.50,
                realized_vol_source_timestamp_ns=cutoff_ns + 1,
            )

    def test_malformed_chain_fields_degrade_to_unknown_neutral(self) -> None:
        source_ns = 18 * DAY_NS
        mutations = {
            "mark_iv": "bad",
            "open_interest": "bad",
            "strike": "bad",
            "expiry_ts_ms": "bad",
            "gamma": "bad",
        }
        for field, value in mutations.items():
            with self.subTest(field=field):
                chain = _complete_chain(source_ns)
                chain[0][field] = value
                state = build_options_state(
                    chain,
                    cutoff_time_ns=source_ns + FOUR_HOURS_NS,
                    realized_vol_30d=0.45,
                    realized_vol_source_timestamp_ns=source_ns + FOUR_HOURS_NS,
                )
                self.assertEqual(state.trend_quality, "UNKNOWN")
                self.assertEqual(state.shadow_risk_multiplier, 1.0)
                self.assertTrue(any("MALFORMED" in flag for flag in state.quality_flags))

    def test_mixed_or_missing_source_timestamps_degrade_to_unknown(self) -> None:
        source_ns = 19 * DAY_NS
        chains = []
        mixed = _complete_chain(source_ns)
        mixed[0]["snapshot_ts_ns"] = source_ns - FOUR_HOURS_NS
        chains.append(mixed)
        missing = _complete_chain(source_ns)
        missing[0].pop("snapshot_ts_ns")
        chains.append(missing)
        for chain in chains:
            state = build_options_state(
                chain,
                cutoff_time_ns=source_ns + FOUR_HOURS_NS,
                realized_vol_30d=0.45,
                realized_vol_source_timestamp_ns=source_ns + FOUR_HOURS_NS,
            )
            self.assertEqual(state.trend_quality, "UNKNOWN")
            self.assertEqual(state.shadow_risk_multiplier, 1.0)
            self.assertEqual(state.source_timestamp_ns, 0)
            self.assertTrue(any("SOURCE_TIMESTAMP" in flag for flag in state.quality_flags))

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

    def test_duplicate_instruments_have_order_invariant_identity(self) -> None:
        source_ns = 42 * DAY_NS
        chain = _complete_chain(source_ns)
        duplicate = dict(chain[0])
        duplicate["volume_usd"] = 77_777.0
        chain.append(duplicate)
        kwargs = {
            "cutoff_time_ns": source_ns + FOUR_HOURS_NS,
            "realized_vol_30d": 0.50,
            "realized_vol_source_timestamp_ns": source_ns + FOUR_HOURS_NS,
        }
        first = build_options_state(chain, **kwargs)
        second = build_options_state(list(reversed(chain)), **kwargs)
        self.assertEqual(first, second)
        self.assertEqual(first.options_state_id, second.options_state_id)
        self.assertIn("OPTIONS_DUPLICATE_INSTRUMENTS", first.quality_flags)
        self.assertEqual(first.trend_quality, "UNKNOWN")

    def test_percent_and_decimal_iv_have_identical_calculated_metrics(self) -> None:
        source_ns = 43 * DAY_NS
        percent_chain = _complete_chain(source_ns)
        decimal_chain = [dict(row) for row in percent_chain]
        for row in decimal_chain:
            row["mark_iv"] = float(row["mark_iv"]) / 100.0
            row["mark_iv_unit"] = "decimal"
        kwargs = {
            "cutoff_time_ns": source_ns + FOUR_HOURS_NS,
            "realized_vol_30d": 0.50,
            "realized_vol_source_timestamp_ns": source_ns + FOUR_HOURS_NS,
        }
        percent = build_options_state(percent_chain, **kwargs)
        decimal = build_options_state(decimal_chain, **kwargs)
        metric_names = (
            "atm_iv_7d",
            "atm_iv_30d",
            "atm_iv_90d",
            "put_iv_25d_30d",
            "call_iv_25d_30d",
            "put_call_skew_25d_30d",
            "normalized_net_gex",
            "gamma_wall_strike",
            "gamma_flip_strike",
        )
        self.assertEqual(
            tuple(getattr(percent, name) for name in metric_names),
            tuple(getattr(decimal, name) for name in metric_names),
        )

    def test_records_actual_delta_selection_and_expiry_methods(self) -> None:
        source_ns = 44 * DAY_NS
        state = build_options_state(
            _complete_chain(source_ns),
            cutoff_time_ns=source_ns + FOUR_HOURS_NS,
            realized_vol_30d=0.50,
            realized_vol_source_timestamp_ns=source_ns + FOUR_HOURS_NS,
        )
        self.assertIn(
            state.put_25d_selection_method,
            {"exact_delta", "linear_delta_interpolation", "nearest_delta"},
        )
        self.assertIn(
            state.call_25d_selection_method,
            {"exact_delta", "linear_delta_interpolation", "nearest_delta"},
        )
        self.assertEqual(state.selected_expiry_ts_ms_30d, (source_ns + 30 * DAY_NS) // 1_000_000)
        self.assertIn("mark_iv_unit=percent_or_decimal", state.calculation_method)
        self.assertIn("normalized_to_annualized_decimal", state.calculation_method)
        self.assertIn("per_leg_selection_recorded", state.calculation_method)
        self.assertNotIn(";linear_delta_interpolation;", state.calculation_method)

    def test_unknown_iv_unit_is_malformed_and_neutral(self) -> None:
        source_ns = 44 * DAY_NS
        chain = _complete_chain(source_ns)
        chain[0]["mark_iv_unit"] = "mystery"
        state = build_options_state(
            chain,
            cutoff_time_ns=source_ns + FOUR_HOURS_NS,
            realized_vol_30d=0.50,
            realized_vol_source_timestamp_ns=source_ns + FOUR_HOURS_NS,
        )
        self.assertEqual(state.trend_quality, "UNKNOWN")
        self.assertEqual(state.shadow_risk_multiplier, 1.0)
        self.assertIn("OPTIONS_MALFORMED_IV_UNIT", state.quality_flags)

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
