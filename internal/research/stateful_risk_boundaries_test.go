package research

import (
	"math"
	"reflect"
	"testing"
)

func TestStatefulRiskStrictCapAndFillDecimals(t *testing.T) {
	cfg := DefaultStatefulRiskConfig()
	r, err := NewStatefulRiskOverlay(cfg)
	if err != nil {
		t.Fatal(err)
	}
	if err = r.OnIndicator(StatefulRiskIndicator{Symbol: "BTC", Timeframe: "4h", Price: 100, CutoffTimeNS: 1, AvailableTimeNS: 1, ValidUntilNS: 100}); err != nil {
		t.Fatal(err)
	}
	for _, q := range []float64{0.1, 0.2} {
		if err = r.OnFill("BTC", 1, q, 100, "USD", 2, 100); err != nil {
			t.Fatal(err)
		}
	}
	if r.State().Positions["BTC"] != 0.3 {
		t.Fatalf("decimal fills: %+v", r.State())
	}
	d, _, err := r.Apply(StatefulRiskTarget{Portfolio: "arc", Symbol: "BTC", Timeframe: "4h", CutoffTimeNS: 3, ProcessTimeNS: 3, Quantity: 0.1234569, Leverage: 1.12349})
	if err != nil {
		t.Fatal(err)
	}
	// Python rounds these to 0.123457 and 1.1235. V2 truncates toward zero.
	if d.Revised.Quantity != 0.123456 || d.Revised.Leverage != 1.1234 {
		t.Fatalf("strict rounding: %+v", d)
	}
	if err = r.OnFill("BTC", -1, 0.3, 100, "USD", 4, 100); err != nil || r.State().Positions["BTC"] != 0 {
		t.Fatalf("close: %v %+v", err, r.State())
	}
}

func TestStatefulRiskRejectsInvalidAndPreservesState(t *testing.T) {
	r, _ := NewStatefulRiskOverlay(DefaultStatefulRiskConfig())
	if err := r.OnFill("BTC", 1, math.NaN(), 100, "USD", 1, 100); err == nil {
		t.Fatal("accepted NaN fill")
	}
	if _, _, err := r.Apply(StatefulRiskTarget{Symbol: "BTC", Timeframe: "4h", Quantity: math.NaN()}); err == nil {
		t.Fatal("accepted NaN target")
	}
	if r.State().Equity != 100000 || len(r.State().Positions) != 0 {
		t.Fatalf("state changed: %+v", r.State())
	}
}

func TestStatefulRiskRejectedEventsAreTransactional(t *testing.T) {
	newOverlay := func() *StatefulRiskOverlay {
		r, err := NewStatefulRiskOverlay(DefaultStatefulRiskConfig())
		if err != nil {
			t.Fatal(err)
		}
		return r
	}
	indicator := func(price float64, ts, expiry int64) StatefulRiskIndicator {
		return StatefulRiskIndicator{Symbol: "BTC", Timeframe: "4h", Price: price, CutoffTimeNS: ts, AvailableTimeNS: ts, ValidUntilNS: expiry}
	}
	target := StatefulRiskTarget{Portfolio: "arc", Symbol: "BTC", Timeframe: "4h", CutoffTimeNS: 10, ProcessTimeNS: 10, Quantity: 1, Leverage: 1}
	tests := []struct {
		name   string
		setup  func(*StatefulRiskOverlay)
		reject func(*StatefulRiskOverlay) error
	}{
		{"fill notional", nil, func(r *StatefulRiskOverlay) error { return r.OnFill("BTC", 1, 1e308, 2, "USD", 1, 20) }},
		{"zero price no mark", nil, func(r *StatefulRiskOverlay) error { return r.OnFill("BTC", 1, 1, 0, "USD", 1, 20) }},
		{"zero price with mark", func(r *StatefulRiskOverlay) { must(t, r.OnIndicator(indicator(100, 1, 20))) }, func(r *StatefulRiskOverlay) error { return r.OnFill("BTC", 1, 1, 0, "USD", 2, 20) }},
		{"mark overflow", func(r *StatefulRiskOverlay) { must(t, r.OnFill("BTC", 1, 1e200, 1, "USD", 1, 20)) }, func(r *StatefulRiskOverlay) error { return r.OnIndicator(indicator(1e200, 2, 20)) }},
		{"fee overflow", func(r *StatefulRiskOverlay) { must(t, r.OnFill("BTC", 1, 1e308, 1, "USD", 1, 20)) }, func(r *StatefulRiskOverlay) error { return r.ApplyFee(1e308, "USD", 2) }},
		{"snapshot overflow", func(r *StatefulRiskOverlay) {
			must(t, r.OnIndicator(indicator(100, 1, 20)))
			r.state.Cash = 1e307
			r.state.Equity = 1e307
			r.state.HighWaterMark = 1e307
		}, func(r *StatefulRiskOverlay) error { _, _, err := r.Apply(target); return err }},
		{"missing indicator", nil, func(r *StatefulRiskOverlay) error { _, _, err := r.Apply(target); return err }},
		{"expired indicator", func(r *StatefulRiskOverlay) { must(t, r.OnIndicator(indicator(100, 1, 9))) }, func(r *StatefulRiskOverlay) error { _, _, err := r.Apply(target); return err }},
		{"future indicator", func(r *StatefulRiskOverlay) { must(t, r.OnIndicator(indicator(100, 11, 20))) }, func(r *StatefulRiskOverlay) error { _, _, err := r.Apply(target); return err }},
		{"expired mark", func(r *StatefulRiskOverlay) {
			must(t, r.OnIndicator(indicator(100, 1, 20)))
			must(t, r.OnFill("ETH", 1, 1, 100, "USD", 2, 9))
		}, func(r *StatefulRiskOverlay) error { _, _, err := r.Apply(target); return err }},
		{"unknown fill currency", nil, func(r *StatefulRiskOverlay) error { return r.OnFill("BTC", 1, 1, 100, "BTC", 1, 20) }},
		{"unknown fee currency", nil, func(r *StatefulRiskOverlay) error { return r.ApplyFee(1, "BTC", 1) }},
		{"wrong portfolio", nil, func(r *StatefulRiskOverlay) error {
			x := target
			x.Portfolio = "other"
			_, _, err := r.Apply(x)
			return err
		}},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			r := newOverlay()
			if tc.setup != nil {
				tc.setup(r)
			}
			before := r.clone()
			if err := tc.reject(r); err == nil {
				t.Fatal("accepted invalid event")
			}
			if !reflect.DeepEqual(r, before) {
				t.Fatalf("rejected event mutated overlay: got %+v want %+v", r, before)
			}
		})
	}
}

