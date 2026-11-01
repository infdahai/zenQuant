package research

import (
	"fmt"
	"math"
)

// StateEngineInput is one complete, point-in-time indicator snapshot. Values must
// contain every named indicator used below, even when its observed value is zero.
// AvailableTimeNS is when the complete snapshot became available.
type StateEngineInput struct {
	Symbol           string             `json:"symbol"`
	Timeframe        string             `json:"timeframe"`
	CutoffTimeNS     int64              `json:"cutoff_time_ns"`
	AvailableTimeNS  int64              `json:"available_time_ns"`
	Values           map[string]float64 `json:"values"`
	StructureState   string             `json:"structure_state"`
	LiquidationStale *bool              `json:"liq_stale"`
	QualityFlags     []string           `json:"quality_flags"`
}

// ResearchMarketState is the supported subset of Python's MarketState.
type ResearchMarketState struct {
	PlaybookState
	Regime           string  `json:"regime"`
	RegimeConfidence float64 `json:"regime_confidence"`
}

type stateSequenceKey struct{ symbol, timeframe string }
type stateSequence struct {
	cutoff, available int64
	direction, regime string
}

// StateEngine is an in-memory, single-owner research classifier. It does not
// synthesize missing bars or support concurrent calls.
type StateEngine struct {
	sequences          map[stateSequenceKey]stateSequence
	liquidationOverlay bool
}

func NewStateEngine(enableLiquidationOverlay bool) *StateEngine {
	return &StateEngine{sequences: make(map[stateSequenceKey]stateSequence), liquidationOverlay: enableLiquidationOverlay}
}

var stateIndicatorNames = []string{"realized_vol", "vix_z", "funding_oi_z", "cvd_slope", "zen_pulse", "momentum_roc_z", "liq_intensity_z", "liq_imbalance", "yield_10y_z", "cycle_position"}

