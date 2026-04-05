"""Cross-asset information flow signal (ETH → BTC Transfer Entropy).

ETH has thinner orderbook → reacts faster to liquidity shocks.
When TE(ETH→BTC) is high, ETH is "leading" BTC → regime transition signal.
Direction: follow ETH's recent momentum as BTC predictor.

Academic basis: Assaf et al. (2024), Keshari et al. (2024)
"""

from __future__ import annotations

from typing import Any

from zenquant.plugins.protocols import PluginMeta, SignalResult


meta = PluginMeta(
    name="cross_asset_flow_signal",
    version="1.0.0",
    plugin_type="signal",
    description="ETH→BTC information flow as regime transition signal",
)


def compute(
    indicators: dict[str, Any],
    state: dict[str, Any],
    config: dict[str, Any],
) -> SignalResult:
    te_score = float(indicators.get("te_eth_to_btc", 0.0) or 0.0)
    eth_momentum = float(indicators.get("eth_momentum", 0.0) or 0.0)

    # No ETH data → neutral
    if te_score == 0.0:
        return SignalResult(value=0.0, confidence=0.0)

    te_threshold = config.get("te_threshold", 0.1)

    # High TE = ETH is leading BTC
    if te_score > te_threshold:
        # Follow ETH's direction as BTC predictor
        if eth_momentum > 0.01:
            magnitude = min(0.6, 0.3 + te_score)
            confidence = min(0.7, 0.3 + te_score * 2)
            return SignalResult(
                value=magnitude,
                confidence=confidence,
                reason_codes=("ETH_LEADING_BULLISH",),
            )
        elif eth_momentum < -0.01:
            magnitude = -min(0.6, 0.3 + te_score)
            confidence = min(0.7, 0.3 + te_score * 2)
            return SignalResult(
                value=magnitude,
                confidence=confidence,
                reason_codes=("ETH_LEADING_BEARISH",),
            )

    return SignalResult(value=0.0, confidence=0.1)
