package research

import (
	"fmt"
	"math"
	"strconv"
)

// SizingConfig holds the parameters of the offline Python sizing reference.
type SizingConfig struct {
	TargetVol5m                 float64 `json:"target_vol_5m"`
	TargetVol1h                 float64 `json:"target_vol_1h"`
	TargetVol4h                 float64 `json:"target_vol_4h"`
	TargetVol1d                 float64 `json:"target_vol_1d"`
	MinRealizedVolRatio         float64 `json:"min_realized_vol_ratio"`
	CrowdingReasonThreshold     float64 `json:"crowding_reason_threshold"`
	PositioningDiscountMax      float64 `json:"positioning_discount_max"`
	PositioningDiscountFloor    float64 `json:"positioning_discount_floor"`
	VolatilityDistortedMultiple float64 `json:"volatility_distorted_multiple"`
	VolatilityHighMultiple      float64 `json:"volatility_high_multiple"`
	VolatilityDistortedDiscount float64 `json:"volatility_distorted_discount"`
	VolatilityHighDiscount      float64 `json:"volatility_high_discount"`
}

func DefaultSizingConfig() SizingConfig {
	return SizingConfig{0.45, 0.55, 0.60, 0.50, 0.25, 1.8, 0.65, 0.35, 2.8, 1.5, 0.75, 0.9}
}

// SizingState contains the MarketState fields used by sizing.
type SizingState struct {
	Symbol                string  `json:"symbol"`
	Timeframe             string  `json:"timeframe"`
	CutoffTimeNS          int64   `json:"cutoff_time_ns"`
	DirectionalConfidence float64 `json:"directional_confidence"`
	VolatilityState       string  `json:"volatility_state"`
	VolatilityConfidence  float64 `json:"volatility_confidence"`
	PositioningConfidence float64 `json:"positioning_confidence"`
	QualityState          string  `json:"quality_state"`
	QualityConfidence     float64 `json:"quality_confidence"`
}

type SizingPlaybook struct {
	PlaybookConfidence float64 `json:"playbook_confidence"`
	MaxLeverage        float64 `json:"max_leverage"`
}

type SizingIndicators struct {
	RealizedVol float64 `json:"realized_vol"`
	FundingOIZ  float64 `json:"funding_oi_z"`
}

type SizingDecision struct {
	Symbol               string   `json:"symbol"`
	Timeframe            string   `json:"timeframe"`
	CutoffTimeNS         int64    `json:"cutoff_time_ns"`
	BaseSize             float64  `json:"base_size"`
	ConfidenceMultiplier float64  `json:"confidence_multiplier"`
	StressDiscount       float64  `json:"stress_discount"`
	ExecutionHaircut     float64  `json:"execution_haircut"`
	FinalLeverage        float64  `json:"final_leverage"`
	ReasonCodes          []string `json:"reason_codes"`
}

