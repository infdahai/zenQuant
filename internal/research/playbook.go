package research

import (
	"fmt"
	"math"
)

// PlaybookState is the subset of MarketState used by offline selection.
type PlaybookState struct {
	Symbol                string  `json:"symbol"`
	Timeframe             string  `json:"timeframe"`
	CutoffTimeNS          int64   `json:"cutoff_time_ns"`
	DirectionalState      string  `json:"directional_state"`
	DirectionalConfidence float64 `json:"directional_confidence"`
	VolatilityState       string  `json:"volatility_state"`
	VolatilityConfidence  float64 `json:"volatility_confidence"`
	PositioningState      string  `json:"positioning_state"`
	PositioningConfidence float64 `json:"positioning_confidence"`
	QualityState          string  `json:"quality_state"`
	QualityConfidence     float64 `json:"quality_confidence"`
	DirectionSignal       float64 `json:"direction_signal"`
	TransitionRisk        float64 `json:"transition_risk"`
}

// PlaybookConfig is a complete configuration value; fields are not defaulted.
// Start with DefaultPlaybookConfig and modify its fields to override defaults.
type PlaybookConfig struct {
	PreserveCapitalTransitionRisk       float64 `json:"preserve_capital_transition_risk"`
	PreserveCapitalMinOverallConfidence float64 `json:"preserve_capital_min_overall_confidence"`
	TradeDirectionalConfidenceFloor     float64 `json:"trade_directional_confidence_floor"`
	BearishOverrideConfidenceFloor      float64 `json:"bearish_override_confidence_floor"`
	BearishOverrideDirectionSignal      float64 `json:"bearish_override_direction_signal"`
	BullMaxLeverage                     float64 `json:"bull_max_leverage"`
	BearMaxLeverage                     float64 `json:"bear_max_leverage"`
	RangeMaxLeverage                    float64 `json:"range_max_leverage"`
	PreserveCapitalMaxLeverage          float64 `json:"preserve_capital_max_leverage"`
	BullTrailingStopATRMult             float64 `json:"bull_trailing_stop_atr_mult"`
	BearTrailingStopATRMult             float64 `json:"bear_trailing_stop_atr_mult"`
	RangeTrailingStopATRMult            float64 `json:"range_trailing_stop_atr_mult"`
	PreserveCapitalTrailingStopATRMult  float64 `json:"preserve_capital_trailing_stop_atr_mult"`
	RangeTimeDecayBars                  int     `json:"range_time_decay_bars"`
	PreserveCapitalTimeDecayBars        int     `json:"preserve_capital_time_decay_bars"`
}

func DefaultPlaybookConfig() PlaybookConfig {
	return PlaybookConfig{0.6, 0.35, 0.5, 0.5, -0.5, 2.5, 2.25, 1.5, 0.35, 2.5, 2.5, 2.5, 1.5, 3, 1}
}

type PlaybookSelection struct {
	Symbol              string    `json:"symbol"`
	Timeframe           string    `json:"timeframe"`
	CutoffTimeNS        int64     `json:"cutoff_time_ns"`
	PlaybookID          string    `json:"playbook_id"`
	PlaybookConfidence  float64   `json:"playbook_confidence"`
	AllowedDirections   []float64 `json:"allowed_directions"`
	MaxLeverage         float64   `json:"max_leverage"`
	TrailingStopATRMult float64   `json:"trailing_stop_atr_mult"`
	AllowPyramid        bool      `json:"allow_pyramid"`
	MaxPyramidCount     int       `json:"max_pyramid_count"`
	TimeDecayBars       *int      `json:"time_decay_bars"`
	SelectionReason     string    `json:"selection_reason"`
	ReasonCodes         []string  `json:"reason_codes"`
}

