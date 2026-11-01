package research

import "fmt"

// CandleObservation is a research-only observation at a confirmed interval end.
// Source identifies the caller's historical dataset; arrival time is unknown and
// must not be inferred from CutoffTimeNS. Values match the Python helpers during
// warmup; readiness flags indicate a full lookback, not whether a value is nonzero.
// These four values are not a complete indicator pack or a trading decision.
type CandleObservation struct {
	Symbol, Timeframe, Source                                   string
	CutoffTimeNS                                                int64
	HistoricalArrivalTimeNS                                     *int64
	ATR, RealizedVol, RateOfChange, OUKappa                     float64
	ATRReady, RealizedVolReady, RateOfChangeReady, OUKappaReady bool
}

// CandleObservations computes the Python default ATR14, realized volatility20,
// raw ROC12 and OU20 from a single contiguous historical series. Full windows
// require respectively 15, 21, 13 and 21 candles. Values use only candles at
// or before their normalized interval-end cutoff. Historical arrival is unknown.
// Inputs are never modified. Empty input returns an empty stream.
func CandleObservations(candles []ReplayCandle, source string) ([]CandleObservation, error) {
	if source == "" {
		return nil, fmt.Errorf("source is required")
	}
	if len(candles) == 0 {
		return []CandleObservation{}, nil
	}
	tf := candles[0].Timeframe
	events, err := PrepareReplayEvents(map[string][]ReplayCandle{tf: candles}, []string{tf}, tf, nil, nil)
	if err != nil {
		return nil, err
	}
	// PrepareReplayEvents sorts; check the original order and continuity before
	// using its validated and normalized copies.
	for i := 1; i < len(candles); i++ {
		if candles[i].OpenTimeNS != events[i-1].CloseTimeNS || candles[i-1].OpenTimeNS != events[i-1].OpenTimeNS {
			return nil, fmt.Errorf("candles must be ordered, unique and contiguous at index %d", i)
		}
	}
	out := make([]CandleObservation, 0, len(events))
	window := make([]IndicatorCandle, 0, 21)
	for _, event := range events {
		if len(window) == 21 {
			window = window[1:]
		}
		window = append(window, IndicatorCandle{High: event.High, Low: event.Low, Close: event.Close})
		obs := CandleObservation{Symbol: event.Symbol, Timeframe: tf, Source: source, CutoffTimeNS: event.CloseTimeNS}
		var err error
		if obs.ATR, err = OfflineATR(window, 14); err != nil {
			return nil, fmt.Errorf("ATR at %d: %w", event.CloseTimeNS, err)
		}
		if obs.RealizedVol, err = OfflineRealizedVol(window, 20, tf); err != nil {
			return nil, fmt.Errorf("realized volatility at %d: %w", event.CloseTimeNS, err)
		}
		if obs.OUKappa, err = OfflineOUKappa(window, 20); err != nil {
			return nil, fmt.Errorf("OU kappa at %d: %w", event.CloseTimeNS, err)
		}
		if obs.RateOfChange, err = OfflineRateOfChange(window, 12); err != nil {
			return nil, fmt.Errorf("rate of change at %d: %w", event.CloseTimeNS, err)
		}
		obs.ATRReady = len(window) >= 15
		obs.RealizedVolReady = len(window) >= 21
		obs.RateOfChangeReady = len(window) >= 13
		obs.OUKappaReady = len(window) >= 21
		out = append(out, obs)
	}
	return out, nil
}
