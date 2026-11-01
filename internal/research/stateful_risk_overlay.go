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
	Portfolio, CashCurrency        string
	StartingEquity, MaxLeverage    float64
	KillCooldownBars, KillRampBars int
}

func DefaultStatefulRiskConfig() StatefulRiskConfig {
	return StatefulRiskConfig{"arc", "USD", 100000, 6, 36, 24}
}

// StatefulRiskIndicator contains only supported indicator inputs. The caller
// must ensure omitted Python risk inputs are inactive; this API cannot establish
// that condition and is not suitable for live authorization.
type StatefulRiskIndicator struct {
	Symbol, Timeframe                           string
	Price, RealizedVol, FundingOIZ              float64
	QualityFlags                                []string
	CutoffTimeNS, AvailableTimeNS, ValidUntilNS int64
}
type StatefulRiskTarget struct {
	IntentID, Portfolio, Symbol, Timeframe string
	CutoffTimeNS, ProcessTimeNS            int64
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
	EquityValid                          bool
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
	indicators    map[string][]StatefulRiskIndicator
	positionOrder []string
	markExpiry    map[string]int64
	markTime      map[string]int64
	targetCutoffs map[string]int64
	lastEventTime int64
	hasEvent      bool
}

func NewStatefulRiskOverlay(c StatefulRiskConfig) (*StatefulRiskOverlay, error) {
	if c.Portfolio == "" || c.CashCurrency != "USD" || !sizingFinite(c.StartingEquity) || c.StartingEquity <= 0 || !sizingFinite(c.MaxLeverage) || c.MaxLeverage < 0 || c.KillCooldownBars < 0 || c.KillRampBars < 1 {
		return nil, fmt.Errorf("invalid risk configuration")
	}
	return &StatefulRiskOverlay{cfg: c, state: StatefulRiskState{Cash: c.StartingEquity, Equity: c.StartingEquity, HighWaterMark: c.StartingEquity, EquityValid: true, Positions: map[string]float64{}, Prices: map[string]float64{}}, indicators: map[string][]StatefulRiskIndicator{}, markExpiry: map[string]int64{}, markTime: map[string]int64{}, targetCutoffs: map[string]int64{}}, nil
}
func riskKey(symbol, timeframe string) string { return symbol + "\x00" + timeframe }
func (r *StatefulRiskOverlay) clone() *StatefulRiskOverlay {
	c := *r
	c.state = r.State()
	c.positionOrder = append([]string(nil), r.positionOrder...)
	c.indicators = make(map[string][]StatefulRiskIndicator, len(r.indicators))
	for k, v := range r.indicators {
		c.indicators[k] = append([]StatefulRiskIndicator(nil), v...)
	}
	c.markExpiry = make(map[string]int64, len(r.markExpiry))
	for k, v := range r.markExpiry {
		c.markExpiry[k] = v
	}
	c.markTime = make(map[string]int64, len(r.markTime))
	for k, v := range r.markTime {
		c.markTime[k] = v
	}
	c.targetCutoffs = make(map[string]int64, len(r.targetCutoffs))
	for k, v := range r.targetCutoffs {
		c.targetCutoffs[k] = v
	}
	return &c
}
func (r *StatefulRiskOverlay) advance(ts int64) error {
	if ts <= 0 || (r.hasEvent && ts < r.lastEventTime) {
		return fmt.Errorf("event time must be positive and nondecreasing")
	}
	r.hasEvent = true
	r.lastEventTime = ts
	for key, entries := range r.indicators {
		kept := entries[:0]
		for _, entry := range entries {
			if entry.ValidUntilNS >= ts {
				kept = append(kept, entry)
			}
		}
		if len(kept) == 0 {
			delete(r.indicators, key)
		} else {
			r.indicators[key] = kept
		}
	}
	for symbol, qty := range r.state.Positions {
		if qty != 0 && r.markExpiry[symbol] < ts {
			r.state.EquityValid = false
			break
		}
	}
	return nil
}
func (r *StatefulRiskOverlay) revalue() error {
	e := r.state.Cash
	if !sizingFinite(e) {
		return fmt.Errorf("cash overflow")
	}
	for _, symbol := range r.positionOrder {
		if r.state.Positions[symbol] != 0 && (r.state.Prices[symbol] <= 0 || r.markExpiry[symbol] < r.lastEventTime) {
			r.state.EquityValid = false
			return nil
		}
		value := r.state.Positions[symbol] * r.state.Prices[symbol]
		if !sizingFinite(value) {
			return fmt.Errorf("position value overflow")
		}
		e += value
		if !sizingFinite(e) {
			return fmt.Errorf("equity overflow")
		}
	}
	r.state.Equity = e
	r.state.EquityValid = true
	r.state.HighWaterMark = math.Max(r.state.HighWaterMark, e)
	return nil
}
func (r *StatefulRiskOverlay) OnIndicator(i StatefulRiskIndicator) error {
	c := r.clone()
	if i.Symbol == "" || i.Timeframe == "" || !sizingFinite(i.Price) || i.Price <= 0 || !sizingFinite(i.RealizedVol) || !sizingFinite(i.FundingOIZ) || i.CutoffTimeNS <= 0 || i.CutoffTimeNS > i.AvailableTimeNS || i.ValidUntilNS < i.AvailableTimeNS {
		return fmt.Errorf("invalid indicator")
	}
	if err := c.advance(i.AvailableTimeNS); err != nil {
		return err
	}
	i.QualityFlags = append([]string(nil), i.QualityFlags...)
	key := riskKey(i.Symbol, i.Timeframe)
	c.indicators[key] = append(c.indicators[key], i)
	// A delayed indicator remains usable as context, but must not replace a
	// newer execution or indicator mark for portfolio valuation.
	if previous, ok := c.markTime[i.Symbol]; !ok || i.CutoffTimeNS >= previous {
		c.state.Prices[i.Symbol] = i.Price
		c.markExpiry[i.Symbol] = i.ValidUntilNS
		c.markTime[i.Symbol] = i.CutoffTimeNS
		if err := c.revalue(); err != nil {
			return err
		}
	}
	*r = *c
	return nil
}