func TestStatefulRiskDelayedIndicatorAndProcessingTime(t *testing.T) {
	r, _ := NewStatefulRiskOverlay(DefaultStatefulRiskConfig())
	must(t, r.OnFill("BTC", 1, 1, 120, "USD", 11, 30))
	must(t, r.OnIndicator(StatefulRiskIndicator{Symbol: "BTC", Timeframe: "4h", Price: 100, RealizedVol: 1, CutoffTimeNS: 10, AvailableTimeNS: 12, ValidUntilNS: 30}))
	if r.State().Prices["BTC"] != 120 {
		t.Fatal("delayed indicator replaced newer fill mark")
	}
	target := StatefulRiskTarget{Portfolio: "arc", Symbol: "BTC", Timeframe: "4h", CutoffTimeNS: 10, ProcessTimeNS: 12, Quantity: 1, Leverage: 1}
	d, _, err := r.Apply(target)
	must(t, err)
	if d.LeverageCap != 2.4 {
		t.Fatalf("delayed indicator not applied: %+v", d)
	}

	for _, tc := range []struct {
		name            string
		cutoff, process int64
	}{{"duplicate", 10, 13}, {"processing before cutoff", 14, 13}, {"expired on processing", 11, 31}} {
		t.Run(tc.name, func(t *testing.T) {
			before := r.clone()
			x := target
			x.CutoffTimeNS = tc.cutoff
			x.ProcessTimeNS = tc.process
			_, _, err := r.Apply(x)
			if err == nil || !reflect.DeepEqual(r, before) {
				t.Fatalf("invalid target accepted or mutated state: %v", err)
			}
		})
	}
	r2, _ := NewStatefulRiskOverlay(DefaultStatefulRiskConfig())
	must(t, r2.OnIndicator(StatefulRiskIndicator{Symbol: "BTC", Timeframe: "4h", Price: 100, CutoffTimeNS: 11, AvailableTimeNS: 12, ValidUntilNS: 30}))
	before := r2.clone()
	if _, _, err := r2.Apply(target); err == nil || !reflect.DeepEqual(r2, before) {
		t.Fatal("future market cutoff accepted or mutated state")
	}
}

