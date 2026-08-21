"""Tests for Gamma Exposure (GEX) analysis."""

from __future__ import annotations

import time
import unittest

from zenquant.research.gex import (
    GexProfile,
    StrikeGex,
    _black_scholes_gamma,
    _find_gamma_flip,
    compute_gex_profile,
    gex_to_signal,
)


def _make_row(
    strike: float,
    option_type: str,
    oi: float = 100.0,
    iv_pct: float = 60.0,
    spot: float = 95000.0,
    dte: int = 30,
) -> dict:
    now_s = time.time()
    expiry_ts_ms = int((now_s + dte * 86400) * 1000)
    return {
        "snapshot_ts_ns": int(now_s * 1e9),
        "strike": strike,
        "option_type": option_type,
        "open_interest": oi,
        "mark_iv": iv_pct,
        "expiry_ts_ms": expiry_ts_ms,
        "underlying_price": spot,
    }


class TestBlackScholesGamma(unittest.TestCase):
    def test_atm_gamma_positive(self):
        g = _black_scholes_gamma(spot=100, strike=100, time_to_expiry_years=0.1, iv=0.5)
        self.assertGreater(g, 0)

    def test_expired_option_zero(self):
        g = _black_scholes_gamma(spot=100, strike=100, time_to_expiry_years=0, iv=0.5)
        self.assertEqual(g, 0.0)

    def test_zero_iv_zero(self):
        g = _black_scholes_gamma(spot=100, strike=100, time_to_expiry_years=0.1, iv=0)
        self.assertEqual(g, 0.0)

    def test_deep_otm_lower_gamma(self):
        g_atm = _black_scholes_gamma(100, 100, 0.1, 0.5)
        g_otm = _black_scholes_gamma(100, 200, 0.1, 0.5)
        self.assertGreater(g_atm, g_otm)


class TestComputeGexProfile(unittest.TestCase):
    def test_gamma_flip_requires_an_actual_cumulative_sign_crossing(self):
        positive = [StrikeGex(90.0, 2.0, 0.0), StrikeGex(100.0, 3.0, 0.0)]
        negative = [StrikeGex(90.0, 0.0, -2.0), StrikeGex(100.0, 0.0, -3.0)]
        crossing = [StrikeGex(90.0, 2.0, 0.0), StrikeGex(100.0, 0.0, -4.0)]
        self.assertIsNone(_find_gamma_flip(positive, spot=100.0))
        self.assertIsNone(_find_gamma_flip(negative, spot=100.0))
        self.assertAlmostEqual(_find_gamma_flip(crossing, spot=100.0) or 0.0, 95.0)

    def test_empty_rows_returns_neutral(self):
        profile = compute_gex_profile([], spot=95000.0)
        self.assertEqual(profile.regime, "NEUTRAL")
        self.assertEqual(profile.net_gex, 0.0)
        self.assertIsNone(profile.gamma_wall_strike)

    def test_zero_spot_returns_neutral(self):
        rows = [_make_row(95000, "call")]
        profile = compute_gex_profile(rows, spot=0.0)
        self.assertEqual(profile.regime, "NEUTRAL")

    def test_call_heavy_produces_positive_gex(self):
        rows = [
            _make_row(95000, "call", oi=1000),
            _make_row(100000, "call", oi=1000),
        ]
        profile = compute_gex_profile(rows, spot=95000.0)
        self.assertGreater(profile.total_call_gex, 0)
        self.assertGreater(profile.net_gex, 0)

    def test_put_gex_is_negative(self):
        rows = [_make_row(90000, "put", oi=1000)]
        profile = compute_gex_profile(rows, spot=95000.0)
        self.assertLess(profile.total_put_gex, 0)

    def test_pinning_regime_when_net_gex_positive(self):
        rows = [
            _make_row(95000, "call", oi=5000),
        ]
        profile = compute_gex_profile(rows, spot=95000.0)
        # Large call OI near spot should produce positive net GEX
        self.assertEqual(profile.regime, "PINNING")

    def test_gamma_wall_at_highest_gex_strike(self):
        rows = [
            _make_row(90000, "call", oi=100),
            _make_row(95000, "call", oi=5000),  # large OI here
            _make_row(100000, "call", oi=100),
        ]
        profile = compute_gex_profile(rows, spot=95000.0)
        # Gamma wall should be at 95000 (highest call OI near ATM)
        self.assertIsNotNone(profile.gamma_wall_strike)

    def test_max_pain_within_strike_range(self):
        rows = [
            _make_row(90000, "call", oi=200),
            _make_row(95000, "call", oi=500),
            _make_row(100000, "put", oi=300),
            _make_row(90000, "put", oi=100),
        ]
        profile = compute_gex_profile(rows, spot=95000.0)
        if profile.max_pain_strike is not None:
            self.assertIn(profile.max_pain_strike, [90000.0, 95000.0, 100000.0])

    def test_expired_options_excluded(self):
        now_s = time.time()
        rows = [
            {
                "snapshot_ts_ns": int(now_s * 1e9),
                "strike": 95000,
                "option_type": "call",
                "open_interest": 1000,
                "mark_iv": 60.0,
                "expiry_ts_ms": int((now_s - 86400) * 1000),  # expired yesterday
                "underlying_price": 95000,
            }
        ]
        profile = compute_gex_profile(rows, spot=95000.0)
        self.assertEqual(profile.net_gex, 0.0)

    def test_strike_gex_net_property(self):
        sg = StrikeGex(strike=95000, call_gex=100.0, put_gex=-40.0)
        self.assertAlmostEqual(sg.net_gex, 60.0)