// Classify rejects incomplete, future, duplicate, and out-of-order snapshots
// without modifying sequence state. Times are positive nanoseconds; cutoff is
// the decision time and availability must not exceed it.
func (e *StateEngine) Classify(in StateEngineInput) (ResearchMarketState, error) {
	if e == nil || e.sequences == nil {
		return ResearchMarketState{}, fmt.Errorf("state engine must be initialized")
	}
	if in.Symbol == "" || (in.Timeframe != "5m" && in.Timeframe != "1h" && in.Timeframe != "4h" && in.Timeframe != "1d") || in.CutoffTimeNS <= 0 || in.AvailableTimeNS <= 0 || in.AvailableTimeNS > in.CutoffTimeNS || in.LiquidationStale == nil || in.QualityFlags == nil {
		return ResearchMarketState{}, fmt.Errorf("invalid identity, time, or missing quality inputs")
	}
	if in.StructureState != "UPTREND" && in.StructureState != "DOWNTREND" && in.StructureState != "NEUTRAL" {
		return ResearchMarketState{}, fmt.Errorf("invalid structure state")
	}
	for _, name := range stateIndicatorNames {
		v, ok := in.Values[name]
		if !ok || !finite(v) {
			return ResearchMarketState{}, fmt.Errorf("missing or non-finite indicator %s", name)
		}
	}
	key := stateSequenceKey{in.Symbol, in.Timeframe}
	prev, exists := e.sequences[key]
	if exists && (in.CutoffTimeNS <= prev.cutoff || in.AvailableTimeNS < prev.available) {
		return ResearchMarketState{}, fmt.Errorf("snapshot time is out of order")
	}
	if !exists {
		prev.direction, prev.regime = "NEUTRAL", "RANGE"
	}
	v := in.Values
	// Python _extract_values uses `or` for these two fields.
	pulse, cycle := v["zen_pulse"], v["cycle_position"]
	if pulse == 0 {
		pulse = 50
	}
	if cycle == 0 {
		cycle = 1
	}
	intensity, imbalance, stale := v["liq_intensity_z"], v["liq_imbalance"], *in.LiquidationStale
	if !e.liquidationOverlay {
		intensity, imbalance, stale = 0, 0, true
	}
	momentum, cvd := v["momentum_roc_z"], v["cvd_slope"]
	grind := momentum <= -0.18 && cvd <= -0.025 && pulse <= 49 && cycle <= 1.15
	transition := momentum <= -0.10 && cvd <= -0.015 && pulse <= 50 && cycle <= 1.20
	liquidationBear := !stale && intensity >= 2.2 && imbalance >= 0.2
	structure := in.StructureState
	var direction string
	var confidence, signal float64
	switch {
	case structure == "UPTREND" && momentum > 0.5:
		direction, confidence, signal = "UPTREND", math.Min(1, 0.55+math.Min(0.35, momentum/4)), 1
	case structure == "DOWNTREND" && momentum < -0.5:
		direction, confidence, signal = "DOWNTREND", math.Min(1, 0.55+math.Min(0.35, math.Abs(momentum)/4)), -1
	case structure == "DOWNTREND" && grind:
		direction, confidence, signal = "DOWNTREND", math.Min(1, 0.58+math.Min(0.18, math.Abs(momentum)/5)), -0.9
		if liquidationBear {
			confidence = math.Min(1, confidence+0.12)
		}
	case liquidationBear && transition:
		direction, confidence, signal = "DOWNTREND", math.Min(1, 0.60+math.Min(0.15, intensity/10)), -0.9
	case prev.direction == "UPTREND" && structure == "UPTREND" && momentum > 0.2:
		direction, confidence, signal = "UPTREND", 0.5, 1
	case prev.direction == "DOWNTREND" && structure == "DOWNTREND" && momentum < -0.2:
		direction, confidence, signal = "DOWNTREND", 0.5, -1
	case prev.direction == "DOWNTREND" && (structure == "DOWNTREND" || structure == "NEUTRAL") && transition:
		direction, confidence, signal = "DOWNTREND", 0.52, -0.8
		if liquidationBear {
			confidence = 0.60
		}
	case structure == "NEUTRAL" && math.Abs(momentum) < 0.3:
		if transition {
			direction, confidence, signal = "TRANSITION", 0.56, -0.65
			if liquidationBear {
				confidence, signal = 0.64, -0.70
			}
		} else {
			direction, confidence, signal = "NEUTRAL", 0.75, 0
		}
	case transition:
		direction, confidence, signal = "TRANSITION", 0.52, -0.55
		if liquidationBear {
			confidence, signal = 0.60, -0.70
		}
	default:
		direction, confidence, signal = "TRANSITION", 0.45, 0
	}
	state, err := ClassifyStateDimensions(StateDimensionsInput{RealizedVol: v["realized_vol"], VIXZ: v["vix_z"], FundingOIZ: v["funding_oi_z"], QualityFlags: in.QualityFlags}, PlaybookState{Symbol: in.Symbol, Timeframe: in.Timeframe, CutoffTimeNS: in.CutoffTimeNS, DirectionalState: direction, DirectionalConfidence: confidence, DirectionSignal: signal})
	if err != nil {
		return ResearchMarketState{}, err
	}
	degraded := false
	for _, flag := range in.QualityFlags {
		if flag == "BOOK_STALE" || flag == "VENUE_DOWN" || flag == "DATA_STALE" || flag == "DATA_FUTURE" {
			degraded = true
		}
	}
	var regime string
	var regimeConfidence float64
	trendStrength := math.Min(1, math.Abs(cvd)*10)
	pulseStrength := math.Min(1, math.Max(0, (pulse-50)/50))
	switch {
	case degraded || v["realized_vol"] > 2.8:
		regime, regimeConfidence = "STRESS", 0.9
	case !stale && intensity >= 2.4 && math.Abs(imbalance) >= 0.2:
		regime, regimeConfidence = "STRESS", math.Min(0.95, 0.78+math.Min(0.12, intensity/12))
	case (prev.regime == "TREND" && trendStrength > 0.12 && pulse >= 50) || (prev.regime != "TREND" && trendStrength > 0.22 && pulse >= 58):
		regime, regimeConfidence = "TREND", math.Min(1, 0.45+trendStrength*0.4+pulseStrength*0.15)
		if math.Abs(v["yield_10y_z"]) > 2 {
			regimeConfidence *= 0.9
		}
		if cycle > 1.3 {
			regimeConfidence *= 0.8
		} else if cycle < 0.8 {
			regimeConfidence *= 0.85
		}
	default:
		regime, regimeConfidence = "RANGE", math.Max(0.35, 1-math.Min(0.6, math.Abs(cvd)*5))
	}
	e.sequences[key] = stateSequence{in.CutoffTimeNS, in.AvailableTimeNS, direction, regime}
	return ResearchMarketState{state, regime, sizingRound(regimeConfidence, 4)}, nil
}
