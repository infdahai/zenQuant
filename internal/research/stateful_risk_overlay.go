package research

import (
	"fmt"
	"math"
	"math/big"
	"strconv"
	"time"
)

// StatefulRiskConfig is deliberately limited to the supported subset of the
// Python overlay. Callers must not use this engine as a complete risk guard.
type StatefulRiskConfig struct {
	Portfolio                      string
	StartingEquity, MaxLeverage    float64
	KillCooldownBars, KillRampBars int
}

func DefaultStatefulRiskConfig() StatefulRiskConfig {
	return StatefulRiskConfig{"arc", 100000, 6, 36, 24}
}

// StatefulRiskIndicator contains only supported indicator inputs. The caller
// must ensure omitted Python risk inputs are inactive; this API cannot establish
// that condition and is not suitable for live authorization.
type StatefulRiskIndicator struct {
	Symbol, Timeframe              string
	Price, RealizedVol, FundingOIZ float64
	QualityFlags                   []string
}
type StatefulRiskTarget struct {
	IntentID, Portfolio, Symbol, Timeframe string
	CutoffTimeNS                           int64
	Quantity, Leverage                     float64
	ReduceOnly                             bool
	// MacroEvent is supplied per target using Python's event-date calendar.
	MacroEvent  bool
	ReasonCodes []string
}
type StatefulRiskDecision struct {
	Original    StatefulRiskTarget
	Revised     StatefulRiskTarget
	LeverageCap float64
	Flags       []string
}
type StatefulRiskSnapshot struct {
	Portfolio                                    string
	TimeNS                                       int64
	Equity, HighWaterMark, Drawdown, LeverageCap float64
	ReduceOnly                                   bool
	Flags                                        []string
}
type StatefulRiskState struct {
	Cash, Equity, HighWaterMark          float64
	Positions, Prices                    map[string]float64
	DayID                                int64
	DayEquity                            float64
	HasDay                               bool
	KillActive                           bool
	KillBars, RecoveryBars, DegradedBars int
	EquityHistory                        []float64
}

type StatefulRiskOverlay struct {
	cfg           StatefulRiskConfig
	state         StatefulRiskState
	indicators    map[string]StatefulRiskIndicator
	positionOrder []string
}

func NewStatefulRiskOverlay(c StatefulRiskConfig) (*StatefulRiskOverlay, error) {
	if !sizingFinite(c.StartingEquity) || c.StartingEquity <= 0 || !sizingFinite(c.MaxLeverage) || c.MaxLeverage < 0 || c.KillCooldownBars < 0 || c.KillRampBars < 1 {
		return nil, fmt.Errorf("invalid risk configuration")
	}
	return &StatefulRiskOverlay{cfg: c, state: StatefulRiskState{Cash: c.StartingEquity, Equity: c.StartingEquity, HighWaterMark: c.StartingEquity, Positions: map[string]float64{}, Prices: map[string]float64{}}, indicators: map[string]StatefulRiskIndicator{}}, nil
}
func riskKey(symbol, timeframe string) string { return symbol + "\x00" + timeframe }
func (r *StatefulRiskOverlay) revalue() {
	e := r.state.Cash
	for _, symbol := range r.positionOrder {
		e += r.state.Positions[symbol] * r.state.Prices[symbol]
	}
	r.state.Equity = e
	r.state.HighWaterMark = math.Max(r.state.HighWaterMark, e)
}
func (r *StatefulRiskOverlay) OnIndicator(i StatefulRiskIndicator) error {
	if i.Symbol == "" || i.Timeframe == "" || !sizingFinite(i.Price) || i.Price < 0 || !sizingFinite(i.RealizedVol) || !sizingFinite(i.FundingOIZ) {
		return fmt.Errorf("invalid indicator")
	}
	i.QualityFlags = append([]string(nil), i.QualityFlags...)
	r.indicators[riskKey(i.Symbol, i.Timeframe)] = i
	if i.Price > 0 {
		r.state.Prices[i.Symbol] = i.Price
		r.revalue()
	}
	return nil
}