// OnFill consumes an incremental fill. The caller declares that the instrument
// is USD-quoted; no currency conversion or base-asset fee is supported. Its
// execution price is a mark valid through validUntilNS, inclusive.
func (r *StatefulRiskOverlay) OnFill(symbol string, side int, qty, price float64, currency string, tsNS, validUntilNS int64) error {
	c := r.clone()
	if symbol == "" || currency != c.cfg.CashCurrency || (side != 1 && side != -1) || !sizingFinite(qty) || qty < 0 || !sizingFinite(price) || price <= 0 || validUntilNS < tsNS {
		return fmt.Errorf("invalid fill or currency")
	}
	if err := c.advance(tsNS); err != nil {
		return err
	}
	if qty == 0 {
		*r = *c
		return nil
	}
	signed := float64(side) * qty
	current, exists := c.state.Positions[symbol]
	if !exists {
		c.positionOrder = append(c.positionOrder, symbol)
	}
	a, _ := new(big.Rat).SetString(strconv.FormatFloat(current, 'f', -1, 64))
	b, _ := new(big.Rat).SetString(strconv.FormatFloat(signed, 'f', -1, 64))
	position, _ := new(big.Rat).Add(a, b).Float64()
	if !sizingFinite(position) {
		return fmt.Errorf("position overflow")
	}
	if ((current > 0 && signed < 0) || (current < 0 && signed > 0)) && math.Abs(position) <= 4*math.Max(math.Nextafter(math.Abs(current), math.Inf(1))-math.Abs(current), math.Nextafter(math.Abs(signed), math.Inf(1))-math.Abs(signed)) {
		position = 0
	}
	notional := signed * price
	if !sizingFinite(notional) {
		return fmt.Errorf("fill notional overflow")
	}
	c.state.Positions[symbol] = position
	c.state.Prices[symbol] = price
	c.markExpiry[symbol] = validUntilNS
	c.markTime[symbol] = tsNS
	c.state.Cash -= notional
	if err := c.revalue(); err != nil {
		return err
	}
	*r = *c
	return nil
}