// SelectPlaybook supports finite inputs and finite intermediate arithmetic. A
// negative confidence product is rejected because Python would produce a complex value.
// Invalid inputs return an error and a zero selection even when a preservation
// condition applies. Callers must treat errors as fail closed, never as trade authorization.
func SelectPlaybook(s PlaybookState, c PlaybookConfig) (PlaybookSelection, error) {
	values := []float64{s.DirectionalConfidence, s.VolatilityConfidence, s.PositioningConfidence, s.QualityConfidence, s.DirectionSignal, s.TransitionRisk, c.PreserveCapitalTransitionRisk, c.PreserveCapitalMinOverallConfidence, c.TradeDirectionalConfidenceFloor, c.BearishOverrideConfidenceFloor, c.BearishOverrideDirectionSignal, c.BullMaxLeverage, c.BearMaxLeverage, c.RangeMaxLeverage, c.PreserveCapitalMaxLeverage, c.BullTrailingStopATRMult, c.BearTrailingStopATRMult, c.RangeTrailingStopATRMult, c.PreserveCapitalTrailingStopATRMult}
	for _, v := range values {
		if !sizingFinite(v) {
			return PlaybookSelection{}, fmt.Errorf("playbook inputs must be finite")
		}
	}
	// Validate the product even when preservation short-circuits, so the API has
	// one predictable finite numeric domain for every selection.
	product := s.DirectionalConfidence
	for _, v := range []float64{s.VolatilityConfidence, s.PositioningConfidence, s.QualityConfidence} {
		product *= v
		if !sizingFinite(product) {
			return PlaybookSelection{}, fmt.Errorf("playbook confidence product overflow")
		}
	}
	if product < 0 {
		return PlaybookSelection{}, fmt.Errorf("playbook confidence product must be nonnegative")
	}
	out := PlaybookSelection{Symbol: s.Symbol, Timeframe: s.Timeframe, CutoffTimeNS: s.CutoffTimeNS}
	preserve := s.VolatilityState == "DISTORTED" || s.QualityState == "DEGRADED" || s.QualityState == "UNSAFE" || s.TransitionRisk >= c.PreserveCapitalTransitionRisk || math.Pow(product, 0.25) < c.PreserveCapitalMinOverallConfidence
	tradable := s.QualityState == "TRADABLE" || s.QualityState == "NOISY"
	bull := s.DirectionalState == "UPTREND" && tradable && s.PositioningState != "CROWDED_LONG" && s.DirectionalConfidence >= c.TradeDirectionalConfidenceFloor
	bear := tradable && s.PositioningState != "CROWDED_SHORT" && ((s.DirectionalState == "DOWNTREND" && s.DirectionalConfidence >= c.TradeDirectionalConfidenceFloor) || (s.DirectionalState == "TRANSITION" && s.DirectionSignal <= c.BearishOverrideDirectionSignal && s.DirectionalConfidence >= c.BearishOverrideConfidenceFloor))
	switch {
	case preserve:
		out.PlaybookID, out.PlaybookConfidence = "CAPITAL_PRESERVATION", 0.9
		out.AllowedDirections = []float64{0}
		out.MaxLeverage, out.TrailingStopATRMult = c.PreserveCapitalMaxLeverage, c.PreserveCapitalTrailingStopATRMult
		decay := c.PreserveCapitalTimeDecayBars
		out.TimeDecayBars = &decay
		out.SelectionReason = fmt.Sprintf("Capital preservation because quality=%s, volatility=%s, transition_risk=%.2f", s.QualityState, s.VolatilityState, s.TransitionRisk)
		out.ReasonCodes = []string{"PLAYBOOK_CAPITAL_PRESERVATION", "PLAYBOOK_REDUCE_ONLY"}
	case bull || bear:
		confidence := float64(s.DirectionalConfidence*0.6) + float64(s.VolatilityConfidence*0.2)
		confidence = confidence + float64(s.PositioningConfidence*0.2)
		if !sizingFinite(confidence) {
			return PlaybookSelection{}, fmt.Errorf("playbook confidence overflow")
		}
		out.PlaybookConfidence = sizingRound(math.Min(1, confidence), 4)
		if bull {
			out.PlaybookID = "BULL_CONTINUATION"
			out.AllowedDirections = []float64{1, 0}
			out.MaxLeverage, out.TrailingStopATRMult = c.BullMaxLeverage, c.BullTrailingStopATRMult
			out.AllowPyramid, out.MaxPyramidCount = true, 2
			out.SelectionReason = "Uptrend state with acceptable volatility and crowding"
			out.ReasonCodes = []string{"PLAYBOOK_BULL_CONTINUATION", "PLAYBOOK_ALLOW_LONG"}
		} else {
			out.PlaybookID = "BEAR_DEFENSE"
			out.AllowedDirections = []float64{-1, 0}
			out.MaxLeverage, out.TrailingStopATRMult = c.BearMaxLeverage, c.BearTrailingStopATRMult
			out.SelectionReason = "Downtrend state with tactical short bias"
			out.ReasonCodes = []string{"PLAYBOOK_BEAR_DEFENSE", "PLAYBOOK_ALLOW_SHORT"}
		}
	default:
		confidence := float64((1-s.DirectionalConfidence)*0.5) + float64(s.VolatilityConfidence*0.25)
		confidence = confidence + float64(s.QualityConfidence*0.25)
		if !sizingFinite(confidence) {
			return PlaybookSelection{}, fmt.Errorf("playbook confidence overflow")
		}
		out.PlaybookID = "RANGE_LIGHT"
		out.PlaybookConfidence = sizingRound(math.Min(1, confidence), 4)
		out.AllowedDirections = []float64{1, -1, 0}
		out.MaxLeverage, out.TrailingStopATRMult = c.RangeMaxLeverage, c.RangeTrailingStopATRMult
		decay := c.RangeTimeDecayBars
		out.TimeDecayBars = &decay
		out.SelectionReason = "Neutral or transition state with lighter mean-reversion posture"
		out.ReasonCodes = []string{"PLAYBOOK_RANGE_LIGHT", "PLAYBOOK_ALLOW_BOTH"}
	}
	return out, nil
}