func TestStatefulRiskRetainsEligibleIndicator(t *testing.T) {
	r, _ := NewStatefulRiskOverlay(DefaultStatefulRiskConfig())
	must(t, r.OnIndicator(StatefulRiskIndicator{Symbol: "BTC", Timeframe: "4h", Price: 100, RealizedVol: 1, CutoffTimeNS: 10, AvailableTimeNS: 11, ValidUntilNS: 30}))
	must(t, r.OnIndicator(StatefulRiskIndicator{Symbol: "BTC", Timeframe: "4h", Price: 100, CutoffTimeNS: 20, AvailableTimeNS: 20, ValidUntilNS: 40}))
	target := StatefulRiskTarget{Portfolio: "arc", Symbol: "BTC", Timeframe: "4h", CutoffTimeNS: 10, ProcessTimeNS: 20, Quantity: 1, Leverage: 1}
	d, _, err := r.Apply(target)
	must(t, err)
	if d.LeverageCap != 2.4 {
		t.Fatalf("incorrect historical indicator: %+v", d)
	}
	target.CutoffTimeNS = 20
	d, _, err = r.Apply(target)
	must(t, err)
	if d.LeverageCap != 6 {
		t.Fatalf("incorrect latest indicator: %+v", d)
	}
}

func TestStatefulRiskExpiredMarksDoNotRaiseHighWater(t *testing.T) {
	for _, event := range []string{"indicator", "fill", "fee"} {
		t.Run(event, func(t *testing.T) {
			r, _ := NewStatefulRiskOverlay(DefaultStatefulRiskConfig())
			must(t, r.OnFill("BTC", 1, 100, 100, "USD", 1, 5))
			must(t, r.OnIndicator(StatefulRiskIndicator{Symbol: "BTC", Timeframe: "4h", Price: 200, CutoffTimeNS: 2, AvailableTimeNS: 2, ValidUntilNS: 5}))
			must(t, r.OnFill("ETH", 1, 100, 100, "USD", 3, 20))
			peak := r.State().HighWaterMark
			switch event {
			case "indicator":
				must(t, r.OnIndicator(StatefulRiskIndicator{Symbol: "ETH", Timeframe: "4h", Price: 200, CutoffTimeNS: 6, AvailableTimeNS: 6, ValidUntilNS: 20}))
			case "fill":
				must(t, r.OnFill("ETH", -1, 10, 200, "USD", 6, 20))
			case "fee":
				must(t, r.ApplyFee(1, "USD", 6))
			}
			if s := r.State(); s.EquityValid || s.HighWaterMark != peak {
				t.Fatalf("stale equity or peak: %+v", s)
			}
			before := r.clone()
			_, _, err := r.Apply(StatefulRiskTarget{Portfolio: "arc", Symbol: "BTC", Timeframe: "4h", CutoffTimeNS: 6, ProcessTimeNS: 6, Quantity: 1, Leverage: 1})
			if err == nil || !reflect.DeepEqual(r, before) {
				t.Fatal("stale portfolio accepted or changed")
			}
			must(t, r.OnIndicator(StatefulRiskIndicator{Symbol: "BTC", Timeframe: "4h", Price: 100, CutoffTimeNS: 7, AvailableTimeNS: 7, ValidUntilNS: 20}))
			if s := r.State(); !s.EquityValid || s.HighWaterMark != peak {
				t.Fatalf("restored valuation or peak: %+v", s)
			}
		})
	}
}

func TestStatefulRiskTargetStreamOrdering(t *testing.T) {
	r, _ := NewStatefulRiskOverlay(DefaultStatefulRiskConfig())
	for _, stream := range [][2]string{{"BTC", "4h"}, {"BTC", "1h"}, {"ETH", "4h"}} {
		must(t, r.OnIndicator(StatefulRiskIndicator{Symbol: stream[0], Timeframe: stream[1], Price: 100, CutoffTimeNS: 1, AvailableTimeNS: 1, ValidUntilNS: 100}))
	}
	for _, stream := range [][2]string{{"BTC", "4h"}, {"BTC", "1h"}, {"ETH", "4h"}} {
		_, _, err := r.Apply(StatefulRiskTarget{Portfolio: "arc", Symbol: stream[0], Timeframe: stream[1], CutoffTimeNS: 10, ProcessTimeNS: 10, Quantity: 1, Leverage: 1})
		must(t, err)
	}
	before := r.clone()
	for _, ts := range []int64{10, 9} {
		_, _, err := r.Apply(StatefulRiskTarget{Portfolio: "arc", Symbol: "BTC", Timeframe: "4h", CutoffTimeNS: ts, ProcessTimeNS: 11, Quantity: 1, Leverage: 1})
		if err == nil || !reflect.DeepEqual(r, before) {
			t.Fatalf("cutoff %d accepted or mutated state: %v", ts, err)
		}
	}
	_, _, err := r.Apply(StatefulRiskTarget{Portfolio: "arc", Symbol: "BTC", Timeframe: "4h", CutoffTimeNS: 11, ProcessTimeNS: 11, Quantity: 1, Leverage: 1})
	must(t, err)
}
