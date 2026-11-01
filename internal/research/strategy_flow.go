package research

import (
	"crypto/sha1"
	"fmt"
	"math"
	"math/big"
	"strconv"
	"strings"
)

// StrategyFlow implements the default legacy BTC 4h strategy before risk.
// Events must be delivered in order; a 4h state consumes an indicator with the
// same cutoff. It does not implement plugins, lower-timeframe promotion, risk
// approval, or the risk-final DecisionSnapshot contract. It is not concurrent.
type StrategyFlow struct {
	config        StrategyFlowConfig
	packs         map[string]StrategyIndicator
	stateCutoff   map[string]int64
	bias          map[string]StrategyState
	positions     map[string]*ExitPosition
	confirmations map[string]strategyConfirmation
}

type StrategyFlowConfig struct {
	Portfolio             string
	Equity                float64
	MinTradeQty           float64
	BiasMaxAgeBars        int
	EntryConfirmationBars int
	FlipConfirmationBars  int
}

func DefaultStrategyFlowConfig() StrategyFlowConfig {
	return StrategyFlowConfig{"arc", 100000, .0001, 8, 1, 1}
}

type StrategyState struct {
	PlaybookState
	Regime           string   `json:"regime"`
	RegimeConfidence float64  `json:"regime_confidence"`
	QualityFlags     []string `json:"quality_flags"`
}
type StrategyIndicator struct {
	Symbol           string  `json:"symbol"`
	Timeframe        string  `json:"timeframe"`
	CutoffTimeNS     int64   `json:"cutoff_time_ns"`
	IndicatorVersion string  `json:"indicator_version"`
	FormulaHash      string  `json:"formula_hash"`
	Price            float64 `json:"price"`
	ATR              float64 `json:"atr"`
	RealizedVol      float64 `json:"realized_vol"`
	FundingOIZ       float64 `json:"funding_oi_z"`
	CVDSlope         float64 `json:"cvd_slope"`
	OBLiqZ           float64 `json:"ob_liq_z"`
	StructureState   string  `json:"structure_state"`
	MomentumROCZ     float64 `json:"momentum_roc_z"`
}
type StrategyFill struct {
	Symbol                string
	Side, Quantity, Price float64
}
type StrategyTarget struct {
	IntentID         string   `json:"intent_id"`
	Portfolio        string   `json:"portfolio"`
	Symbol           string   `json:"symbol"`
	Timeframe        string   `json:"timeframe"`
	CutoffTimeNS     int64    `json:"cutoff_time_ns"`
	TargetQty        float64  `json:"target_qty"`
	TargetLeverage   float64  `json:"target_leverage"`
	ReduceOnly       bool     `json:"reduce_only"`
	Regime           string   `json:"regime"`
	RegimeConfidence float64  `json:"regime_confidence"`
	ReasonCodes      []string `json:"reason_codes"`
	RealizedVol      float64  `json:"realized_vol"`
	ATR              float64  `json:"atr"`
}

// StrategyEvidence is pre-risk evidence, never an approved decision snapshot.
type StrategyReason struct {
	Portfolio    string   `json:"portfolio"`
	Symbol       string   `json:"symbol"`
	CutoffTimeNS int64    `json:"cutoff_time_ns"`
	Summary      string   `json:"summary"`
	ReasonCodes  []string `json:"reason_codes"`
}

type StrategyEvidence struct {
	Playbook     PlaybookSelection `json:"playbook"`
	Sizing       *SizingDecision   `json:"sizing"`
	Exit         *ExitDecision     `json:"exit"`
	Target       *StrategyTarget   `json:"target"`
	Reason       *StrategyReason   `json:"reason"`
	Confirmation string            `json:"confirmation"`
}
type strategyConfirmation struct {
	direction float64
	bars      int
}

func NewStrategyFlow(c StrategyFlowConfig) (*StrategyFlow, error) {
	if c.Portfolio == "" || !sizingFinite(c.Equity) || c.Equity <= 0 || !sizingFinite(c.MinTradeQty) || c.MinTradeQty <= 0 || c.BiasMaxAgeBars < 0 || c.EntryConfirmationBars < 1 || c.FlipConfirmationBars < 1 {
		return nil, fmt.Errorf("invalid strategy configuration")
	}
	return &StrategyFlow{c, map[string]StrategyIndicator{}, map[string]int64{}, map[string]StrategyState{}, map[string]*ExitPosition{}, map[string]strategyConfirmation{}}, nil
}
func strategyKey(symbol, timeframe string) string { return symbol + "\x00" + timeframe }
func strategySymbol(symbol string) bool           { return strings.HasPrefix(strings.ToUpper(symbol), "BTC") }

