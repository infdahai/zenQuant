package research

import (
	"encoding/json"
	"os"
	"reflect"
	"testing"
)

type traceRiskState struct {
	Cash          float64            `json:"cash"`
	Equity        float64            `json:"equity"`
	HighWaterMark float64            `json:"high_water_mark"`
	Positions     map[string]float64 `json:"positions"`
	Prices        map[string]float64 `json:"prices"`
	HasDay        bool               `json:"has_day"`
	DayID         int64              `json:"day_id"`
	DayEquity     float64            `json:"day_equity"`
	KillActive    bool               `json:"kill_active"`
	KillBars      int                `json:"kill_bars"`
	RecoveryBars  int                `json:"recovery_bars"`
	DegradedBars  int                `json:"degraded_bars"`
	EquityHistory []float64          `json:"equity_history"`
}
type traceRiskStep struct {
	Event struct {
		Type    string   `json:"type"`
		Price   float64  `json:"price"`
		Vol     float64  `json:"vol"`
		Crowd   float64  `json:"crowd"`
		Quality []string `json:"quality"`
		Side    int      `json:"side"`
		Qty     float64  `json:"qty"`
		Amount  float64  `json:"amount"`
		Day     int64    `json:"day"`
		Reduce  bool     `json:"reduce"`
	} `json:"event"`
	State  traceRiskState `json:"state"`
	Output *struct {
		Qty           float64  `json:"qty"`
		Leverage      float64  `json:"leverage"`
		Reduce        bool     `json:"reduce"`
		Reasons       []string `json:"reasons"`
		Flags         []string `json:"flags"`
		Cap           float64  `json:"cap"`
		Equity        float64  `json:"equity"`
		HighWaterMark float64  `json:"high_water_mark"`
		Drawdown      float64  `json:"drawdown"`
	} `json:"output"`
}

func TestStatefulRiskPythonTrace(t *testing.T) {
	data, err := os.ReadFile("testdata/stateful_risk_trace.json")
	if err != nil {
		t.Fatal(err)
	}
	var steps []traceRiskStep
	if err = json.Unmarshal(data, &steps); err != nil {
		t.Fatal(err)
	}
	cfg := DefaultStatefulRiskConfig()
	cfg.KillCooldownBars = 2
	cfg.KillRampBars = 3
	r, err := NewStatefulRiskOverlay(cfg)
	if err != nil {
		t.Fatal(err)
	}
	const base int64 = 1704067200000000000
	for n, step := range steps {
		e := step.Event
		switch e.Type {
		case "indicator":
			err = r.OnIndicator(StatefulRiskIndicator{Symbol: "BTC", Timeframe: "4h", Price: e.Price, RealizedVol: e.Vol, FundingOIZ: e.Crowd, QualityFlags: e.Quality})
		case "fill":
			err = r.OnFill("BTC", e.Side, e.Qty, e.Price)
		case "fee":
			err = r.ApplyFee(e.Amount)
		case "target":
			var d StatefulRiskDecision
			var s StatefulRiskSnapshot
			d, s, err = r.Apply(StatefulRiskTarget{IntentID: "a", Portfolio: "arc", Symbol: "BTC", Timeframe: "4h", CutoffTimeNS: base + e.Day*86400000000000, Quantity: e.Qty, Leverage: 2, ReduceOnly: e.Reduce, ReasonCodes: []string{"TEST"}})
			if err == nil {
				o := step.Output
				if d.Revised.Quantity != o.Qty || d.Revised.Leverage != o.Leverage || d.Revised.ReduceOnly != o.Reduce || !reflect.DeepEqual(d.Revised.ReasonCodes, o.Reasons) || !reflect.DeepEqual(d.Flags, o.Flags) || d.LeverageCap != o.Cap || s.Equity != o.Equity || s.HighWaterMark != o.HighWaterMark || s.Drawdown != o.Drawdown {
					t.Fatalf("step %d: got %+v %+v want %+v", n, d, s, o)
				}
			}
		default:
			t.Fatalf("unknown event %q", e.Type)
		}
		if err != nil {
			t.Fatalf("step %d: %v", n, err)
		}
		s := r.State()
		got := traceRiskState{s.Cash, s.Equity, s.HighWaterMark, s.Positions, s.Prices, s.HasDay, s.DayID, s.DayEquity, s.KillActive, s.KillBars, s.RecoveryBars, s.DegradedBars, s.EquityHistory}
		if len(got.EquityHistory) == 0 {
			got.EquityHistory = []float64{}
		}
		if !reflect.DeepEqual(got, step.State) {
			t.Fatalf("step %d: got %+v want %+v", n, got, step.State)
		}
	}
}