// ApplyFee deducts positive fees in the configured USD cash currency, including
// below zero. Unlike Python legacy's cash floor, this preserves the full cost.
func (r *StatefulRiskOverlay) ApplyFee(fee float64, currency string, tsNS int64) error {
	c := r.clone()
	if !sizingFinite(fee) || currency != c.cfg.CashCurrency {
		return fmt.Errorf("invalid fee or currency")
	}
	if err := c.advance(tsNS); err != nil {
		return err
	}
	if fee > 0 {
		cash := c.state.Cash - fee
		if !sizingFinite(cash) {
			return fmt.Errorf("fee overflow")
		}
		c.state.Cash = cash
		if err := c.revalue(); err != nil {
			return err
		}
	}
	*r = *c
	return nil
}

// State returns a detached view. Equity is the last complete valuation when
// EquityValid is false; it must not be used to authorize risk.
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

// Apply processes a target at ProcessTimeNS, which must be at or after its
// market cutoff and nondecreasing across events. Indicator cutoffs cannot exceed
// the target cutoff; indicator availability and mark expiry use process time.
// The latest eligible indicator is selected, including older still-valid entries;
// expired entries are discarded as the processing clock advances.
// Portfolio marks and fills reflect events processed by that time, even when
// their timestamps are later than the target's market cutoff. Target days may
// not move backward across streams, preserving the portfolio's daily anchor.
// Each symbol/timeframe accepts at most one target at any given cutoff; targets
// for different streams may share a cutoff and each advances portfolio counters.
func (r *StatefulRiskOverlay) Apply(t StatefulRiskTarget) (StatefulRiskDecision, StatefulRiskSnapshot, error) {
	c := r.clone()
	if t.Portfolio != c.cfg.Portfolio {
		return StatefulRiskDecision{}, StatefulRiskSnapshot{}, fmt.Errorf("unsupported portfolio")
	}
	if t.CutoffTimeNS <= 0 || t.ProcessTimeNS < t.CutoffTimeNS {
		return StatefulRiskDecision{}, StatefulRiskSnapshot{}, fmt.Errorf("invalid target cutoff or processing time")
	}
	if err := c.advance(t.ProcessTimeNS); err != nil {
		return StatefulRiskDecision{}, StatefulRiskSnapshot{}, err
	}
	key := riskKey(t.Symbol, t.Timeframe)
	if last, ok := c.targetCutoffs[key]; ok && t.CutoffTimeNS <= last {
		return StatefulRiskDecision{}, StatefulRiskSnapshot{}, fmt.Errorf("duplicate or out-of-order target")
	}
	var indicator StatefulRiskIndicator
	found := false
	for _, candidate := range c.indicators[key] {
		if candidate.CutoffTimeNS <= t.CutoffTimeNS && candidate.AvailableTimeNS <= t.ProcessTimeNS && candidate.ValidUntilNS >= t.ProcessTimeNS && (!found || candidate.CutoffTimeNS > indicator.CutoffTimeNS || (candidate.CutoffTimeNS == indicator.CutoffTimeNS && candidate.AvailableTimeNS >= indicator.AvailableTimeNS)) {
			indicator, found = candidate, true
		}
	}
	if !found {
		return StatefulRiskDecision{}, StatefulRiskSnapshot{}, fmt.Errorf("missing, future or expired indicator")
	}
	if !c.state.EquityValid {
		return StatefulRiskDecision{}, StatefulRiskSnapshot{}, fmt.Errorf("portfolio equity unavailable due to expired marks")
	}
	for symbol, qty := range c.state.Positions {
		if qty != 0 && (c.state.Prices[symbol] <= 0 || c.markExpiry[symbol] < t.ProcessTimeNS) {
			return StatefulRiskDecision{}, StatefulRiskSnapshot{}, fmt.Errorf("missing or expired position mark: %s", symbol)
		}
	}
	if c.state.Prices[t.Symbol] <= 0 || c.markExpiry[t.Symbol] < t.ProcessTimeNS {
		return StatefulRiskDecision{}, StatefulRiskSnapshot{}, fmt.Errorf("missing or expired target mark")
	}
	d, s, err := c.apply(t, indicator)
	if err != nil {
		return StatefulRiskDecision{}, StatefulRiskSnapshot{}, err
	}
	c.targetCutoffs[key] = t.CutoffTimeNS
	*r = *c
	return d, s, nil
}
func (r *StatefulRiskOverlay) apply(t StatefulRiskTarget, i StatefulRiskIndicator) (StatefulRiskDecision, StatefulRiskSnapshot, error) {
	if t.Symbol == "" || t.Timeframe == "" || !sizingFinite(t.Quantity) || !sizingFinite(t.Leverage) || t.Leverage < 0 || r.state.Equity < 0 {
		return StatefulRiskDecision{}, StatefulRiskSnapshot{}, fmt.Errorf("invalid target or negative equity")
	}
	before := r.State()
	s := &r.state
	dd := math.Max(0, (s.HighWaterMark-s.Equity)/s.HighWaterMark)
	if !sizingFinite(dd) {
		return StatefulRiskDecision{}, StatefulRiskSnapshot{}, fmt.Errorf("drawdown overflow")
	}
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
				ret := (s.EquityHistory[i] - s.EquityHistory[i-1]) / s.EquityHistory[i-1]
				if !sizingFinite(ret) {
					return StatefulRiskDecision{}, StatefulRiskSnapshot{}, fmt.Errorf("return overflow")
				}
				returns = append(returns, ret)
			}
		}
		if len(returns) > 0 {
			avg := 0.0
			for _, v := range returns {
				avg += v
			}
			if !sizingFinite(avg) {
				return StatefulRiskDecision{}, StatefulRiskSnapshot{}, fmt.Errorf("return sum overflow")
			}
			avg /= float64(len(returns))
			variance := 0.0
			for _, v := range returns {
				variance += (v - avg) * (v - avg)
			}
			if !sizingFinite(variance) {
				return StatefulRiskDecision{}, StatefulRiskSnapshot{}, fmt.Errorf("return variance overflow")
			}
			variance /= float64(len(returns))
			sharpe := 0.0
			if variance > 1e-15 {
				sharpe = avg / math.Sqrt(variance) * math.Sqrt(float64(len(returns)))
			}
			if !sizingFinite(sharpe) {
				return StatefulRiskDecision{}, StatefulRiskSnapshot{}, fmt.Errorf("sharpe overflow")
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
	if s.HasDay && day < s.DayID {
		return StatefulRiskDecision{}, StatefulRiskSnapshot{}, fmt.Errorf("target day moved backward")
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
	{
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
	price := s.Prices[t.Symbol]
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
	for _, scaled := range []float64{cap * 1e4, s.Equity * 100, s.HighWaterMark * 100, dd * 1e6} {
		if !sizingFinite(scaled) {
			return StatefulRiskDecision{}, StatefulRiskSnapshot{}, fmt.Errorf("snapshot rounding overflow")
		}
	}
	decision := StatefulRiskDecision{t, revised, math.RoundToEven(cap*1e4) / 1e4, result.Flags}
	snap := StatefulRiskSnapshot{r.cfg.Portfolio, t.CutoffTimeNS, math.RoundToEven(s.Equity*100) / 100, math.RoundToEven(s.HighWaterMark*100) / 100, math.RoundToEven(dd*1e6) / 1e6, decision.LeverageCap, reduce, result.Flags}
	return decision, snap, nil
}