// Indicator accepts monotonic bar-close data. Replacements and old bars fail.
func (f *StrategyFlow) Indicator(p StrategyIndicator) error {
	if !strategySymbol(p.Symbol) || p.Timeframe != "4h" || p.CutoffTimeNS <= 0 {
		return fmt.Errorf("unsupported indicator")
	}
	for _, v := range []float64{p.Price, p.ATR, p.RealizedVol, p.FundingOIZ, p.CVDSlope, p.OBLiqZ, p.MomentumROCZ} {
		if !sizingFinite(v) {
			return fmt.Errorf("nonfinite indicator")
		}
	}
	if p.Price <= 0 || p.ATR < 0 || p.RealizedVol < 0 {
		return fmt.Errorf("invalid indicator")
	}
	k := strategyKey(p.Symbol, p.Timeframe)
	if old, ok := f.packs[k]; ok && p.CutoffTimeNS <= old.CutoffTimeNS {
		return fmt.Errorf("indicator cutoff is not increasing")
	}
	if p.CutoffTimeNS <= f.stateCutoff[k] {
		return fmt.Errorf("indicator precedes processed state")
	}
	f.packs[k] = p
	return nil
}

// Fill applies actual incremental fills, including sub-minimum residuals.
func (f *StrategyFlow) Fill(fill StrategyFill) error {
	if !strategySymbol(fill.Symbol) || (fill.Side != 1 && fill.Side != -1) || !sizingFinite(fill.Quantity) || fill.Quantity <= 0 || !sizingFinite(fill.Price) || fill.Price <= 0 {
		return fmt.Errorf("invalid fill")
	}
	p := f.positions[fill.Symbol]
	delta := fill.Side * fill.Quantity
	if p == nil {
		f.positions[fill.Symbol] = &ExitPosition{Symbol: fill.Symbol, Timeframe: "4h", Direction: fill.Side, EntryPrice: fill.Price, CurrentQty: fill.Quantity}
		return nil
	}
	current := p.Direction * p.CurrentQty
	a, _ := new(big.Rat).SetString(strconv.FormatFloat(current, 'f', -1, 64))
	b, _ := new(big.Rat).SetString(strconv.FormatFloat(delta, 'f', -1, 64))
	next, _ := new(big.Rat).Add(a, b).Float64()
	ulp := func(x float64) float64 { return math.Abs(math.Nextafter(x, math.Inf(1)) - x) }
	if current*delta < 0 && math.Abs(next) <= 4*math.Max(ulp(current), ulp(delta)) {
		next = 0
	}
	if next == 0 {
		delete(f.positions, fill.Symbol)
		return nil
	}
	if current*next < 0 {
		f.positions[fill.Symbol] = &ExitPosition{Symbol: fill.Symbol, Timeframe: "4h", Direction: math.Copysign(1, next), EntryPrice: fill.Price, CurrentQty: math.Abs(next)}
		return nil
	}
	if math.Abs(next) > math.Abs(current) {
		p.EntryPrice = (p.EntryPrice*math.Abs(current) + fill.Price*math.Abs(delta)) / (math.Abs(current) + math.Abs(delta))
	}
	p.CurrentQty = math.Abs(next)
	return nil
}

