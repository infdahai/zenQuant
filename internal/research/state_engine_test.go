package research

import (
	"encoding/json"
	"math"
	"os"
	"reflect"
	"testing"
)

type engineFixture struct {
	Overlay  bool                `json:"overlay"`
	Input    StateEngineInput    `json:"input"`
	Expected ResearchMarketState `json:"expected"`
}

func loadEngineFixture(t *testing.T) []engineFixture {
	t.Helper()
	b, err := os.ReadFile("testdata/state_engine_golden.json")
	if err != nil {
		t.Fatal(err)
	}
	var cases []engineFixture
	if err := json.Unmarshal(b, &cases); err != nil {
		t.Fatal(err)
	}
	return cases
}
func TestStateEnginePythonDifferential(t *testing.T) {
	engines := map[bool]*StateEngine{true: NewStateEngine(true), false: NewStateEngine(false)}
	for i, tc := range loadEngineFixture(t) {
		got, err := engines[tc.Overlay].Classify(tc.Input)
		if err != nil {
			t.Fatalf("case %d: %v", i, err)
		}
		if !reflect.DeepEqual(got, tc.Expected) {
			t.Fatalf("case %d got %+v want %+v", i, got, tc.Expected)
		}
	}
}
func TestStateEngineFailureAtomicAndFutureIsolation(t *testing.T) {
	cases := loadEngineFixture(t)
	first := cases[0].Input
	reference, subject := NewStateEngine(true), NewStateEngine(true)
	if _, err := reference.Classify(first); err != nil {
		t.Fatal(err)
	}
	if _, err := subject.Classify(first); err != nil {
		t.Fatal(err)
	}
	next := cases[3].Input
	bad := next
	bad.Values = make(map[string]float64)
	for k, v := range next.Values {
		bad.Values[k] = v
	}
	delete(bad.Values, "cvd_slope")
	badFuture := next
	badFuture.AvailableTimeNS = next.CutoffTimeNS + 1
	badDuplicate := first
	badNonfinite := next
	badNonfinite.Values = make(map[string]float64)
	for k, v := range next.Values {
		badNonfinite.Values[k] = v
	}
	badNonfinite.Values["momentum_roc_z"] = math.NaN()
	badFlag := next
	badFlag.QualityFlags = []string{" \t"}
	for _, in := range []StateEngineInput{bad, badFuture, badDuplicate, badNonfinite, badFlag} {
		if _, err := subject.Classify(in); err == nil {
			t.Fatal("invalid snapshot accepted")
		}
	}
	want, err := reference.Classify(next)
	if err != nil {
		t.Fatal(err)
	}
	got, err := subject.Classify(next)
	if err != nil || !reflect.DeepEqual(got, want) {
		t.Fatalf("failure changed history: %+v %+v %v", got, want, err)
	}
	// Replaying the same prefix before two different futures must yield the
	// same prefix outputs, including the interleaved independent sequences.
	left, right := NewStateEngine(true), NewStateEngine(true)
	for _, tc := range cases[:4] {
		a, err := left.Classify(tc.Input)
		if err != nil {
			t.Fatal(err)
		}
		b, err := right.Classify(tc.Input)
		if err != nil || !reflect.DeepEqual(a, b) {
			t.Fatalf("prefix mismatch: %v", err)
		}
	}
	future := cases[4].Input
	if _, err := left.Classify(future); err != nil {
		t.Fatal(err)
	}
	future.Values = make(map[string]float64)
	for k, v := range cases[4].Input.Values {
		future.Values[k] = v
	}
	future.Values["momentum_roc_z"] = -5
	if _, err := right.Classify(future); err != nil {
		t.Fatal(err)
	}
}