// ComputeSizing reproduces the Python sizing path for finite inputs and finite
// intermediate results with a nonnegative confidence product. It returns an
// error and a zero decision for unsupported values, including intermediate
// overflow. Equity is validated but does not affect the leverage calculation.
func ComputeSizing(state SizingState, playbook SizingPlaybook, equity float64, indicators SizingIndicators, config SizingConfig) (SizingDecision, error) {
	inputs := []float64{equity, state.DirectionalConfidence, state.VolatilityConfidence, state.PositioningConfidence, state.QualityConfidence,
		playbook.PlaybookConfidence, playbook.MaxLeverage, indicators.RealizedVol, indicators.FundingOIZ,
		config.TargetVol5m, config.TargetVol1h, config.TargetVol4h, config.TargetVol1d, config.MinRealizedVolRatio,
		config.CrowdingReasonThreshold, config.PositioningDiscountMax, config.PositioningDiscountFloor,
		config.VolatilityDistortedMultiple, config.VolatilityHighMultiple, config.VolatilityDistortedDiscount, config.VolatilityHighDiscount}
	for _, value := range inputs {
		if !sizingFinite(value) {
			return SizingDecision{}, fmt.Errorf("sizing input must be finite")
		}
	}
	target := config.TargetVol4h
	switch state.Timeframe {
	case "5m":
		target = config.TargetVol5m
	case "1h":
		target = config.TargetVol1h
	case "1d":
		target = config.TargetVol1d
	}
	floor := target * config.MinRealizedVolRatio
	base := target / math.Max(math.Max(indicators.RealizedVol, floor), 1e-9)
	product := state.DirectionalConfidence * state.VolatilityConfidence
	if !sizingFinite(product) {
		return SizingDecision{}, fmt.Errorf("sizing confidence product overflow")
	}
	product *= state.PositioningConfidence
	if !sizingFinite(product) {
		return SizingDecision{}, fmt.Errorf("sizing confidence product overflow")
	}
	product *= state.QualityConfidence
	if !sizingFinite(product) || product < 0 {
		return SizingDecision{}, fmt.Errorf("sizing confidence product must be finite and nonnegative")
	}
	blend := math.Pow(product, 0.25) + playbook.PlaybookConfidence
	if !sizingFinite(floor) || !sizingFinite(base) || !sizingFinite(blend) {
		return SizingDecision{}, fmt.Errorf("sizing intermediate overflow")
	}
	confidence := math.Max(0, math.Min(1, blend/2))
	positioning := math.Max(config.PositioningDiscountFloor, 1-math.Min(config.PositioningDiscountMax, math.Abs(indicators.FundingOIZ)/4))
	volatility := 1.0
	stressTarget := math.Max(target, 1e-9)
	distortedThreshold := stressTarget * config.VolatilityDistortedMultiple
	highThreshold := stressTarget * config.VolatilityHighMultiple
	if !sizingFinite(distortedThreshold) || !sizingFinite(highThreshold) {
		return SizingDecision{}, fmt.Errorf("sizing volatility threshold overflow")
	}
	if indicators.RealizedVol > distortedThreshold || state.VolatilityState == "DISTORTED" {
		volatility = config.VolatilityDistortedDiscount
	} else if indicators.RealizedVol > highThreshold || state.VolatilityState == "HIGH" {
		volatility = config.VolatilityHighDiscount
	}
	quality, haircut := 0.9, 0.7
	switch state.QualityState {
	case "TRADABLE":
		quality, haircut = 1, 1
	case "NOISY":
		quality, haircut = 1, 0.9
	case "UNSAFE":
		quality, haircut = 0, 0
	}
	stressProduct := positioning * volatility
	if !sizingFinite(stressProduct) {
		return SizingDecision{}, fmt.Errorf("sizing stress overflow")
	}
	stressProduct *= quality
	if !sizingFinite(stressProduct) {
		return SizingDecision{}, fmt.Errorf("sizing stress overflow")
	}
	stress := math.Max(0, stressProduct)
	unclamped := base * confidence
	if !sizingFinite(unclamped) {
		return SizingDecision{}, fmt.Errorf("sizing leverage overflow")
	}
	unclamped *= stress
	if !sizingFinite(unclamped) {
		return SizingDecision{}, fmt.Errorf("sizing leverage overflow")
	}
	unclamped *= haircut
	if !sizingFinite(unclamped) {
		return SizingDecision{}, fmt.Errorf("sizing leverage overflow")
	}
	final := math.Min(playbook.MaxLeverage, unclamped)
	reasons := []string{"SIZE_BASE_VOL_TARGET", "SIZE_CONFIDENCE_BLEND"}
	if math.Abs(indicators.FundingOIZ) > config.CrowdingReasonThreshold {
		reasons = append(reasons, "CROWDING_DISCOUNT")
	}
	if state.VolatilityState == "DISTORTED" {
		reasons = append(reasons, "SIZE_VOLATILITY_STRESS")
	}
	if state.QualityState == "NOISY" || state.QualityState == "DEGRADED" || state.QualityState == "UNSAFE" {
		reasons = append(reasons, "QUALITY_DEGRADED")
	}
	if haircut < 1 {
		reasons = append(reasons, "SIZE_EXECUTION_HAIRCUT")
	}
	if final < unclamped-1e-9 {
		reasons = append(reasons, "SIZE_MAX_LEVERAGE_CAP")
	}
	return SizingDecision{state.Symbol, state.Timeframe, state.CutoffTimeNS, sizingRound(base, 6), sizingRound(confidence, 6), sizingRound(stress, 6), sizingRound(haircut, 6), sizingRound(final, 4), reasons}, nil
}

func sizingFinite(value float64) bool {
	return !math.IsNaN(value) && !math.IsInf(value, 0)
}

// Formatting rounds the original binary float to decimal ties-to-even, like Python round.
func sizingRound(value float64, digits int) float64 {
	result, _ := strconv.ParseFloat(strconv.FormatFloat(value, 'f', digits, 64), 64)
	return result
}
