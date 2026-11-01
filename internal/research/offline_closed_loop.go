package research

import (
	"fmt"
	"math"
)

// OfflineDecisionInput is an explicitly supplied synthetic observation available
// at the 4h close. The caller asserts that both inputs contain only information
// available at AvailableTimeNS, which must equal the close; timestamps cannot
// prove their provenance. No 1d
// bias or derived market data is supplied by this loop.
type OfflineDecisionInput struct {
	Indicator       StrategyIndicator
	State           StrategyState
	AvailableTimeNS int64
	ValidUntilNS    int64
}

// OfflineClosedLoopConfig is an offline research-only BTC/USD 4h experiment.
// Strategy equity is fixed by StrategyConfig and may differ from the risk
// starting equity; risk equity tracks fills, fees, and funding settlements.
// The leverage cap does not check margin or available funds. Fills occur at the
// same close with no slippage. Reversals close first; opening requires a new
// decision on a later bar. Fees can exceed the pre-fill leverage cap. This is
// not live execution or parity evidence.
type OfflineClosedLoopConfig struct {
	Replay         OfflineReplayConfig
	StrategyConfig StrategyFlowConfig
	RiskConfig     StatefulRiskConfig
	Inputs         []OfflineDecisionInput
}

type OfflineClosedLoopDecision struct {
	Evidence StrategyEvidence
	Risk     *StatefulRiskDecision
}

type OfflineClosedLoopResult struct {
	Replay    *OfflineReplayResult
	Decisions []OfflineClosedLoopDecision
	RiskState StatefulRiskState
}

func RunOfflineClosedLoop(c OfflineClosedLoopConfig) (*OfflineClosedLoopResult, error) {
	return runOfflineClosedLoop(c, nil)
}

