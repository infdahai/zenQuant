package research

import (
	"errors"
	"math"
	"sort"
)

// GateV3Episode contains the synthetic episode fields consumed by Gate v3.
type GateV3Episode struct {
	Symbol     string  `json:"symbol"`
	Side       int     `json:"side"`
	OpenedAtNS int64   `json:"opened_at_ns"`
	NetPnL     float64 `json:"net_pnl"`
	Closed     bool    `json:"closed"`
}

type ProfitFactorStatus string

const (
	ProfitFactorInsufficientSample ProfitFactorStatus = "INSUFFICIENT_SAMPLE"
	ProfitFactorNoLossEpisodes     ProfitFactorStatus = "NO_LOSS_EPISODES"
	ProfitFactorDefined            ProfitFactorStatus = "DEFINED"
)

type GateSeverity string

const (
	GateSeverityNone GateSeverity = "NONE"
	GateSeveritySoft GateSeverity = "SOFT"
	GateSeverityHard GateSeverity = "HARD"
)

type ProfitFactorResult struct {
	Value              *float64           `json:"value"`
	Status             ProfitFactorStatus `json:"status"`
	ClosedEpisodeCount int                `json:"closed_episode_count"`
	GrossProfit        float64            `json:"gross_profit"`
	GrossLoss          float64            `json:"gross_loss"`
}

type CadenceAssessment struct {
	SwitchesPerDay float64      `json:"switches_per_day"`
	Severity       GateSeverity `json:"severity"`
	Policy         string       `json:"policy"`
	Reason         *string      `json:"reason"`
}

type GateV3Diagnostics struct {
	FillProfitFactor    *float64           `json:"fill_profit_factor"`
	ActionProfitFactor  map[string]float64 `json:"action_profit_factor"`
	PositionSwitchCount int                `json:"position_switch_count"`
}

type GateV3Result struct {
	EpisodeProfitFactor ProfitFactorResult `json:"episode_profit_factor"`
	Cadence             CadenceAssessment  `json:"cadence"`
	HardFailed          bool               `json:"hard_failed"`
	Passed              bool               `json:"passed"`
	HardFailures        []string           `json:"hard_failures"`
	SoftFlags           []string           `json:"soft_flags"`
	Diagnostics         GateV3Diagnostics  `json:"diagnostics"`
}

// GateV3Options uses nil pointers for omitted values; defaults match Python.
type GateV3Options struct {
	SwitchesPerDay         *float64
	ElapsedDays            *float64
	MinEpisodeProfitFactor *float64
	Policy                 *string
	FillProfitFactor       *float64
	ActionProfitFactor     map[string]float64
}

// pythonSum implements the compensated float summation used by Python 3.12+.
type pythonSum struct{ sum, correction float64 }

func (s *pythonSum) add(x float64) {
	t := s.sum + x
	if math.Abs(s.sum) >= math.Abs(x) {
		s.correction += (s.sum - t) + x
	} else {
		s.correction += (x - t) + s.sum
	}
	s.sum = t
}

func (s pythonSum) value() float64 {
	if s.correction != 0 && !math.IsInf(s.correction, 0) && !math.IsNaN(s.correction) {
		return s.sum + s.correction
	}
	return s.sum
}

// CalculateEpisodeProfitFactor uses net PnL from closed episodes only.
func CalculateEpisodeProfitFactor(episodes []GateV3Episode) ProfitFactorResult {
	result := ProfitFactorResult{}
	var profit, loss pythonSum
	for _, e := range episodes {
		if !e.Closed {
			continue
		}
		result.ClosedEpisodeCount++
		// Python's max/min with 0 as the first argument ignore NaN.
		positive, negative := 0.0, 0.0
		if e.NetPnL > 0 {
			positive = e.NetPnL
		}
		if e.NetPnL < 0 {
			negative = e.NetPnL
		}
		profit.add(positive)
		loss.add(negative)
	}
	result.GrossProfit = profit.value()
	result.GrossLoss = math.Abs(loss.value())
	switch {
	case result.ClosedEpisodeCount == 0:
		result.Status = ProfitFactorInsufficientSample
	case result.GrossProfit == 0:
		result.Status = ProfitFactorDefined
		value := 0.0
		result.Value = &value
	case result.GrossLoss == 0:
		result.Status = ProfitFactorNoLossEpisodes
	default:
		result.Status = ProfitFactorDefined
		value := result.GrossProfit / result.GrossLoss
		result.Value = &value
	}
	return result
}