// State processes 1d bias or a 4h close and returns pre-risk evidence.
func (f *StrategyFlow) State(s StrategyState) (*StrategyEvidence, error) {
	if !strategySymbol(s.Symbol) || (s.Timeframe != "1d" && s.Timeframe != "4h") || s.CutoffTimeNS <= 0 || !sizingFinite(s.RegimeConfidence) {
		return nil, fmt.Errorf("unsupported state")
	}
	k := strategyKey(s.Symbol, s.Timeframe)
	if s.CutoffTimeNS <= f.stateCutoff[k] {
		return nil, fmt.Errorf("state cutoff is not increasing")
	}
	pb, err := SelectPlaybook(s.PlaybookState, DefaultPlaybookConfig())
	if err != nil {
		return nil, err
	}
	if s.Timeframe == "1d" {
		s.QualityFlags = append([]string(nil), s.QualityFlags...)
		f.bias[s.Symbol] = s
		f.stateCutoff[k] = s.CutoffTimeNS
		return &StrategyEvidence{Playbook: pb}, nil
	}
	p, ok := f.packs[k]
	if !ok || p.CutoffTimeNS != s.CutoffTimeNS {
		return nil, fmt.Errorf("state requires indicator at same cutoff")
	}
	out := &StrategyEvidence{Playbook: pb}
	pos := f.positions[s.Symbol]
	var updated ExitPosition
	if pos != nil && pos.CurrentQty >= f.config.MinTradeQty {
		updated = *pos
		updated.BarsHeld++
		exit, err := CheckExit(&updated, ExitState{s.CutoffTimeNS, s.DirectionalState, s.VolatilityState, s.QualityState, s.TransitionRisk}, ExitPlaybook{pb.PlaybookID, pb.TrailingStopATRMult, pb.TimeDecayBars}, ExitIndicators{p.Price, p.ATR}, ExitConfig{true, true, 1, .8})
		if err != nil {
			return nil, err
		}
		out.Exit = &exit
		if exit.ExitType != "NONE" {
			qty := 0.0
			if exit.ExitUrgency != "IMMEDIATE" {
				qty = pos.Direction * pos.CurrentQty * math.Max(0, 1-exit.TargetReductionPct)
			}
			if math.Abs(qty) < f.config.MinTradeQty {
				qty = 0
			}
			leverage := 0.0
			if qty != 0 {
				leverage = math.Abs(qty*p.Price) / f.config.Equity
			}
			out.Target = f.target(p, s, qty, leverage, true, append([]string{"REGIME_" + s.Regime}, exit.ReasonCodes...))
			out.Reason = strategyReason(out.Target)
			delete(f.confirmations, s.Symbol)
			*pos = updated
			f.stateCutoff[k] = s.CutoffTimeNS
			return out, nil
		}
	}
	direction := legacyStrategyDirection(s.Regime, p)
	reasons := append([]string{"REGIME_" + s.Regime}, pb.ReasonCodes...)
	if bias, ok := f.bias[s.Symbol]; ok {
		reason := ""
		for _, flag := range bias.QualityFlags {
			if flag != "OK" {
				reason = "BIAS_QUALITY_DEGRADED"
				break
			}
		}
		if reason == "" && p.CutoffTimeNS < bias.CutoffTimeNS {
			reason = "BIAS_FUTURE"
		}
		if reason == "" && p.CutoffTimeNS-bias.CutoffTimeNS > int64(f.config.BiasMaxAgeBars)*4*3600*1e9 {
			reason = "BIAS_STALE"
		}
		if reason != "" {
			reasons = append(reasons, reason)
		} else if direction*bias.DirectionSignal < 0 {
			direction = 0
			reasons = append(reasons, "BIAS_DIRECTION_BLOCK")
		}
	}
	reduce := pb.PlaybookID == "CAPITAL_PRESERVATION"
	if reduce && direction != 0 {
		direction = 0
		reasons = append(reasons, "PLAYBOOK_DIRECTION_BLOCK")
	}
	sc := DefaultSizingConfig()
	sc.TargetVol5m = .70
	sc.TargetVol1h = .80
	sc.TargetVol4h = 1.10
	sc.TargetVol1d = 1.30
	sizing, err := ComputeSizing(SizingState{s.Symbol, s.Timeframe, s.CutoffTimeNS, s.DirectionalConfidence, s.VolatilityState, s.VolatilityConfidence, s.PositioningConfidence, s.QualityState, s.QualityConfidence}, SizingPlaybook{pb.PlaybookConfidence, pb.MaxLeverage}, f.config.Equity, SizingIndicators{p.RealizedVol, p.FundingOIZ}, sc)
	if err != nil {
		return nil, err
	}
	out.Sizing = &sizing
	if s.Regime == "STRESS" {
		reduce = true
	}
	qty := f.config.Equity * sizing.FinalLeverage * direction / p.Price
	if math.Abs(qty) < f.config.MinTradeQty {
		qty = 0
	}
	if direction > 0 {
		reasons = append(reasons, "TREND_LONG")
	} else if direction < 0 {
		reasons = append(reasons, "TREND_SHORT")
	} else {
		reasons = append(reasons, "NO_DIRECTION")
	}
	reasons = append(reasons, sizing.ReasonCodes...)
	target := f.target(p, s, qty, sizing.FinalLeverage, reduce, reasons)
	current := 0.0
	if pos != nil && pos.CurrentQty >= f.config.MinTradeQty {
		current = pos.Direction
	}
	td := 0.0
	if target.TargetQty != 0 {
		td = math.Copysign(1, target.TargetQty)
	}
	if reduce || td == 0 || td == current {
		delete(f.confirmations, s.Symbol)
	} else {
		required := f.config.EntryConfirmationBars
		if current != 0 {
			required = f.config.FlipConfirmationBars
		}
		if required > 1 {
			c := f.confirmations[s.Symbol]
			if c.direction != td {
				c = strategyConfirmation{td, 0}
			}
			c.bars++
			f.confirmations[s.Symbol] = c
			if c.bars < required {
				out.Confirmation = "INTENT_CONFIRMATION_PENDING"
				target = nil
			} else {
				target.ReasonCodes = append(target.ReasonCodes, fmt.Sprintf("INTENT_CONFIRMED_%d_BARS", required))
				out.Confirmation = "INTENT_CONFIRMED"
			}
		} else {
			delete(f.confirmations, s.Symbol)
		}
	}
	out.Target = target
	if target != nil {
		out.Reason = strategyReason(target)
	}
	if pos != nil && pos.CurrentQty >= f.config.MinTradeQty {
		*pos = updated
	}
	f.stateCutoff[k] = s.CutoffTimeNS
	return out, nil
}
func strategyReason(t *StrategyTarget) *StrategyReason {
	return &StrategyReason{t.Portfolio, t.Symbol, t.CutoffTimeNS, fmt.Sprintf("intent=%s regime=%s conf=%.2f lev=%.2f qty=%.6f", t.IntentID[:8], t.Regime, t.RegimeConfidence, t.TargetLeverage, t.TargetQty), append([]string(nil), t.ReasonCodes...)}
}

