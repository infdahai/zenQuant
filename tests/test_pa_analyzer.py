import unittest

from zenquant.domain.models import Candle, PAPattern
from zenquant.pa_analyzer import PAAnalyzer, PAAnalyzerConfig


class PAAnalyzerTests(unittest.TestCase):
    def test_h1_then_h2_signal(self) -> None:
        analyzer = PAAnalyzer(PAAnalyzerConfig(ema_period=3, atr_period=3, micro_channel_len=10))
        candles = [
            Candle("BTC_USDT", 1, 100.0, 101.0, 99.0, 100.0, 1.0),
            Candle("BTC_USDT", 2, 100.0, 102.0, 100.0, 101.5, 1.0),
            Candle("BTC_USDT", 3, 101.5, 103.0, 101.0, 102.6, 1.0),
            Candle("BTC_USDT", 4, 102.6, 102.8, 100.7, 101.2, 1.0),
            Candle("BTC_USDT", 5, 101.2, 103.2, 100.8, 102.7, 1.0),
            Candle("BTC_USDT", 6, 102.7, 102.9, 100.4, 101.3, 1.0),
            Candle("BTC_USDT", 7, 101.3, 103.4, 100.2, 102.9, 1.0),
        ]

        patterns = [analyzer.update(candle) for candle in candles]
        self.assertIn(PAPattern.H1_PULLBACK, patterns)
        self.assertIn(PAPattern.H2_RESUMPTION, patterns)

    def test_major_trend_reversal(self) -> None:
        analyzer = PAAnalyzer(PAAnalyzerConfig(ema_period=3, atr_period=3, micro_channel_len=10))
        candles = [
            Candle("BTC_USDT", 1, 100.0, 101.0, 99.0, 100.0, 1.0),
            Candle("BTC_USDT", 2, 100.0, 103.0, 100.0, 102.0, 1.0),
            Candle("BTC_USDT", 3, 102.0, 104.0, 101.0, 103.0, 1.0),
            Candle("BTC_USDT", 4, 103.0, 105.0, 102.0, 104.0, 1.0),
            Candle("BTC_USDT", 5, 104.0, 104.9, 98.0, 99.0, 1.0),
        ]

        patterns = [analyzer.update(candle) for candle in candles]
        self.assertEqual(patterns[-1], PAPattern.MAJOR_TREND_REVERSAL)


if __name__ == "__main__":
    unittest.main()

