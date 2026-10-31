package research

import (
	"fmt"
	"strings"
)

// ExitPosition is the mutable subset used by the standalone exit check.
type ExitPosition struct {
	Symbol       string  `json:"symbol"`
	Timeframe    string  `json:"timeframe"`
	Direction    float64 `json:"direction"`
	EntryPrice   float64 `json:"entry_price"`
	CurrentQty   float64 `json:"current_qty"`
	TrailingStop float64 `json:"trailing_stop"`
	BarsHeld     int     `json:"bars_held"`
}
type ExitState struct {
	CutoffTimeNS     int64   `json:"cutoff_time_ns"`
	DirectionalState string  `json:"directional_state"`
	VolatilityState  string  `json:"volatility_state"`
	QualityState     string  `json:"quality_state"`
	TransitionRisk   float64 `json:"transition_risk"`
}
type ExitPlaybook struct {
	PlaybookID          string  `json:"playbook_id"`
	TrailingStopATRMult float64 `json:"trailing_stop_atr_mult"`
	TimeDecayBars       *int    `json:"time_decay_bars"`
}
type ExitConfig struct {
	TimeDecayEnabled            bool    `json:"time_decay_enabled"`
	RegimeChangeEnabled         bool    `json:"regime_change_enabled"`
	MinBarsBeforeTrail          int     `json:"min_bars_before_trail"`
	TransitionRiskExitThreshold float64 `json:"transition_risk_exit_threshold"`
}
type ExitIndicators struct {
	Price float64 `json:"price"`
	ATR   float64 `json:"atr"`
}
type ExitDecision struct {
	Symbol             string   `json:"symbol"`
	Timeframe          string   `json:"timeframe"`
	CutoffTimeNS       int64    `json:"cutoff_time_ns"`
	ExitType           string   `json:"exit_type"`
	ExitUrgency        string   `json:"exit_urgency"`
	TargetReductionPct float64  `json:"target_reduction_pct"`
	StopPrice          *float64 `json:"stop_price"`
	ReasonCode         string   `json:"reason_code"`
	ReasonDetail       string   `json:"reason_detail"`
	ReasonCodes        []string `json:"reason_codes"`
}