// OnFill consumes an incremental fill, not cumulative order quantity.
func (r *StatefulRiskOverlay) OnFill(symbol string, side int, qty, price float64) error {
	if symbol == "" || (side != 1 && side != -1) || !sizingFinite(qty) || qty < 0 || !sizingFinite(price) || price < 0 {
		return fmt.Errorf("invalid fill")
	}
	if qty == 0 {
		return nil
	}
	signed := float64(side) * qty
	current, exists := r.state.Positions[symbol]
	if !exists {
		r.positionOrder = append(r.positionOrder, symbol)
	}
	// Python adds the shortest decimal representations and removes opposing
	// cancellation residue only within four ULPs.
	a, _ := new(big.Rat).SetString(strconv.FormatFloat(current, 'f', -1, 64))
	b, _ := new(big.Rat).SetString(strconv.FormatFloat(signed, 'f', -1, 64))
	position, _ := new(big.Rat).Add(a, b).Float64()
	if current*signed < 0 && math.Abs(position) <= 4*math.Max(math.Nextafter(math.Abs(current), math.Inf(1))-math.Abs(current), math.Nextafter(math.Abs(signed), math.Inf(1))-math.Abs(signed)) {
		position = 0
	}
	r.state.Positions[symbol] = position
	if price > 0 {
		r.state.Prices[symbol] = price
	}
	r.state.Cash -= float64(side) * qty * price
	r.revalue()
	return nil
}
func (r *StatefulRiskOverlay) ApplyFee(fee float64) error {
	if !sizingFinite(fee) {
		return fmt.Errorf("invalid fee")
	}
	if fee > 0 {
		r.state.Cash = math.Max(0, r.state.Cash-fee)
		r.revalue()
	}
	return nil
}