// PositionSwitchCount counts side changes per symbol in chronological order.
func PositionSwitchCount(episodes []GateV3Episode) (int, error) {
	ordered := append([]GateV3Episode(nil), episodes...)
	sort.SliceStable(ordered, func(i, j int) bool {
		if ordered[i].OpenedAtNS != ordered[j].OpenedAtNS {
			return ordered[i].OpenedAtNS < ordered[j].OpenedAtNS
		}
		return ordered[i].Symbol < ordered[j].Symbol
	})
	previous := make(map[string]int)
	count := 0
	for _, e := range ordered {
		if e.Side != -1 && e.Side != 1 {
			return 0, errors.New("episode side must be -1 or 1")
		}
		if side, ok := previous[e.Symbol]; ok && side != e.Side {
			count++
		}
		previous[e.Symbol] = e.Side
	}
	return count, nil
}

// EvaluateCadence classifies cadence regardless of policy name.
func EvaluateCadence(switchesPerDay float64, policy string) (CadenceAssessment, error) {
	if switchesPerDay < 0 {
		return CadenceAssessment{}, errors.New("switches_per_day cannot be negative")
	}
	result := CadenceAssessment{SwitchesPerDay: switchesPerDay, Policy: policy}
	var reason string
	switch {
	case switchesPerDay <= 1:
		result.Severity = GateSeverityNone
	case switchesPerDay <= 2:
		result.Severity = GateSeveritySoft
		reason = "position_switch_cadence_above_preferred"
	default:
		result.Severity = GateSeverityHard
		reason = "position_switch_cadence_above_normal_limit"
		if switchesPerDay > 5 {
			reason = "position_switch_cadence_above_absolute_limit"
		}
	}
	if reason != "" {
		result.Reason = &reason
	}
	return result, nil
}

// EvaluateGateV3 evaluates synthetic episodes and retains diagnostic-only metrics.
func EvaluateGateV3(episodes []GateV3Episode, options GateV3Options) (GateV3Result, error) {
	minimum := 1.0
	if options.MinEpisodeProfitFactor != nil {
		minimum = *options.MinEpisodeProfitFactor
	}
	if minimum < 0 {
		return GateV3Result{}, errors.New("min_episode_profit_factor cannot be negative")
	}
	count, err := PositionSwitchCount(episodes)
	if err != nil {
		return GateV3Result{}, err
	}
	var switches float64
	if options.SwitchesPerDay != nil {
		switches = *options.SwitchesPerDay
	} else {
		if options.ElapsedDays == nil || *options.ElapsedDays <= 0 {
			return GateV3Result{}, errors.New("positive elapsed_days is required when switches_per_day is omitted")
		}
		switches = float64(count) / *options.ElapsedDays
	}
	policy := "normal"
	if options.Policy != nil {
		policy = *options.Policy
	}
	pf := CalculateEpisodeProfitFactor(episodes)
	cadence, err := EvaluateCadence(switches, policy)
	if err != nil {
		return GateV3Result{}, err
	}
	result := GateV3Result{EpisodeProfitFactor: pf, Cadence: cadence, HardFailures: []string{}, SoftFlags: []string{}}
	switch {
	case pf.Status == ProfitFactorInsufficientSample:
		result.HardFailures = append(result.HardFailures, "episode_profit_factor_insufficient_sample")
	case pf.Status == ProfitFactorNoLossEpisodes:
		if pf.ClosedEpisodeCount < 2 {
			result.HardFailures = append(result.HardFailures, "episode_profit_factor_no_loss_sample_too_small")
		}
	case pf.Value != nil && *pf.Value < minimum:
		result.HardFailures = append(result.HardFailures, "episode_profit_factor_below_minimum")
	}
	if cadence.Reason != nil {
		if cadence.Severity == GateSeverityHard {
			result.HardFailures = append(result.HardFailures, *cadence.Reason)
		}
		if cadence.Severity == GateSeveritySoft {
			result.SoftFlags = append(result.SoftFlags, *cadence.Reason)
		}
	}
	result.HardFailed = len(result.HardFailures) != 0
	result.Passed = !result.HardFailed
	actions := make(map[string]float64, len(options.ActionProfitFactor))
	for k, v := range options.ActionProfitFactor {
		actions[k] = v
	}
	var fill *float64
	if options.FillProfitFactor != nil {
		value := *options.FillProfitFactor
		fill = &value
	}
	result.Diagnostics = GateV3Diagnostics{FillProfitFactor: fill, ActionProfitFactor: actions, PositionSwitchCount: count}
	return result, nil
}