// CheckExit ports only ExitEngine.check, not full strategy, risk, or execution
// parity. The strict domain is direction +/-1, positive entry price and quantity,
// nonnegative ATR, price, multiplier and bar counts, and risk and threshold in
// [0,1]. Trailing stops may be negative; zero denotes an uninitialized stop.
// Zero price/ATR represent missing indicators. All floats must be finite.
// Errors leave position untouched, including on overflow.
func CheckExit(position *ExitPosition, state ExitState, playbook ExitPlaybook, indicators ExitIndicators, config ExitConfig) (ExitDecision, error) {
	if position == nil {
		return ExitDecision{}, fmt.Errorf("nil exit position")
	}
	p := *position
	for _, x := range []float64{p.Direction, p.EntryPrice, p.CurrentQty, p.TrailingStop, state.TransitionRisk, playbook.TrailingStopATRMult, indicators.Price, indicators.ATR, config.TransitionRiskExitThreshold} {
		if !sizingFinite(x) {
			return ExitDecision{}, fmt.Errorf("exit inputs must be finite")
		}
	}
	if (p.Direction != 1 && p.Direction != -1) || p.EntryPrice <= 0 || p.CurrentQty <= 0 || p.BarsHeld < 0 || indicators.Price < 0 || indicators.ATR < 0 || playbook.TrailingStopATRMult < 0 || config.MinBarsBeforeTrail < 0 || (playbook.TimeDecayBars != nil && *playbook.TimeDecayBars < 0) || state.TransitionRisk < 0 || state.TransitionRisk > 1 || config.TransitionRiskExitThreshold < 0 || config.TransitionRiskExitThreshold > 1 {
		return ExitDecision{}, fmt.Errorf("exit input outside supported domain")
	}
	decision := ExitDecision{Symbol: p.Symbol, Timeframe: p.Timeframe, CutoffTimeNS: state.CutoffTimeNS, ExitType: "NONE", ExitUrgency: "NONE", ReasonCode: "NO_EXIT", ReasonDetail: "No exit condition met", ReasonCodes: []string{}}
	set := func(kind, urgency string, reduction float64, code, detail string, reasons ...string) {
		decision.ExitType = kind
		decision.ExitUrgency = urgency
		decision.TargetReductionPct = reduction
		decision.ReasonCode = code
		decision.ReasonDetail = detail
		decision.ReasonCodes = reasons
	}
	if p.Direction > 0 && strings.Contains(playbook.PlaybookID, "BULL") && state.DirectionalState == "DOWNTREND" {
		set("INVALIDATION", "IMMEDIATE", 1, "BULL_INVALIDATED_BY_DOWNTREND", "Bull playbook invalidated by downtrend state", "EXIT_INVALIDATION", "DIRECTIONAL_FLIP")
		return decision, nil
	}
	if p.Direction < 0 && strings.Contains(playbook.PlaybookID, "BEAR") && state.DirectionalState == "UPTREND" {
		set("INVALIDATION", "IMMEDIATE", 1, "BEAR_INVALIDATED_BY_UPTREND", "Bear playbook invalidated by uptrend state", "EXIT_INVALIDATION", "DIRECTIONAL_FLIP")
		return decision, nil
	}
	if indicators.Price > 0 && indicators.ATR > 0 && p.BarsHeld >= config.MinBarsBeforeTrail {
		distance := indicators.ATR * playbook.TrailingStopATRMult
		if !sizingFinite(distance) {
			return ExitDecision{}, fmt.Errorf("exit stop distance overflow")
		}
		if p.TrailingStop == 0 {
			p.TrailingStop = p.EntryPrice - p.Direction*distance
			if !sizingFinite(p.TrailingStop) {
				return ExitDecision{}, fmt.Errorf("exit initial stop overflow")
			}
		} else {
			next := indicators.Price - p.Direction*distance
			if !sizingFinite(next) {
				return ExitDecision{}, fmt.Errorf("exit trailing stop overflow")
			}
			if (p.Direction > 0 && next > p.TrailingStop) || (p.Direction < 0 && next < p.TrailingStop) {
				p.TrailingStop = next
			}
			if (p.Direction > 0 && indicators.Price <= p.TrailingStop) || (p.Direction < 0 && indicators.Price >= p.TrailingStop) {
				set("TRAILING", "IMMEDIATE", 1, "TRAILING_STOP_BREACH", fmt.Sprintf("Price %.2f breached trailing stop %.2f", indicators.Price, p.TrailingStop), "EXIT_TRAILING_STOP")
				stop := p.TrailingStop
				decision.StopPrice = &stop
				*position = p
				return decision, nil
			}
		}
	}
	if config.TimeDecayEnabled && playbook.TimeDecayBars != nil && p.BarsHeld > *playbook.TimeDecayBars && indicators.Price > 0 {
		delta := indicators.Price - p.EntryPrice
		pnl := delta * p.Direction
		if !sizingFinite(delta) || !sizingFinite(pnl) {
			return ExitDecision{}, fmt.Errorf("exit pnl overflow")
		}
		pnl *= p.CurrentQty
		if !sizingFinite(pnl) {
			return ExitDecision{}, fmt.Errorf("exit pnl overflow")
		}
		if pnl < 0 {
			set("TIME_DECAY", "GRADUAL", 0.5, "TIME_DECAY_UNPROFITABLE", fmt.Sprintf("Held %d bars without profit", p.BarsHeld), "EXIT_TIME_DECAY", "UNPROFITABLE")
			*position = p
			return decision, nil
		}
	}
	if config.RegimeChangeEnabled {
		if state.QualityState == "UNSAFE" {
			set("REGIME_CHANGE", "IMMEDIATE", 1, "QUALITY_UNSAFE", "Data quality unsafe; flatten position", "EXIT_REGIME_CHANGE", "QUALITY_UNSAFE")
		} else if state.VolatilityState == "DISTORTED" {
			set("REGIME_CHANGE", "GRADUAL", 0.5, "VOLATILITY_DISTORTED", "Volatility regime distorted; de-risk position", "EXIT_REGIME_CHANGE", "VOLATILITY_STRESS")
		} else if playbook.PlaybookID != "CAPITAL_PRESERVATION" && state.TransitionRisk >= config.TransitionRiskExitThreshold {
			set("REGIME_CHANGE", "GRADUAL", 0.5, "STATE_TRANSITION_RISK", "State transition risk is elevated; de-risk position", "EXIT_REGIME_CHANGE", "TRANSITION_RISK")
		}
	}
	*position = p
	return decision, nil
}
