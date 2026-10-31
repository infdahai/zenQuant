package research

import (
	"encoding/json"
	"math"
	"os"
	"reflect"
	"testing"
)

func TestExitEnginePythonGolden(t *testing.T) {
	data, err := os.ReadFile("testdata/exit_engine_golden.json")
	if err != nil {
		t.Fatal(err)
	}
	var fixture struct {
		Version int `json:"version"`
		Cases   []struct {
			Name         string         `json:"name"`
			Position     ExitPosition   `json:"position"`
			State        ExitState      `json:"state"`
			Playbook     ExitPlaybook   `json:"playbook"`
			Indicators   ExitIndicators `json:"indicators"`
			Config       ExitConfig     `json:"config"`
			Expected     ExitDecision   `json:"expected"`
			TrailingStop float64        `json:"trailing_stop"`
		} `json:"cases"`
		Sequences []struct {
			Name     string       `json:"name"`
			Position ExitPosition `json:"position"`
			State    ExitState    `json:"state"`
			Playbook ExitPlaybook `json:"playbook"`
			Config   ExitConfig   `json:"config"`
			Steps    []struct {
				Indicators   ExitIndicators `json:"indicators"`
				Expected     ExitDecision   `json:"expected"`
				TrailingStop float64        `json:"trailing_stop"`
			} `json:"steps"`
		} `json:"sequences"`
	}
	if err := json.Unmarshal(data, &fixture); err != nil {
		t.Fatal(err)
	}
	if fixture.Version != 1 || len(fixture.Cases) == 0 {
		t.Fatal("invalid fixture")
	}
	for _, tc := range fixture.Cases {
		t.Run(tc.Name, func(t *testing.T) {
			before := tc.Position
			got, err := CheckExit(&tc.Position, tc.State, tc.Playbook, tc.Indicators, tc.Config)
			if err != nil {
				t.Fatal(err)
			}
			if !reflect.DeepEqual(got, tc.Expected) {
				t.Fatalf("decision got %+v; want %+v", got, tc.Expected)
			}
			before.TrailingStop = tc.TrailingStop
			if tc.Position != before {
				t.Fatalf("position got %+v; want %+v", tc.Position, before)
			}
		})
	}
	if len(fixture.Sequences) == 0 {
		t.Fatal("missing sequential fixtures")
	}
	for _, seq := range fixture.Sequences {
		t.Run(seq.Name, func(t *testing.T) {
			if len(seq.Steps) < 2 {
				t.Fatal("sequence needs multiple steps")
			}
			position := seq.Position
			for step, want := range seq.Steps {
				before := position
				got, err := CheckExit(&position, seq.State, seq.Playbook, want.Indicators, seq.Config)
				if err != nil {
					t.Fatalf("step %d: %v", step, err)
				}
				if !reflect.DeepEqual(got, want.Expected) {
					t.Fatalf("step %d: got %+v; want %+v", step, got, want.Expected)
				}
				before.TrailingStop = want.TrailingStop
				if position != before {
					t.Fatalf("step %d: position got %+v; want %+v", step, position, before)
				}
			}
		})
	}
}

func TestExitEngineRejectsTransactionally(t *testing.T) {
	horizon := 0
	tests := []struct {
		name   string
		change func(*ExitPosition, *ExitState, *ExitPlaybook, *ExitIndicators, *ExitConfig)
	}{
		{"nan", func(p *ExitPosition, _ *ExitState, _ *ExitPlaybook, _ *ExitIndicators, _ *ExitConfig) {
			p.EntryPrice = math.NaN()
		}},
		{"infinity", func(_ *ExitPosition, s *ExitState, _ *ExitPlaybook, _ *ExitIndicators, _ *ExitConfig) {
			s.TransitionRisk = math.Inf(1)
		}},
		{"zero direction", func(p *ExitPosition, _ *ExitState, _ *ExitPlaybook, _ *ExitIndicators, _ *ExitConfig) {
			p.Direction = 0
		}},
		{"negative bars", func(p *ExitPosition, _ *ExitState, _ *ExitPlaybook, _ *ExitIndicators, _ *ExitConfig) {
			p.BarsHeld = -1
		}},
		{"negative atr", func(_ *ExitPosition, _ *ExitState, _ *ExitPlaybook, i *ExitIndicators, _ *ExitConfig) { i.ATR = -1 }},
		{"distance overflow", func(_ *ExitPosition, _ *ExitState, b *ExitPlaybook, i *ExitIndicators, _ *ExitConfig) {
			b.TrailingStopATRMult = 2
			i.ATR = math.MaxFloat64
		}},
		{"initial overflow", func(p *ExitPosition, _ *ExitState, b *ExitPlaybook, i *ExitIndicators, _ *ExitConfig) {
			p.Direction = -1
			p.TrailingStop = 0
			p.EntryPrice = math.MaxFloat64
			b.TrailingStopATRMult = 1
			i.ATR = math.MaxFloat64
		}},
		{"ratchet overflow", func(p *ExitPosition, _ *ExitState, b *ExitPlaybook, i *ExitIndicators, _ *ExitConfig) {
			p.Direction = -1
			b.TrailingStopATRMult = 1
			i.Price = math.MaxFloat64
			i.ATR = math.MaxFloat64
		}},
		{"pnl overflow after mutation", func(p *ExitPosition, _ *ExitState, b *ExitPlaybook, i *ExitIndicators, c *ExitConfig) {
			p.CurrentQty = math.MaxFloat64
			b.TimeDecayBars = &horizon
			i.Price = 98
			c.TimeDecayEnabled = true
		}},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			p := ExitPosition{Direction: 1, EntryPrice: 100, CurrentQty: 1, TrailingStop: 90, BarsHeld: 2}
			s := ExitState{}
			b := ExitPlaybook{TrailingStopATRMult: 2}
			i := ExitIndicators{Price: 100, ATR: 2}
			c := ExitConfig{MinBarsBeforeTrail: 1, TransitionRiskExitThreshold: 0.8}
			tc.change(&p, &s, &b, &i, &c)
			original := p
			got, err := CheckExit(&p, s, b, i, c)
			if err == nil || !reflect.DeepEqual(got, ExitDecision{}) {
				t.Fatalf("got %+v, %v", got, err)
			}
			if math.Float64bits(p.Direction) != math.Float64bits(original.Direction) || math.Float64bits(p.EntryPrice) != math.Float64bits(original.EntryPrice) || math.Float64bits(p.CurrentQty) != math.Float64bits(original.CurrentQty) || math.Float64bits(p.TrailingStop) != math.Float64bits(original.TrailingStop) || p.BarsHeld != original.BarsHeld {
				t.Fatalf("position changed: %+v", p)
			}
		})
	}
	if _, err := CheckExit(nil, ExitState{}, ExitPlaybook{}, ExitIndicators{}, ExitConfig{}); err == nil {
		t.Fatal("nil position accepted")
	}
}
