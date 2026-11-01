package research

import (
	"math"
	"testing"
)

func TestStatefulRiskStrictCapAndFillDecimals(t *testing.T) {
	cfg := DefaultStatefulRiskConfig()
	r, err := NewStatefulRiskOverlay(cfg)
	if err != nil {
		t.Fatal(err)
	}
	for _, q := range []float64{0.1, 0.2} {
		if err = r.OnFill("BTC", 1, q, 100); err != nil {
			t.Fatal(err)
		}
	}
	if r.State().Positions["BTC"] != 0.3 {
		t.Fatalf("decimal fills: %+v", r.State())
	}
	d, _, err := r.Apply(StatefulRiskTarget{Symbol: "BTC", Timeframe: "4h", Quantity: 0.1234569, Leverage: 1.12349})
	if err != nil {
		t.Fatal(err)
	}
	// Python rounds these to 0.123457 and 1.1235. V2 truncates toward zero.
	if d.Revised.Quantity != 0.123456 || d.Revised.Leverage != 1.1234 {
		t.Fatalf("strict rounding: %+v", d)
	}
	if err = r.OnFill("BTC", -1, 0.3, 100); err != nil || r.State().Positions["BTC"] != 0 {
		t.Fatalf("close: %v %+v", err, r.State())
	}
}

func TestStatefulRiskRejectsInvalidAndPreservesState(t *testing.T) {
	r, _ := NewStatefulRiskOverlay(DefaultStatefulRiskConfig())
	if err := r.OnFill("BTC", 1, math.NaN(), 100); err == nil {
		t.Fatal("accepted NaN fill")
	}
	if _, _, err := r.Apply(StatefulRiskTarget{Symbol: "BTC", Timeframe: "4h", Quantity: math.NaN()}); err == nil {
		t.Fatal("accepted NaN target")
	}
	if r.State().Equity != 100000 || len(r.State().Positions) != 0 {
		t.Fatalf("state changed: %+v", r.State())
	}
}
