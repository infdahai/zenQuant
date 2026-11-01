package research

import (
	"encoding/json"
	"os"
	"reflect"
	"testing"
)

type strategyFixtureStep struct {
	Kind      string            `json:"kind"`
	Indicator StrategyIndicator `json:"indicator"`
	State     StrategyState     `json:"state"`
	Side      float64           `json:"side"`
	Quantity  float64           `json:"quantity"`
	Price     float64           `json:"price"`
	Expected  struct {
		Playbook     PlaybookSelection `json:"playbook"`
		Sizing       *SizingDecision   `json:"sizing"`
		Exit         *ExitDecision     `json:"exit"`
		Target       *StrategyTarget   `json:"target"`
		Reason       *StrategyReason   `json:"reason"`
		Confirmation string            `json:"confirmation"`
	} `json:"expected"`
}

func TestStrategyFlowPythonReference(t *testing.T) {
	data, err := os.ReadFile("testdata/strategy_flow_golden.json")
	if err != nil {
		t.Fatal(err)
	}
	var steps []strategyFixtureStep
	if err = json.Unmarshal(data, &steps); err != nil {
		t.Fatal(err)
	}
	c := DefaultStrategyFlowConfig()
	c.EntryConfirmationBars = 2
	c.FlipConfirmationBars = 2
	flow, err := NewStrategyFlow(c)
	if err != nil {
		t.Fatal(err)
	}
	for i, step := range steps {
		if step.Kind == "fill" {
			if err = flow.Fill(StrategyFill{"BTCUSDT_PERP", step.Side, step.Quantity, step.Price}); err != nil {
				t.Fatalf("step %d: %v", i, err)
			}
			continue
		}
		if err = flow.Indicator(step.Indicator); err != nil {
			t.Fatalf("step %d: %v", i, err)
		}
		got, err := flow.State(step.State)
		if err != nil {
			t.Fatalf("step %d: %v", i, err)
		}
		want := StrategyEvidence{step.Expected.Playbook, step.Expected.Sizing, step.Expected.Exit, step.Expected.Target, step.Expected.Reason, step.Expected.Confirmation}
		if !reflect.DeepEqual(*got, want) {
			a, _ := json.Marshal(got)
			b, _ := json.Marshal(want)
			t.Errorf("step %d\ngot  %s\nwant %s", i, a, b)
		}
	}
}
func TestStrategyFlowCutoffsAndBias(t *testing.T) {
	flow, _ := NewStrategyFlow(DefaultStrategyFlowConfig())
	s := StrategyState{PlaybookState: PlaybookState{Symbol: "BTCUSDT_PERP", Timeframe: "1d", CutoffTimeNS: 20, DirectionalState: "UPTREND", DirectionalConfidence: .8, VolatilityState: "NORMAL", VolatilityConfidence: .8, PositioningState: "HEALTHY", PositioningConfidence: .8, QualityState: "TRADABLE", QualityConfidence: .8, DirectionSignal: 1}, Regime: "TREND", RegimeConfidence: .8, QualityFlags: []string{"OK"}}
	if _, err := flow.State(s); err != nil {
		t.Fatal(err)
	}
	s.Timeframe = "4h"
	s.CutoffTimeNS = 10
	p := StrategyIndicator{Symbol: s.Symbol, Timeframe: "4h", CutoffTimeNS: 11, Price: 100, RealizedVol: .8, CVDSlope: -.1}
	if err := flow.Indicator(p); err != nil {
		t.Fatal(err)
	}
	if _, err := flow.State(s); err == nil {
		t.Fatal("future indicator accepted")
	}
	s.CutoffTimeNS = 11
	got, err := flow.State(s)
	if err != nil {
		t.Fatal(err)
	}
	if got.Target == nil || got.Target.TargetQty >= 0 {
		t.Fatalf("future bias affected decision: %+v", got.Target)
	}
	if _, err = flow.State(s); err == nil {
		t.Fatal("duplicate state accepted")
	}
	if err = flow.Indicator(p); err == nil {
		t.Fatal("duplicate indicator accepted")
	}
}
func TestStrategyFlowActualResidualFill(t *testing.T) {
	flow, _ := NewStrategyFlow(DefaultStrategyFlowConfig())
	for _, v := range []StrategyFill{{"BTCUSDT_PERP", 1, .00005, 100}, {"BTCUSDT_PERP", 1, .00012, 100}, {"BTCUSDT_PERP", -1, .000165, 100}} {
		if err := flow.Fill(v); err != nil {
			t.Fatal(err)
		}
	}
	if p := flow.positions["BTCUSDT_PERP"]; p == nil || p.CurrentQty != .000005 {
		t.Fatalf("lost actual residual: %+v", p)
	}
	if err := flow.Fill(StrategyFill{"BTCUSDT_PERP", -1, .000005, 100}); err != nil {
		t.Fatal(err)
	}
	if flow.positions["BTCUSDT_PERP"] != nil {
		t.Fatal("position did not flatten")
	}
}

func TestStrategyFlowBiasFreshnessAndQuality(t *testing.T) {
	for _, tc := range []struct {
		name    string
		cutoff  int64
		flags   []string
		reason  string
		qtyZero bool
	}{
		{"opposing bias", 10, []string{"OK"}, "BIAS_DIRECTION_BLOCK", true},
		{"stale bias", 10 + 9*4*3600*1e9, []string{"OK"}, "BIAS_STALE", false},
		{"degraded bias", 10, []string{"DATA_STALE"}, "BIAS_QUALITY_DEGRADED", false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			f, _ := NewStrategyFlow(DefaultStrategyFlowConfig())
			s := StrategyState{PlaybookState: PlaybookState{Symbol: "BTCUSDT_PERP", Timeframe: "1d", CutoffTimeNS: 10, DirectionalState: "UPTREND", DirectionalConfidence: .8, VolatilityState: "NORMAL", VolatilityConfidence: .8, PositioningState: "HEALTHY", PositioningConfidence: .8, QualityState: "TRADABLE", QualityConfidence: .8, DirectionSignal: 1}, Regime: "TREND", RegimeConfidence: .8, QualityFlags: tc.flags}
			if _, err := f.State(s); err != nil {
				t.Fatal(err)
			}
			s.Timeframe = "4h"
			s.CutoffTimeNS = tc.cutoff
			p := StrategyIndicator{Symbol: s.Symbol, Timeframe: "4h", CutoffTimeNS: tc.cutoff, Price: 100, RealizedVol: .8, CVDSlope: -.1}
			if err := f.Indicator(p); err != nil {
				t.Fatal(err)
			}
			got, err := f.State(s)
			if err != nil {
				t.Fatal(err)
			}
			if (got.Target.TargetQty == 0) != tc.qtyZero {
				t.Fatalf("unexpected target %+v", got.Target)
			}
			found := false
			for _, reason := range got.Target.ReasonCodes {
				if reason == tc.reason {
					found = true
				}
			}
			if !found {
				t.Fatalf("missing %s: %+v", tc.reason, got.Target)
			}
		})
	}
}
