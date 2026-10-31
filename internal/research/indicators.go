package research

import (
	"fmt"
	"math"
)

// IndicatorCandle contains the prices needed by the offline indicators.
type IndicatorCandle struct {
	High  float64 `json:"high"`
	Low   float64 `json:"low"`
	Close float64 `json:"close"`
}

// MaxIndicatorWindow bounds indicator work and temporary memory.
const MaxIndicatorWindow = 1_000_000

func indicatorSubset(candles []IndicatorCandle, window int) ([]IndicatorCandle, error) {
	if window < 1 || window > MaxIndicatorWindow {
		return nil, fmt.Errorf("indicator window must be in [1, %d]", MaxIndicatorWindow)
	}
	if len(candles) > MaxIndicatorWindow+1 {
		return nil, fmt.Errorf("too many indicator candles")
	}
	// Validate the complete input, including observations outside the window.
	for _, c := range candles {
		if !finite(c.High) || !finite(c.Low) || !finite(c.Close) {
			return nil, fmt.Errorf("indicator prices must be finite")
		}
	}
	if len(candles) > window+1 {
		candles = candles[len(candles)-window-1:]
	}
	return candles, nil
}

// OfflineATR returns the arithmetic mean of true ranges in the trailing window.
func OfflineATR(candles []IndicatorCandle, window int) (float64, error) {
	cs, err := indicatorSubset(candles, window)
	if err != nil || len(cs) < 2 {
		return 0, err
	}
	sum := 0.0
	for i := 1; i < len(cs); i++ {
		a, b, c := cs[i].High-cs[i].Low, cs[i].High-cs[i-1].Close, cs[i].Low-cs[i-1].Close
		if !finite(a) || !finite(b) || !finite(c) {
			return 0, fmt.Errorf("true range overflow")
		}
		sum += math.Max(a, math.Max(math.Abs(b), math.Abs(c)))
		if !finite(sum) {
			return 0, fmt.Errorf("true range sum overflow")
		}
	}
	return sum / float64(len(cs)-1), nil
}

// OfflineRealizedVol returns annualized sample standard deviation of log returns.
// Nonpositive closes invalidate their adjacent returns; unknown timeframes use 4h.
func OfflineRealizedVol(candles []IndicatorCandle, window int, timeframe string) (float64, error) {
	cs, err := indicatorSubset(candles, window)
	if err != nil {
		return 0, err
	}
	returns := make([]float64, 0, len(cs))
	sum := 0.0
	for i := 1; i < len(cs); i++ {
		prior, now := cs[i-1].Close, cs[i].Close
		if prior <= 0 || now <= 0 {
			continue
		}
		ratio := now / prior
		if !finite(ratio) || ratio == 0 {
			return 0, fmt.Errorf("return ratio overflow or underflow")
		}
		r := math.Log(ratio)
		sum += r
		if !finite(r) || !finite(sum) {
			return 0, fmt.Errorf("log return overflow")
		}
		returns = append(returns, r)
	}
	if len(returns) < 2 {
		return 0, nil
	}
	mean := sum / float64(len(returns))
	variance := 0.0
	for _, r := range returns {
		d := r - mean
		variance += d * d
		if !finite(variance) {
			return 0, fmt.Errorf("variance overflow")
		}
	}
	bars := 6.0
	switch timeframe {
	case "5m":
		bars = 288
	case "1h":
		bars = 24
	case "1d":
		bars = 1
	}
	result := math.Sqrt(variance/float64(len(returns)-1)) * math.Sqrt(365*bars)
	if !finite(result) {
		return 0, fmt.Errorf("annualized volatility overflow")
	}
	return result, nil
}

// OfflineRateOfChange returns the close ratio over lookback minus one.
func OfflineRateOfChange(candles []IndicatorCandle, lookback int) (float64, error) {
	cs, err := indicatorSubset(candles, lookback)
	if err != nil || len(cs) < lookback+1 {
		return 0, err
	}
	then, now := cs[0].Close, cs[len(cs)-1].Close
	if then <= 0 || now <= 0 {
		return 0, nil
	}
	result := now/then - 1
	if !finite(result) {
		return 0, fmt.Errorf("rate of change overflow")
	}
	return result, nil
}
