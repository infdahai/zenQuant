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
			// Version 1 records Python's historical nearest rounding. These
			// cases intentionally differ under the strict version 2 contract.
			divergences := map[string][2]float64{
				"round_above_limit":  {1.234567, 1},
				"round_binary_tie":   {0.123456, 1.2344},
				"round_reduce_only":  {0, 0.9999},
				"round_leverage_cap": {0.1, 0.9999},
			}
			if values, ok := divergences[tc.Name]; ok {
				if got.TargetQty == tc.Expected.TargetQty && got.TargetLeverage == tc.Expected.TargetLeverage {
					t.Fatal("expected intentional divergence from Python v1")
				}
				tc.Expected.TargetQty, tc.Expected.TargetLeverage = values[0], values[1]
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

func TestRiskTargetQuantizationRespectsLimits(t *testing.T) {
	for _, sign := range []float64{-1, 1} {
		in := RiskTargetInput{TargetQty: sign * 0.0000006, TargetLeverage: 0.99996, CurrentPosition: sign * 0.0000006, Equity: 1, LeverageCap: 1, Price: 1, ReduceOnly: true}
		got, err := ClampRiskTarget(in)
		if err != nil || got.TargetQty != 0 || got.TargetLeverage != 0.9999 {
			t.Fatalf("tiny position: %+v, %v", got, err)
		}
		in = RiskTargetInput{TargetQty: sign * 2, TargetLeverage: 1, Equity: 1.2345676, LeverageCap: 0.99996, Price: 0.99996}
		got, err = ClampRiskTarget(in)
		if err != nil || got.TargetQty != sign*1.234567 || got.TargetLeverage != 0.9999 {
			t.Fatalf("quantity cap: %+v, %v", got, err)
		}
		in.ReduceOnly, in.CurrentPosition = true, -sign
		got, err = ClampRiskTarget(in)
		if err != nil || got.TargetQty != 0 || !reflect.DeepEqual(got.Flags, []string{"REDUCE_ONLY_BLOCK_ADD"}) {
			t.Fatalf("flip: %+v, %v", got, err)
		}
	}
}

func TestRiskTargetQuantizationFloatingPointEdges(t *testing.T) {
	for _, boundary := range []float64{1e-6, 0.1, 1.234567, 1e6, 1e12} {
		for _, value := range []float64{math.Nextafter(boundary, 0), boundary, math.Nextafter(boundary, math.Inf(1))} {
			for _, sign := range []float64{-1, 1} {
				in := RiskTargetInput{TargetQty: sign * value, TargetLeverage: 0.99996, CurrentPosition: sign * value, Equity: value, LeverageCap: 1, Price: 1, ReduceOnly: true}
				got, err := ClampRiskTarget(in)
				if err != nil || math.Abs(got.TargetQty) > value || got.TargetQty*sign < 0 || got.TargetLeverage > in.TargetLeverage || got.TargetLeverage > in.LeverageCap {
					t.Fatalf("value %.17g sign %v: %+v, %v", value, sign, got, err)
				}
			}
		}
	}
}

func TestRiskTargetQuantizationStable(t *testing.T) {
	for _, value := range []float64{1.0000045, 4503599627.3705015, -1.0000045, math.SmallestNonzeroFloat64} {
		got := riskQuantizeTowardZero(value, 1e6)
		if math.Abs(got) > math.Abs(value) || riskQuantizeTowardZero(got, 1e6) != got {
			t.Fatalf("unstable quantization %.17g -> %.17g -> %.17g", value, got, riskQuantizeTowardZero(got, 1e6))
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
