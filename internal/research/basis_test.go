package research

import (
	"encoding/json"
	"math"
	"os"
	"reflect"
	"testing"
)

func TestBasisPythonSynthetic(t *testing.T) {
	data, err := os.ReadFile("testdata/basis_golden.json")
	if err != nil {
		t.Fatal(err)
	}
	var f struct {
		Spot, Perp []BasisPrice
		Window     int
		MinSamples int `json:"min_samples"`
		Scores     []struct {
			Values     []float64 `json:"values"`
			Current    float64   `json:"current"`
			MinSamples int       `json:"min_samples"`
			Expected   float64   `json:"expected"`
		} `json:"scores"`
		Lookups []struct {
			Cutoff   int64 `json:"cutoff_ns"`
			Snapshot BasisSnapshot
		}
	}
	if err = json.Unmarshal(data, &f); err != nil {
		t.Fatal(err)
	}
	s, err := NewBasisStore(f.Spot, f.Perp, f.Window, f.MinSamples)
	if err != nil {
		t.Fatal(err)
	}
	for _, score := range f.Scores {
		got, err := RobustZ(score.Values, score.Current, score.MinSamples)
		if err != nil || got != score.Expected {
			t.Errorf("score %g: got %g, %v; want %g", score.Current, got, err, score.Expected)
		}
	}
	if len(f.Scores) != 2 {
		t.Fatal("missing overflow score fixtures")
	}
	for _, row := range f.Lookups {
		got, err := s.Lookup(row.Cutoff)
		if err != nil {
			t.Fatal(err)
		}
		if !reflect.DeepEqual(got, row.Snapshot) {
			t.Errorf("cutoff %d: got %+v want %+v", row.Cutoff, got, row.Snapshot)
		}
	}
}

func TestBasisInputAndOverflow(t *testing.T) {
	good := []BasisPrice{{1, 100}}
	for _, tc := range []struct {
		spot, perp  []BasisPrice
		window, min int
	}{
		{good, good, 1, 1}, {good, good, 2, 0}, {good, good, 2, 2},
		{[]BasisPrice{{-1, 1}}, good, 2, 1}, {[]BasisPrice{{1, math.NaN()}}, good, 2, 1},
		{good, []BasisPrice{{1, math.Inf(1)}}, 2, 1},
		{good, []BasisPrice{{1, 0}}, 2, 1}, {good, []BasisPrice{{2, -1}}, 2, 1},
		{good, []BasisPrice{{1, -1}, {1, 100}}, 2, 1},
		{[]BasisPrice{{1, math.MaxFloat64}}, []BasisPrice{{1, -math.MaxFloat64}}, 2, 1},
		{[]BasisPrice{{1, math.SmallestNonzeroFloat64}}, []BasisPrice{{1, math.MaxFloat64}}, 2, 1},
	} {
		if _, err := NewBasisStore(tc.spot, tc.perp, tc.window, tc.min); err == nil {
			t.Errorf("accepted %+v", tc)
		}
	}
	s, _ := NewBasisStore(good, good, 2, 1)
	if _, err := s.Lookup(-1); err == nil {
		t.Error("accepted negative cutoff")
	}
}

func TestRobustZBoundaries(t *testing.T) {
	for _, tc := range []struct {
		values  []float64
		current float64
		min     int
		want    float64
	}{
		{[]float64{1, 2}, 2, 2, 0}, {[]float64{1, 1, 3}, 3, 2, 0},
		{[]float64{1, 2, 3, 100}, 100, 3, 6}, {[]float64{1, 2, 3, -100}, -100, 3, -6},
		{[]float64{1, 2, 3, 4}, 4, 3, 1.349},
	} {
		got, err := RobustZ(tc.values, tc.current, tc.min)
		if err != nil || math.Abs(got-tc.want) > 1e-12 {
			t.Errorf("%v: got %v, %v", tc, got, err)
		}
	}
	for _, tc := range []struct {
		values  []float64
		current float64
		min     int
	}{
		{[]float64{1}, 1, 0}, {[]float64{math.NaN()}, 1, 1}, {[]float64{1}, math.Inf(1), 1},
		{[]float64{math.MaxFloat64, math.MaxFloat64, 1}, 1, 2},
		{[]float64{-math.MaxFloat64, -math.MaxFloat64, math.MaxFloat64, 1}, 1, 3},
	} {
		if _, err := RobustZ(tc.values, tc.current, tc.min); err == nil {
			t.Errorf("accepted %+v", tc)
		}
	}
}
