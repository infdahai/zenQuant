import tempfile
import unittest
from pathlib import Path

from zenquant.domain import Candle, IndicatorPack, MarketSnapshot, Timeframe
from zenquant.indicator import IndicatorEngine, IndicatorEngineConfig, IndicatorStore
from zenquant.infra import InMemoryJetStream
from zenquant.research.microstructure import (
    MicrostructureFeatureStore,
    MicrostructureFeatureStoreConfig,
    write_microstructure_sidecar_features,
)


def _snapshot_row(ts_ns: int, mid: float, *, bid_size: float, ask_size: float, sequence: int) -> dict[str, object]:
    half_spread = 0.5
    bid = mid - half_spread
    ask = mid + half_spread
    return {
        "ts_ns": ts_ns,
        "venue": "bybit",
        "symbol": "BTCUSDT",
        "sequence": sequence,
        "bid_prices": f"[{bid}, {bid - 1.0}]",
        "bid_sizes": f"[{bid_size}, {bid_size * 0.8}]",
        "ask_prices": f"[{ask}, {ask + 1.0}]",
        "ask_sizes": f"[{ask_size}, {ask_size * 0.8}]",
    }


def _trade_row(ts_ns: int, price: float, side: str, size: float) -> dict[str, object]:
    return {
        "ts_ns": ts_ns,
        "venue": "bybit",
        "symbol": "BTCUSDT",
        "price": price,
        "size": size,
        "side": side,
    }


class MicrostructureIndicatorIntegrationTests(unittest.TestCase):
    def test_indicator_engine_injects_risk_only_sidecar_values(self) -> None:
        import pandas as pd

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw_root = root / "raw"
            feature_root = root / "sidecar"
            raw_root.mkdir(parents=True, exist_ok=True)

            base_ns = 1_700_000_000_000_000_000
            snapshots = []
            trades = []
            mid = 50_000.0
            for idx in range(8):
                ts_ns = base_ns + idx * 1_000_000_000
                mid += 2.0
                snapshots.append(
                    _snapshot_row(ts_ns, mid, bid_size=12.0 + idx, ask_size=18.0 - idx * 0.5, sequence=idx)
                )
                trades.append(_trade_row(ts_ns + 100_000_000, mid, "buy" if idx % 2 == 0 else "sell", 1.0))

            pd.DataFrame(snapshots).to_parquet(raw_root / "snapshots.parquet", index=False)
            pd.DataFrame(trades).to_parquet(raw_root / "trades.parquet", index=False)
            write_microstructure_sidecar_features(
                data_root=raw_root,
                venue="bybit",
                symbol="BTCUSDT",
                depth=20,
                cadence_ms=1000,
                feature_output_root=feature_root,
            )

            store = MicrostructureFeatureStore(
                MicrostructureFeatureStoreConfig(
                    data_dir=feature_root / "bybit" / "BTCUSDT" / "1000ms",
                    max_staleness_ns=4 * 3600 * 1_000_000_000,
                )
            )
            store.load()

            bus = InMemoryJetStream()
            indicator_store = IndicatorStore()
            engine = IndicatorEngine(
                bus=bus,
                store=indicator_store,
                config=IndicatorEngineConfig(expected_symbols=("BTCUSDT_PERP",)),
                microstructure_store=store,
            )
            self.assertIsNotNone(engine)

            packs: list[IndicatorPack] = []
            bus.subscribe(
                "indicator.>",
                lambda _subject, payload: packs.append(payload) if isinstance(payload, IndicatorPack) else None,
            )

            cutoff_ns = base_ns + 7 * 1_000_000_000
            bus.publish(
                "md.candle.4h.BTCUSDT_PERP",
                Candle(
                    symbol="BTCUSDT_PERP",
                    timeframe=Timeframe.TF_4H,
                    bar_start_ns=cutoff_ns - Timeframe.TF_4H.seconds * 1_000_000_000,
                    bar_end_ns=cutoff_ns,
                    open=50_000.0,
                    high=50_100.0,
                    low=49_900.0,
                    close=50_050.0,
                    volume=120.0,
                    trade_count=25,
                ),
            )
            bus.publish(
                "md.snapshot.4h.BTCUSDT_PERP",
                MarketSnapshot(
                    symbol="BTCUSDT_PERP",
                    timeframe=Timeframe.TF_4H,
                    cutoff_time_ns=cutoff_ns,
                    last_price=50_050.0,
                    bid_depth=100.0,
                    ask_depth=90.0,
                    book_age_ms=50,
                    venue_count=1,
                    cvd_delta=12.0,
                    funding_oi_weighted=0.01,
                    oi_coverage=1.0,
                    staleness_ms=0,
                    quality_flags=["OK"],
                ),
            )

        self.assertEqual(len(packs), 1)
        pack = packs[0]
        self.assertIn("ms_toxicity_proxy", pack.values)
        self.assertIn("ms_liquidity_vacuum_score", pack.values)
        self.assertTrue(bool(pack.values["ms_sidecar_ready"]))
        self.assertFalse(bool(pack.values["ms_sidecar_stale"]))


if __name__ == "__main__":
    unittest.main()
