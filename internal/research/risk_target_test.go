package research

import (
	"encoding/json"
	"math"
	"os"
	"reflect"
	"testing"
)

func TestRiskTargetPythonGolden(t *testing.T) {
	data, err := os.ReadFile("testdata/risk_target_golden.json")
	if err != nil {
		t.Fatal(err)
	}
	var fixture struct {
		Version int `json:"version"`
		Cases   []struct {
			Name     string           `json:"name"`
			Input    RiskTargetInput  `json:"input"`
			Expected RiskTargetResult `json:"expected"`
		} `json:"cases"`
	}
	if err := json.Unmarshal(data, &fixture); err != nil {
		t.Fatal(err)
	}
	if fixture.Version != 1 || len(fixture.Cases) == 0 {
		t.Fatal("invalid fixture")
	}
	for _, tc := range fixture.Cases {
		t.Run(tc.Name, func(t *testing.T) {
			got, err := ClampRiskTarget(tc.Input)
			if err != nil {
				t.Fatal(err)
			}
			if !reflect.DeepEqual(got, tc.Expected) {
				t.Fatalf("got %+v; want %+v", got, tc.Expected)
			}
		})
	}
}

func TestRiskTargetFlagsAndReasons(t *testing.T) {
	in := RiskTargetInput{TargetQty: 4, TargetLeverage: 2, CurrentPosition: 2, Equity: 100, LeverageCap: 1, Price: 100, ReduceOnly: true, ReasonCodes: []string{"Z", "A", "Z"}, Flags: []string{"Z", "B", "B"}}
	got, err := ClampRiskTarget(in)
	if err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(got.ReasonCodes, []string{"A", "B", "REDUCE_ONLY_BLOCK_ADD", "Z"}) || !reflect.DeepEqual(got.Flags, []string{"B", "REDUCE_ONLY_BLOCK_ADD", "Z"}) {
		t.Fatalf("unexpected reasons: %+v", got)
	}
	if !reflect.DeepEqual(in.Flags, []string{"Z", "B", "B"}) {
		t.Fatal("input mutated")
	}
}

func TestRiskTargetRoundingCanExceedLimitsWithoutFlag(t *testing.T) {
	in := RiskTargetInput{TargetQty: 0.0000006, TargetLeverage: 0.99996, CurrentPosition: 0.0000006, Equity: 1, LeverageCap: 1, Price: 1, ReduceOnly: true}
	got, err := ClampRiskTarget(in)
	if err != nil {
		t.Fatal(err)
	}
	if got.TargetQty != 0.000001 || got.TargetQty <= in.CurrentPosition || got.TargetLeverage != 1 || !reflect.DeepEqual(got.Flags, []string{"OK"}) {
		t.Fatalf("unexpected rounding: %+v", got)
	}
	in.ReduceOnly = false
	in.LeverageCap = 0.99996
	in.TargetLeverage = 1
	got, err = ClampRiskTarget(in)
	if err != nil || got.TargetLeverage != 1 || got.TargetLeverage <= in.LeverageCap || !reflect.DeepEqual(got.Flags, []string{"OK"}) {
		t.Fatalf("unexpected leverage rounding: %+v, %v", got, err)
	}
}

func TestRiskTargetStrictBounds(t *testing.T) {
	for _, position := range []float64{-0.0000006, 0, 0.0000006, -1.2345678, 1.2345678} {
		for _, target := range []float64{-2, -0.0000006, 0, 0.0000006, 2} {
			in := RiskTargetInput{TargetQty: target, TargetLeverage: 1, CurrentPosition: position, Equity: 1, LeverageCap: 0.99996, Price: 1, ReduceOnly: true}
			got, err := ClampRiskTargetStrict(in)
			if err != nil {
				t.Fatal(err)
			}
			if math.Abs(got.TargetQty) > math.Abs(position) || math.Abs(got.TargetQty) > in.LeverageCap || got.TargetLeverage > in.LeverageCap || (position >= 0 && got.TargetQty < 0) || (position <= 0 && got.TargetQty > 0) {
				t.Fatalf("position %g target %g: %+v", position, target, got)
			}
		}
	}
}

func TestRiskTargetRejectsUnsupportedNumbers(t *testing.T) {
	tests := []struct {
		name   string
		change func(*RiskTargetInput)
	}{
		{"quantity nan", func(i *RiskTargetInput) { i.TargetQty = math.NaN() }},
		{"position infinity", func(i *RiskTargetInput) { i.CurrentPosition = math.Inf(-1) }},
		{"leverage infinity", func(i *RiskTargetInput) { i.TargetLeverage = math.Inf(1) }},
		{"equity nan", func(i *RiskTargetInput) { i.Equity = math.NaN() }},
		{"cap infinity", func(i *RiskTargetInput) { i.LeverageCap = math.Inf(1) }},
		{"price nan", func(i *RiskTargetInput) { i.Price = math.NaN() }},
		{"negative equity", func(i *RiskTargetInput) { i.Equity = -1 }},
		{"negative cap", func(i *RiskTargetInput) { i.LeverageCap = -1 }},
		{"negative leverage", func(i *RiskTargetInput) { i.TargetLeverage = -1 }},
		{"price below floor", func(i *RiskTargetInput) { i.Price = 0 }},
		{"notional overflow", func(i *RiskTargetInput) { i.Equity = math.MaxFloat64; i.LeverageCap = 2 }},
		{"division overflow", func(i *RiskTargetInput) { i.Equity = math.MaxFloat64; i.Price = 1e-9 }},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			in := RiskTargetInput{TargetQty: 1, TargetLeverage: 1, Equity: 1, LeverageCap: 1, Price: 1}
			tc.change(&in)
			got, err := ClampRiskTarget(in)
			if err == nil || !reflect.DeepEqual(got, RiskTargetResult{}) {
				t.Fatalf("got %+v, %v", got, err)
			}
		})
	}
}
