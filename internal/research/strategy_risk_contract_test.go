package research

import (
	"math"
	"testing"
)

// This offline contract supplies target mapping, USD currency and mark validity.
// It does not establish live parity.
func TestManuallyDrivenStrategyRiskPartialFillContract(t *testing.T) {
	const symbol = "BTCUSDT_PERP"
	const first int64 = 1704067200000000000
	const bar int64 = 4 * 60 * 60 * 1000000000
	flow, err := NewStrategyFlow(DefaultStrategyFlowConfig())
	if err != nil {
		t.Fatal(err)
	}
	cfg := DefaultStatefulRiskConfig()
	cfg.MaxLeverage = 0.1
	risk, err := NewStatefulRiskOverlay(cfg)
	if err != nil {
		t.Fatal(err)
	}

	decide := func(ts int64, volatility string) (*StrategyEvidence, StatefulRiskDecision) {
		t.Helper()
		p := StrategyIndicator{Symbol: symbol, Timeframe: "4h", CutoffTimeNS: ts, Price: 100, ATR: 1, RealizedVol: 0.8, CVDSlope: 0.1}
		s := StrategyState{PlaybookState: PlaybookState{Symbol: symbol, Timeframe: "4h", CutoffTimeNS: ts, DirectionalState: "UPTREND", DirectionalConfidence: 0.8, VolatilityState: volatility, VolatilityConfidence: 0.8, PositioningState: "HEALTHY", PositioningConfidence: 0.8, QualityState: "TRADABLE", QualityConfidence: 0.8, DirectionSignal: 1}, Regime: "TREND", RegimeConfidence: 0.8, QualityFlags: []string{"OK"}}
		if err := flow.Indicator(p); err != nil {
			t.Fatal(err)
		}
		e, err := flow.State(s)
		if err != nil {
			t.Fatal(err)
		}
		if e.Target == nil {
			t.Fatal("missing strategy target")
		}
		if err := risk.OnIndicator(StatefulRiskIndicator{Symbol: symbol, Timeframe: "4h", Price: p.Price, RealizedVol: p.RealizedVol, FundingOIZ: p.FundingOIZ, QualityFlags: s.QualityFlags, CutoffTimeNS: ts, AvailableTimeNS: ts, ValidUntilNS: ts + bar}); err != nil {
			t.Fatal(err)
		}
		x := e.Target
		d, _, err := risk.Apply(StatefulRiskTarget{IntentID: x.IntentID, Portfolio: x.Portfolio, Symbol: x.Symbol, Timeframe: x.Timeframe, CutoffTimeNS: x.CutoffTimeNS, ProcessTimeNS: ts, Quantity: x.TargetQty, Leverage: x.TargetLeverage, ReduceOnly: x.ReduceOnly, ReasonCodes: x.ReasonCodes})
		if err != nil {
			t.Fatal(err)
		}
		for name, value := range map[string]float64{
			"strategy quantity": x.TargetQty, "strategy leverage": x.TargetLeverage,
			"risk quantity": d.Revised.Quantity, "risk leverage": d.Revised.Leverage,
		} {
			if math.IsNaN(value) || math.IsInf(value, 0) {
				t.Fatalf("%s is not finite: %v", name, value)
			}
		}
		return e, d
	}

	initial, approved := decide(first, "NORMAL")
	if initial.Target.TargetQty <= 0 || approved.Revised.Quantity <= 0 || approved.Revised.Quantity >= initial.Target.TargetQty {
		t.Fatalf("risk did not reduce long target: strategy=%+v risk=%+v", initial.Target, approved.Revised)
	}
	// Only half the approved target fills; the rest remains unfilled.
	fill, err := SimulateOfflineExecution(OfflineExecutionOrder{ClientOrderID: "partial-target", Symbol: symbol, Side: 1, Quantity: approved.Revised.Quantity / 2}, 100, 0.001, first+1)
	if err != nil {
		t.Fatal(err)
	}
	adapter, err := NewOfflineReportAdapter(flow, risk)
	if err != nil {
		t.Fatal(err)
	}
	if err := adapter.Dispatch(fill, "USD", first+bar); err != nil {
		t.Fatal(err)
	}
	if got := risk.State().Positions[symbol]; got != fill.FilledQty {
		t.Fatalf("risk position = %v, want actual fill %v", got, fill.FilledQty)
	}

	// Distorted volatility requests a gradual 50% exit of the filled position.
	next, revised := decide(first+bar, "DISTORTED")
	want := fill.FilledQty / 2
	if next.Exit == nil || next.Exit.ExitType != "REGIME_CHANGE" || math.Abs(next.Target.TargetQty-want) > 1e-6 || !next.Target.ReduceOnly {
		t.Fatalf("next strategy decision did not use partial fill %v: exit=%+v target=%+v", fill.FilledQty, next.Exit, next.Target)
	}
	if math.Abs(revised.Revised.Quantity-want) > 1e-6 || !revised.Revised.ReduceOnly {
		t.Fatalf("risk did not preserve actual-position reduction: %+v", revised.Revised)
	}
}