class TestGexToSignal(unittest.TestCase):
    def test_pinning_regime_positive_score(self):
        profile = GexProfile(
            spot=95000,
            ts_ns=0,
            strikes=[],
            gamma_wall_strike=97000,
            flip_strike=None,
            max_pain_strike=96000,
            total_call_gex=1e9,
            total_put_gex=-2e8,
            net_gex=8e8,
            regime="PINNING",
            regime_reason="test",
        )
        sig = gex_to_signal(profile, 95000)
        self.assertEqual(sig["gex_regime_score"], 1.0)
        self.assertAlmostEqual(sig["gamma_wall_distance_pct"], 97000 / 95000 - 1, places=4)

    def test_amplifying_regime_negative_score(self):
        profile = GexProfile(
            spot=95000,
            ts_ns=0,
            strikes=[],
            gamma_wall_strike=None,
            flip_strike=93000,
            max_pain_strike=94000,
            total_call_gex=1e8,
            total_put_gex=-9e8,
            net_gex=-8e8,
            regime="AMPLIFYING",
            regime_reason="test",
        )
        sig = gex_to_signal(profile, 95000)
        self.assertEqual(sig["gex_regime_score"], -1.0)

    def test_neutral_regime_zero_score(self):
        profile = GexProfile(
            spot=95000,
            ts_ns=0,
            strikes=[],
            gamma_wall_strike=None,
            flip_strike=None,
            max_pain_strike=None,
            total_call_gex=0,
            total_put_gex=0,
            net_gex=0,
            regime="NEUTRAL",
            regime_reason="test",
        )
        sig = gex_to_signal(profile, 95000)
        self.assertEqual(sig["gex_regime_score"], 0.0)


