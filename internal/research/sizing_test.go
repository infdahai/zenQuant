package research

import (
	"encoding/json"
	"math"
	"os"
	"reflect"
	"testing"
)

func TestSizingPythonGolden(t *testing.T) {
	data, err := os.ReadFile("testdata/sizing_golden.json")
	if err != nil {
		t.Fatal(err)
	}
	var fixture struct {
		Version int `json:"version"`
		Cases   []struct {
			Name       string           `json:"name"`
			State      SizingState      `json:"state"`
			Playbook   SizingPlaybook   `json:"playbook"`
			Indicators SizingIndicators `json:"indicators"`
			Config     SizingConfig     `json:"config"`
			Expected   SizingDecision   `json:"expected"`
		} `json:"cases"`
		Rounding []struct {
			Value    float64 `json:"value"`
			Digits   int     `json:"digits"`
			Expected float64 `json:"expected"`
		} `json:"rounding"`
	}
	if err := json.Unmarshal(data, &fixture); err != nil {
		t.Fatal(err)
	}
	if fixture.Version != 1 || len(fixture.Cases) == 0 || len(fixture.Rounding) == 0 {
		t.Fatal("invalid fixture")
	}
	for _, tc := range fixture.Cases {
		t.Run(tc.Name, func(t *testing.T) {
			got, err := ComputeSizing(tc.State, tc.Playbook, 100000, tc.Indicators, tc.Config)
			if err != nil {
				t.Fatal(err)
			}
			if !reflect.DeepEqual(got, tc.Expected) {
				t.Fatalf("got %+v; want %+v", got, tc.Expected)
			}
		})
	}
	for _, tc := range fixture.Rounding {
		if got := sizingRound(tc.Value, tc.Digits); got != tc.Expected {
			t.Errorf("round(%v, %d) = %v; want %v", tc.Value, tc.Digits, got, tc.Expected)
		}
	}
}

func TestSizingRejectsUnsupportedNumbers(t *testing.T) {
	tests := []struct {
		name   string
		change func(*SizingState, *SizingPlaybook, *float64, *SizingIndicators, *SizingConfig)
	}{
		{"nan state", func(s *SizingState, _ *SizingPlaybook, _ *float64, _ *SizingIndicators, _ *SizingConfig) {
			s.DirectionalConfidence = math.NaN()
		}},
		{"infinite equity", func(_ *SizingState, _ *SizingPlaybook, e *float64, _ *SizingIndicators, _ *SizingConfig) {
			*e = math.Inf(1)
		}},
		{"infinite indicator", func(_ *SizingState, _ *SizingPlaybook, _ *float64, i *SizingIndicators, _ *SizingConfig) {
			i.RealizedVol = math.Inf(-1)
		}},
		{"nan config", func(_ *SizingState, _ *SizingPlaybook, _ *float64, _ *SizingIndicators, c *SizingConfig) {
			c.TargetVol1d = math.NaN()
		}},
		{"infinite playbook", func(_ *SizingState, p *SizingPlaybook, _ *float64, _ *SizingIndicators, _ *SizingConfig) {
			p.MaxLeverage = math.Inf(1)
		}},
		{"negative product", func(s *SizingState, _ *SizingPlaybook, _ *float64, _ *SizingIndicators, _ *SizingConfig) {
			s.DirectionalConfidence = -1
		}},
		{"confidence intermediate overflow", func(s *SizingState, _ *SizingPlaybook, _ *float64, _ *SizingIndicators, _ *SizingConfig) {
			s.DirectionalConfidence = math.MaxFloat64
			s.VolatilityConfidence = 2
			s.PositioningConfidence = 0
		}},
		{"base floor overflow", func(_ *SizingState, _ *SizingPlaybook, _ *float64, _ *SizingIndicators, c *SizingConfig) {
			c.TargetVol4h = math.MaxFloat64
			c.MinRealizedVolRatio = 2
		}},
		{"threshold overflow", func(_ *SizingState, _ *SizingPlaybook, _ *float64, _ *SizingIndicators, c *SizingConfig) {
			c.VolatilityHighMultiple = math.MaxFloat64
			c.TargetVol4h = 2
		}},
		{"stress overflow", func(_ *SizingState, _ *SizingPlaybook, _ *float64, _ *SizingIndicators, c *SizingConfig) {
			c.PositioningDiscountFloor = math.MaxFloat64
			c.VolatilityHighDiscount = 2
			c.VolatilityHighMultiple = 0
		}},
		{"leverage overflow", func(_ *SizingState, _ *SizingPlaybook, _ *float64, i *SizingIndicators, c *SizingConfig) {
			c.PositioningDiscountFloor = math.MaxFloat64
			i.RealizedVol = 0
			c.MinRealizedVolRatio = 0.01
		}},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			s := SizingState{DirectionalConfidence: 1, VolatilityConfidence: 1, PositioningConfidence: 1, QualityConfidence: 1}
			p := SizingPlaybook{PlaybookConfidence: 1, MaxLeverage: 3}
			e := 100.0
			i := SizingIndicators{RealizedVol: 0.6}
			c := DefaultSizingConfig()
			tc.change(&s, &p, &e, &i, &c)
			got, err := ComputeSizing(s, p, e, i, c)
			if err == nil || !reflect.DeepEqual(got, SizingDecision{}) {
				t.Fatalf("got decision %+v and error %v", got, err)
			}
		})
	}
}

func TestDefaultSizingConfig(t *testing.T) {
	data, err := os.ReadFile("testdata/sizing_golden.json")
	if err != nil {
		t.Fatal(err)
	}
	var fixture struct {
		Cases []struct {
			Config SizingConfig `json:"config"`
		} `json:"cases"`
	}
	if err := json.Unmarshal(data, &fixture); err != nil {
		t.Fatal(err)
	}
	if len(fixture.Cases) == 0 || fixture.Cases[0].Config != DefaultSizingConfig() {
		t.Fatal("default config differs from Python")
	}
}
