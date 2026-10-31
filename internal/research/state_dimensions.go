package research

import (
	"fmt"
	"math"
)

// StateDimensionsInput contains synthetic or caller-supplied indicators; no
// extraction, defaults, directional classification, or historical state is implied.
type StateDimensionsInput struct {
	RealizedVol  float64  `json:"realized_vol"`
	VIXZ         float64  `json:"vix_z"`
	FundingOIZ   float64  `json:"funding_oi_z"`
	QualityFlags []string `json:"quality_flags"`
}

// ClassifyStateDimensions fills the three offline dimensions of a caller's
// playbook state. Numeric inputs must be finite and directional confidence must
// be in [0,1]; errors return a zero state. Transition risk uses unrounded
// confidences, while returned confidences and risk use Python's four-place rounding.
// Directional state, signal and metadata are supplied by the caller.
func ClassifyStateDimensions(input StateDimensionsInput, state PlaybookState) (PlaybookState, error) {
	if !finite(input.RealizedVol) || !finite(input.VIXZ) || !finite(input.FundingOIZ) || !finite(state.DirectionalConfidence) || !finite(state.DirectionSignal) || state.DirectionalConfidence < 0 || state.DirectionalConfidence > 1 {
		return PlaybookState{}, fmt.Errorf("state dimensions require finite inputs and directional confidence in [0,1]")
	}
	unsafe, stale, noisy, other := false, false, false, false
	for _, flag := range input.QualityFlags {
		switch flag {
		case "DATA_FUTURE", "VENUE_DOWN":
			unsafe = true
		case "DATA_STALE", "BOOK_STALE":
			stale = true
		case "MACRO_STALE":
			noisy = true
		case "OK":
		default:
			other = true
		}
	}
	switch {
	case unsafe || stale || input.RealizedVol > 2.8:
		state.VolatilityState, state.VolatilityConfidence = "DISTORTED", 0.9
	case input.VIXZ > 3:
		state.VolatilityState, state.VolatilityConfidence = "DISTORTED", 0.85
	case input.RealizedVol > 1.5 || input.VIXZ > 2:
		state.VolatilityState, state.VolatilityConfidence = "HIGH", 0.75
	case input.RealizedVol < 0.45:
		state.VolatilityState, state.VolatilityConfidence = "LOW", 0.7
	default:
		state.VolatilityState, state.VolatilityConfidence = "NORMAL", 0.8
	}
	switch {
	case input.FundingOIZ >= 1.8:
		state.PositioningState, state.PositioningConfidence = "CROWDED_LONG", 0.65+math.Min(0.25, input.FundingOIZ/8)
	case input.FundingOIZ <= -1.8:
		state.PositioningState, state.PositioningConfidence = "CROWDED_SHORT", 0.65+math.Min(0.25, math.Abs(input.FundingOIZ)/8)
	case math.Abs(input.FundingOIZ) <= 0.5:
		state.PositioningState, state.PositioningConfidence = "HEALTHY", 0.8
	default:
		state.PositioningState, state.PositioningConfidence = "UNCERTAIN", 0.6
	}
	switch {
	case unsafe:
		state.QualityState, state.QualityConfidence = "UNSAFE", 0.95
	case stale:
		state.QualityState, state.QualityConfidence = "DEGRADED", 0.85
	case noisy || other:
		state.QualityState, state.QualityConfidence = "NOISY", 0.7
	default:
		state.QualityState, state.QualityConfidence = "TRADABLE", 0.9
	}
	minimum := math.Min(math.Min(state.DirectionalConfidence, state.VolatilityConfidence), math.Min(state.PositioningConfidence, state.QualityConfidence))
	state.TransitionRisk = sizingRound(math.Max(0, 1-minimum), 4)
	state.DirectionalConfidence = sizingRound(state.DirectionalConfidence, 4)
	state.VolatilityConfidence = sizingRound(state.VolatilityConfidence, 4)
	state.PositioningConfidence = sizingRound(state.PositioningConfidence, 4)
	state.QualityConfidence = sizingRound(state.QualityConfidence, 4)
	return state, nil
}