// State returns a detached view for audit and replay comparisons.
func (r *StatefulRiskOverlay) State() StatefulRiskState {
	s := r.state
	s.Positions = map[string]float64{}
	for k, v := range r.state.Positions {
		s.Positions[k] = v
	}
	s.Prices = map[string]float64{}
	for k, v := range r.state.Prices {
		s.Prices[k] = v
	}
	s.EquityHistory = append([]float64(nil), r.state.EquityHistory...)
	return s
}
func (r *StatefulRiskOverlay) Apply(t StatefulRiskTarget) (StatefulRiskDecision, StatefulRiskSnapshot, error) {
	if t.Symbol == "" || t.Timeframe == "" || !sizingFinite(t.Quantity) || !sizingFinite(t.Leverage) || t.Leverage < 0 || r.state.Equity < 0 {
		return StatefulRiskDecision{}, StatefulRiskSnapshot{}, fmt.Errorf("invalid target or negative equity")
	}
	before := r.State()
	s := &r.state
	dd := math.Max(0, (s.HighWaterMark-s.Equity)/s.HighWaterMark)
	cap := r.cfg.MaxLeverage
	flags := []string{}
	s.EquityHistory = append(s.EquityHistory, s.Equity)
	if len(s.EquityHistory) > 120 {
		s.EquityHistory = s.EquityHistory[1:]
	}
	if len(s.EquityHistory) >= 60 {
		returns := []float64{}
		for i := 1; i < len(s.EquityHistory); i++ {
			if s.EquityHistory[i-1] > 0 {
				returns = append(returns, (s.EquityHistory[i]-s.EquityHistory[i-1])/s.EquityHistory[i-1])
			}
		}
		if len(returns) > 0 {
			avg := 0.0
			for _, v := range returns {
				avg += v
			}
			avg /= float64(len(returns))
			variance := 0.0
			for _, v := range returns {
				variance += (v - avg) * (v - avg)
			}
			variance /= float64(len(returns))
			sharpe := 0.0
			if variance > 1e-15 {
				sharpe = avg / math.Sqrt(variance) * math.Sqrt(float64(len(returns)))
			}
			if sharpe < -2 {
				s.DegradedBars++
				if s.DegradedBars >= 24 {
					cap *= 0.4
					flags = append(flags, "STRATEGY_DEGRADED")
				}
			} else if s.DegradedBars > 0 {
				s.DegradedBars--
			}
		}
	}
	d1, d2, d3 := 0.08, 0.14, 0.20
	if t.Quantity > 0 {
		d1, d2, d3 = 0.15, 0.22, 0.28
	}
	if dd >= d3 {
		cap *= 0.2
		flags = append(flags, "DD_STEP_3")
	} else if dd >= d2 {
		cap *= 0.4
		flags = append(flags, "DD_STEP_2")
	} else if dd >= d1 {
		cap *= 0.65
		flags = append(flags, "DD_STEP_1")
	}
	if dd >= 0.30 {
		if !s.KillActive {
			s.KillActive = true
			s.KillBars = 0
			s.RecoveryBars = 0
		}
		s.KillBars++
		flags = append(flags, "KILL_SWITCH")
	} else if s.KillActive {
		s.KillBars++
		if dd <= 0.12 && s.KillBars >= r.cfg.KillCooldownBars {
			s.RecoveryBars++
			if s.RecoveryBars >= r.cfg.KillRampBars {
				s.KillActive = false
				s.KillBars = 0
				s.RecoveryBars = 0
				flags = append(flags, "KILL_RECOVERED")
			} else {
				cap *= 0.3 + 0.7*float64(s.RecoveryBars)/float64(r.cfg.KillRampBars)
				flags = append(flags, "KILL_RECOVERY_RAMP")
			}
		} else {
			s.RecoveryBars = 0
			flags = append(flags, "KILL_SWITCH")
		}
	}
	// UTC day index has the same equality/rollover behavior as Python ordinal.
	day := time.Unix(0, t.CutoffTimeNS).UTC().Unix() / 86400
	if t.CutoffTimeNS < 0 && time.Unix(0, t.CutoffTimeNS).UTC().Unix()%86400 != 0 {
		day--
	}
	if !s.HasDay || s.DayID != day {
		s.HasDay = true
		s.DayID = day
		s.DayEquity = s.Equity
	} else if s.DayEquity > 0 && math.Max(0, (s.DayEquity-s.Equity)/s.DayEquity) >= 0.10 {
		cap = math.Min(cap, 0.5)
		flags = append(flags, "DAILY_LOSS_LIMIT")
	}
	reduce := t.ReduceOnly
	for _, f := range flags {
		if f == "KILL_SWITCH" {
			reduce = true
		}
	}
	if i, ok := r.indicators[riskKey(t.Symbol, t.Timeframe)]; ok {
		if i.RealizedVol >= 0.95 {
			cap *= 0.4
			flags = append(flags, "VOL_SHOCK")
		}
		if math.Abs(i.FundingOIZ) >= 2.2 {
			cap *= 0.7
			flags = append(flags, "CROWDING_EXTREME")
		}
		if math.Abs(i.FundingOIZ) >= 3 {
			reduce = true
			flags = append(flags, "SEVERE_CROWDING")
		}
		weekday := time.Unix(0, t.CutoffTimeNS).UTC().Weekday()
		if weekday == time.Saturday || weekday == time.Sunday {
			cap *= 1.25
			flags = append(flags, "WEEKEND_VOL_ADJUST")
		}
		if t.MacroEvent {
			cap *= 0.75
			flags = append(flags, "MACRO_EVENT")
		}
		for _, f := range i.QualityFlags {
			if f == "BOOK_STALE" || f == "VENUE_DOWN" || f == "DATA_STALE" || f == "DATA_FUTURE" {
				reduce = true
				cap = math.Min(cap, 0.45)
				flags = append(flags, "LIQUIDITY_STRESS")
				break
			}
		}
	}
	price := math.Max(1e-9, s.Prices[t.Symbol])
	result, err := ClampRiskTarget(RiskTargetInput{TargetQty: t.Quantity, TargetLeverage: t.Leverage, CurrentPosition: s.Positions[t.Symbol], Equity: s.Equity, LeverageCap: cap, Price: price, ReduceOnly: reduce, ReasonCodes: t.ReasonCodes, Flags: flags})
	if err != nil {
		r.state = before
		return StatefulRiskDecision{}, StatefulRiskSnapshot{}, err
	}
	revised := t
	revised.Quantity = result.TargetQty
	revised.Leverage = result.TargetLeverage
	revised.ReduceOnly = reduce
	revised.ReasonCodes = result.ReasonCodes
	decision := StatefulRiskDecision{t, revised, math.RoundToEven(cap*1e4) / 1e4, result.Flags}
	snap := StatefulRiskSnapshot{r.cfg.Portfolio, t.CutoffTimeNS, math.RoundToEven(s.Equity*100) / 100, math.RoundToEven(s.HighWaterMark*100) / 100, math.RoundToEven(dd*1e6) / 1e6, decision.LeverageCap, reduce, result.Flags}
	return decision, snap, nil
}