func runOfflineClosedLoop(c OfflineClosedLoopConfig, simulate func(OfflineExecutionOrder, float64, float64, int64) (ExecutionReport, error)) (*OfflineClosedLoopResult, error) {
	if c.Replay.Symbol != "BTC" || c.Replay.ExecutionTimeframe != "4h" || len(c.Replay.Timeframes) != 1 || c.Replay.Timeframes[0] != "4h" || c.Replay.OnReport != nil {
		return nil, fmt.Errorf("closed loop requires BTC, 4h only and its own report callback")
	}
	strategy, err := NewStrategyFlow(c.StrategyConfig)
	if err != nil {
		return nil, err
	}
	risk, err := NewStatefulRiskOverlay(c.RiskConfig)
	if err != nil {
		return nil, err
	}
	if c.StrategyConfig.Portfolio != c.RiskConfig.Portfolio {
		return nil, fmt.Errorf("portfolio mismatch")
	}
	adapter, err := NewOfflineReportAdapter(strategy, risk)
	if err != nil {
		return nil, err
	}
	inputs := make(map[int64]OfflineDecisionInput, len(c.Inputs))
	for _, in := range c.Inputs {
		ts := in.Indicator.CutoffTimeNS
		if ts <= 0 || in.AvailableTimeNS != ts || in.ValidUntilNS < ts || in.Indicator.Symbol != "BTC" || in.Indicator.Timeframe != "4h" || in.State.Symbol != "BTC" || in.State.Timeframe != "4h" || in.State.CutoffTimeNS != ts {
			return nil, fmt.Errorf("invalid input identity or cutoff")
		}
		if _, ok := inputs[ts]; ok {
			return nil, fmt.Errorf("duplicate input cutoff")
		}
		inputs[ts] = in
	}
	result := &OfflineClosedLoopResult{}
	check := func(pos float64) error {
		strategyPos := 0.0
		if p := strategy.positions["BTC"]; p != nil {
			strategyPos = p.Direction * p.CurrentQty
		}
		riskPos := risk.State().Positions["BTC"]
		if math.Abs(strategyPos-pos) > 1e-10 || math.Abs(riskPos-pos) > 1e-10 {
			return fmt.Errorf("position mismatch: replay=%g strategy=%g risk=%g", pos, strategyPos, riskPos)
		}
		return nil
	}
	c.Replay.OnReport = func(report ExecutionReport, pos float64) error {
		if report.FilledQty > 0 {
			if err := adapter.Dispatch(report, "USD", report.TransactionTimeNS); err != nil {
				return err
			}
		}
		return check(pos)
	}
	seen := 0
	replay, err := runOfflineReplayIntents(c.Replay, func(s OfflineReplaySnapshot) ([]OfflineReplayIntent, error) {
		if err := check(s.PositionQty); err != nil {
			return nil, err
		}
		ts := s.Event.CloseTimeNS
		for _, settlement := range s.settlements {
			if err := risk.applyReplayFunding("BTC", settlement, ts); err != nil {
				return nil, fmt.Errorf("risk funding: %w", err)
			}
		}
		in, ok := inputs[ts]
		if !ok {
			return nil, fmt.Errorf("missing input at %d", ts)
		}
		if in.Indicator.Price != s.Event.Close {
			return nil, fmt.Errorf("indicator price differs from execution close")
		}
		seen++
		if err := strategy.Indicator(in.Indicator); err != nil {
			return nil, err
		}
		if err := risk.OnIndicator(StatefulRiskIndicator{Symbol: "BTC", Timeframe: "4h", Price: in.Indicator.Price, RealizedVol: in.Indicator.RealizedVol, FundingOIZ: in.Indicator.FundingOIZ, QualityFlags: in.State.QualityFlags, CutoffTimeNS: in.Indicator.CutoffTimeNS, AvailableTimeNS: in.AvailableTimeNS, ValidUntilNS: in.ValidUntilNS}); err != nil {
			return nil, err
		}
		evidence, err := strategy.State(in.State)
		if err != nil {
			return nil, err
		}
		decision := OfflineClosedLoopDecision{Evidence: *evidence}
		t := evidence.Target
		if t == nil {
			t = &StrategyTarget{IntentID: fmt.Sprintf("hold-%d", ts), Portfolio: c.StrategyConfig.Portfolio, Symbol: "BTC", Timeframe: "4h", CutoffTimeNS: ts, TargetQty: s.PositionQty}
		}
		riskState := risk.State()
		if !riskState.EquityValid || !finite(riskState.Equity) || riskState.Equity <= 0 {
			return nil, fmt.Errorf("risk equity unavailable")
		}
		leverage := math.Abs(t.TargetQty) * s.Event.Close / riskState.Equity
		if !finite(leverage) {
			return nil, fmt.Errorf("risk leverage overflow")
		}
		approved, _, err := risk.Apply(StatefulRiskTarget{IntentID: t.IntentID, Portfolio: t.Portfolio, Symbol: t.Symbol, Timeframe: t.Timeframe, CutoffTimeNS: ts, ProcessTimeNS: ts, Quantity: t.TargetQty, Leverage: leverage, ReduceOnly: t.ReduceOnly, ReasonCodes: t.ReasonCodes})
		if err != nil {
			return nil, err
		}
		decision.Risk = &approved
		result.Decisions = append(result.Decisions, decision)
		target := approved.Revised.Quantity
		current := s.PositionQty
		if approved.Revised.ReduceOnly {
			if current == 0 || target*current < 0 {
				target = 0
			}
			if math.Abs(target) > math.Abs(current) {
				target = current
			}
		}
		if current != 0 && target != 0 && math.Signbit(current) != math.Signbit(target) {
			side := -1
			if current < 0 {
				side = 1
			}
			return []OfflineReplayIntent{{Order: &OfflineExecutionOrder{ClientOrderID: t.IntentID + "-close", Symbol: "BTC", Side: side, Quantity: math.Abs(current), ReduceOnly: true}}}, nil
		}
		delta := target - current
		if delta == 0 {
			return nil, nil
		}
		side := 1
		if delta < 0 {
			side = -1
		}
		reduce := current != 0 && math.Signbit(delta) != math.Signbit(current)
		return []OfflineReplayIntent{{Order: &OfflineExecutionOrder{ClientOrderID: t.IntentID + "-order", Symbol: "BTC", Side: side, Quantity: math.Abs(delta), ReduceOnly: reduce}}}, nil
	}, simulate)
	if err != nil {
		return nil, err
	}
	if seen != len(inputs) {
		return nil, fmt.Errorf("input cutoff outside replay")
	}
	if err := check(replay.PositionQty); err != nil {
		return nil, err
	}
	result.Replay = replay
	result.RiskState = risk.State()
	return result, nil
}