func legacyStrategyDirection(regime string, p StrategyIndicator) float64 {
	if regime == "STRESS" {
		return 0
	}
	if regime == "TREND" {
		if p.StructureState == "UPTREND" && p.MomentumROCZ > .3 {
			return 1
		}
		if p.StructureState == "DOWNTREND" && p.MomentumROCZ < -.3 {
			return -1
		}
		if p.CVDSlope > 0 {
			return 1
		}
		if p.CVDSlope < 0 {
			return -1
		}
		return 0
	}
	if math.Abs(p.CVDSlope) < .03 && math.Abs(p.OBLiqZ) > 1.2 {
		return -math.Copysign(1, p.OBLiqZ)
	}
	return 0
}
func (f *StrategyFlow) target(p StrategyIndicator, s StrategyState, qty, lev float64, reduce bool, reasons []string) *StrategyTarget {
	name := fmt.Sprintf("%s:%s:%s:%d:%s:%s", f.config.Portfolio, p.Symbol, p.Timeframe, p.CutoffTimeNS, p.IndicatorVersion, p.FormulaHash)
	namespace := []byte{0x6b, 0xa7, 0xb8, 0x11, 0x9d, 0xad, 0x11, 0xd1, 0x80, 0xb4, 0x00, 0xc0, 0x4f, 0xd4, 0x30, 0xc8}
	sum := sha1.Sum(append(namespace, []byte(name)...))
	sum[6] = (sum[6] & 0x0f) | 0x50
	sum[8] = (sum[8] & 0x3f) | 0x80
	id := fmt.Sprintf("%x-%x-%x-%x-%x", sum[:4], sum[4:6], sum[6:8], sum[8:10], sum[10:16])
	return &StrategyTarget{id, f.config.Portfolio, p.Symbol, p.Timeframe, p.CutoffTimeNS, sizingRound(qty, 6), sizingRound(lev, 4), reduce, s.Regime, sizingRound(s.RegimeConfidence, 4), reasons, p.RealizedVol, p.ATR}
}