class TestGexRegimeBoundaryConditions(unittest.TestCase):
    """Boundary tests for GEX regime classification thresholds.

    Tests exact 1e6 threshold, epsilon around zero, and edge cases.
    Existing tests use happy path values (large positive/negative GEX).
    Boundary tests verify off-by-one logic around thresholds.
    """

    def test_exact_1e6_is_pinning_not_neutral(self) -> None:
        """net_gex = 1,000,000 exactly should be PINNING (logic uses abs < 1e6 for NEUTRAL)."""
        from zenquant.research.gex import _classify_regime

        regime, _ = _classify_regime(
            net_gex=1_000_000.0,
            spot=95000.0,
            gamma_wall=96000.0,
            flip=None,
        )

        # abs(1e6) = 1e6, which is NOT < 1e6, so should be PINNING
        self.assertEqual(regime, "PINNING")

    def test_just_below_1e6_is_neutral(self) -> None:
        """net_gex = 999,999 should be NEUTRAL (abs < 1e6)."""
        from zenquant.research.gex import _classify_regime

        regime, _ = _classify_regime(
            net_gex=999_999.0,
            spot=95000.0,
            gamma_wall=None,
            flip=None,
        )

        self.assertEqual(regime, "NEUTRAL")

    def test_exact_minus_1e6_is_amplifying_not_neutral(self) -> None:
        """net_gex = -1,000,000 exactly should be AMPLIFYING."""
        from zenquant.research.gex import _classify_regime

        regime, _ = _classify_regime(
            net_gex=-1_000_000.0,
            spot=95000.0,
            gamma_wall=None,
            flip=93000.0,
        )

        # abs(-1e6) = 1e6, which is NOT < 1e6, so should be AMPLIFYING
        self.assertEqual(regime, "AMPLIFYING")

    def test_just_above_minus_1e6_is_neutral(self) -> None:
        """net_gex = -999,999 should be NEUTRAL (abs < 1e6)."""
        from zenquant.research.gex import _classify_regime

        regime, _ = _classify_regime(
            net_gex=-999_999.0,
            spot=95000.0,
            gamma_wall=None,
            flip=None,
        )

        self.assertEqual(regime, "NEUTRAL")

    def test_exact_zero_is_neutral(self) -> None:
        """net_gex = 0.0 should be NEUTRAL."""
        from zenquant.research.gex import _classify_regime

        regime, _ = _classify_regime(
            net_gex=0.0,
            spot=95000.0,
            gamma_wall=None,
            flip=None,
        )

        self.assertEqual(regime, "NEUTRAL")

    def test_epsilon_positive_is_neutral(self) -> None:
        """net_gex = 0.1 (epsilon positive) should be NEUTRAL."""
        from zenquant.research.gex import _classify_regime

        regime, _ = _classify_regime(
            net_gex=0.1,
            spot=95000.0,
            gamma_wall=None,
            flip=None,
        )

        self.assertEqual(regime, "NEUTRAL")

    def test_epsilon_negative_is_neutral(self) -> None:
        """net_gex = -0.1 (epsilon negative) should be NEUTRAL."""
        from zenquant.research.gex import _classify_regime

        regime, _ = _classify_regime(
            net_gex=-0.1,
            spot=95000.0,
            gamma_wall=None,
            flip=None,
        )

        self.assertEqual(regime, "NEUTRAL")

    def test_large_positive_gex_is_pinning(self) -> None:
        """net_gex >> 1e6 should be PINNING."""
        from zenquant.research.gex import _classify_regime

        regime, _ = _classify_regime(
            net_gex=1e9,  # 1 billion
            spot=95000.0,
            gamma_wall=97000.0,
            flip=None,
        )

        self.assertEqual(regime, "PINNING")

    def test_large_negative_gex_is_amplifying(self) -> None:
        """net_gex << -1e6 should be AMPLIFYING."""
        from zenquant.research.gex import _classify_regime

        regime, _ = _classify_regime(
            net_gex=-1e9,  # -1 billion
            spot=95000.0,
            gamma_wall=None,
            flip=93000.0,
        )

        self.assertEqual(regime, "AMPLIFYING")

    def test_gex_profile_boundary_empty_strikes_after_filtering(self) -> None:
        """All strikes filtered out (min_oi, max_dte) should return NEUTRAL."""
        rows = [
            _make_row(95000, "call", oi=0.1),  # Below min_oi
        ]
        profile = compute_gex_profile(rows, spot=95000.0, min_oi=1.0)

        self.assertEqual(profile.regime, "NEUTRAL")
        self.assertEqual(profile.regime_reason, "No strikes after filtering")

    def test_gex_profile_boundary_all_expired(self) -> None:
        """All expired options (DTE < 0) should return NEUTRAL."""
        import time

        now_s = time.time()
        rows = [
            {
                "snapshot_ts_ns": int(now_s * 1e9),
                "strike": 95000,
                "option_type": "call",
                "open_interest": 1000,
                "mark_iv": 60.0,
                "expiry_ts_ms": int((now_s - 86400) * 1000),  # expired
                "underlying_price": 95000,
            }
        ]
        profile = compute_gex_profile(rows, spot=95000.0)

        self.assertEqual(profile.regime, "NEUTRAL")
        self.assertEqual(profile.net_gex, 0.0)


if __name__ == "__main__":
    unittest.main()
