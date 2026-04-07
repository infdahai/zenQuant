import unittest
from pathlib import Path

from zenquant.domain import IndicatorCoverage, IndicatorPack, PlaybookSelection, TargetPosition, Timeframe
from zenquant.infra import InMemoryJetStream
from zenquant.plugins.profile import build_from_profile
from zenquant.state import StateEngine
from zenquant.strategy import ARCStrategyEngine


def _grind_pack(cutoff_time_ns: int, momentum_roc_z: float, cvd_slope: float, zen_pulse: float) -> IndicatorPack:
    return IndicatorPack(
        ts_ns=cutoff_time_ns,
        symbol="BTCUSDT_PERP",
        timeframe=Timeframe.TF_4H,
        cutoff_time_ns=cutoff_time_ns,
        indicator_version="pack.v1",
        formula_hash="sha256:test",
        quality_flags=["OK"],
        coverage=IndicatorCoverage(venue_count=1, oi_coverage=1.0, staleness_ms=0),
        values={
            "realized_vol": 0.9,
            "cvd_slope": cvd_slope,
            "funding_oi_z": -0.2,
            "ob_liq_z": 0.0,
            "zen_pulse": zen_pulse,
            "last_price": 28000.0,
            "atr": 600.0,
            "structure_state": "NEUTRAL",
            "momentum_roc_z": momentum_roc_z,
            "cycle_position": 0.98,
        },
        components={},
    )


class August2023BearishGrindTests(unittest.TestCase):
    def test_grind_sequence_routes_to_bear_defense_instead_of_range_light(self) -> None:
        bus = InMemoryJetStream()
        _, registry = build_from_profile(
            Path("config/profiles/default.toml"),
            plugin_root=Path("zenquant/plugins"),
        )
        ARCStrategyEngine(bus, plugin_registry=registry)
        StateEngine(bus)

        playbooks: list[PlaybookSelection] = []
        targets: list[TargetPosition] = []
        bus.subscribe(
            "strategy.playbook.>",
            lambda _subject, payload: playbooks.append(payload) if isinstance(payload, PlaybookSelection) else None,
        )
        bus.subscribe(
            "intent.target_position.arc.BTCUSDT_PERP",
            lambda _subject, payload: targets.append(payload) if isinstance(payload, TargetPosition) else None,
        )

        for idx, values in enumerate(
            [
                (-0.14, -0.020, 49.0),
                (-0.17, -0.028, 48.5),
                (-0.20, -0.032, 47.8),
                (-0.19, -0.030, 47.5),
            ],
            start=1,
        ):
            bus.publish(
                "indicator.pack.v1.4h.BTCUSDT_PERP",
                _grind_pack(idx, values[0], values[1], values[2]),
            )

        self.assertGreaterEqual(len(playbooks), 4)
        grind_playbooks = [playbook.playbook_id for playbook in playbooks[-4:]]
        self.assertGreaterEqual(grind_playbooks.count("BEAR_DEFENSE"), 2)
        self.assertLess(grind_playbooks.count("RANGE_LIGHT"), 4)
        self.assertTrue(any(target.target_qty < 0 for target in targets[-4:]))


if __name__ == "__main__":
    unittest.main()
