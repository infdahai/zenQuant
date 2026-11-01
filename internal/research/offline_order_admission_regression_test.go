package research

import (
	"fmt"
	"math"
	"reflect"
	"testing"
)

func TestOfflineReplayDefaultCapAndSkippedNonpositiveOrders(t *testing.T) {
	cfg := replayTestConfig()
	calls := 0
	result, err := RunOfflineReplay(cfg, func(s OfflineReplaySnapshot) ([]OfflineExecutionOrder, error) {
		calls++
		if calls > 1 {
			return nil, nil
		}
		return []OfflineExecutionOrder{
			{ClientOrderID: "reused", Symbol: "BTC", Side: 1, Quantity: 0},
			{ClientOrderID: "reused", Symbol: "BTC", Side: 1, Quantity: -1},
			{ClientOrderID: "reused", Symbol: "BTC", Side: 1, Quantity: 2},
			{ClientOrderID: "exhausted", Symbol: "BTC", Side: 1, Quantity: 1},
		}, nil
	})
	if err != nil {
		t.Fatal(err)
	}
	if len(result.Reports) != 1 || result.Reports[0].ClientOrderID != "reused" || result.Reports[0].FilledQty != 1 || result.PositionQty != 1 {
		t.Fatalf("unexpected result: %+v", result)
	}
	for _, capQty := range []float64{-1, math.NaN(), math.Inf(1)} {
		bad := cfg
		bad.MaxTradeQtyPerCutoff = capQty
		if _, err := RunOfflineReplay(bad, func(OfflineReplaySnapshot) ([]OfflineExecutionOrder, error) { return nil, nil }); err == nil {
			t.Fatalf("accepted invalid cap %v", capQty)
		}
	}
}

func TestFilledQuantityPythonCancellation(t *testing.T) {
	// Values are checked against zenquant.domain.quantity.add_filled_quantity.
	cases := []struct{ a, b, want float64 }{
		{.00016500000000000002, -.000165, 0},
		{1, -math.Nextafter(1, 0), 0},
		{.00005, -.0000499999999999, 1e-16},
		{1, -.999999999999999, 1e-15},
		{.1, .2, .3},
		{-1, math.Nextafter(1, 0), 0},
	}
	for _, tc := range cases {
		if got := decimalAdd(tc.a, tc.b); got != tc.want {
			t.Errorf("decimalAdd(%g,%g)=%g want %g", tc.a, tc.b, got, tc.want)
		}
	}
	a := offlineOrderAdmission{maxQty: 1, positions: map[string]float64{"BTC": .00016500000000000002}, filled: map[admissionKey]float64{}}
	a.record(ExecutionReport{Symbol: "BTC", Side: -1, FilledQty: .000165}, 1)
	if a.positions["BTC"] != 0 {
		t.Fatalf("cancellation left inventory %g", a.positions["BTC"])
	}
}

func TestOfflineReplayDecimalPositionClosesEpisode(t *testing.T) {
	for _, side := range []int{1, -1} {
		t.Run(fmt.Sprint(side), func(t *testing.T) {
			cfg := replayTestConfig()
			cfg.MaxTradeQtyPerCutoff = 20000
			cfg.Candles["5m"] = append(cfg.Candles["5m"], replayTestBar("5m", 10*replayMinute, 5*replayMinute, 120))
			var seen []float64
			result, err := RunOfflineReplay(cfg, func(s OfflineReplaySnapshot) ([]OfflineExecutionOrder, error) {
				seen = append(seen, s.PositionQty)
				switch len(seen) {
				case 1:
					return []OfflineExecutionOrder{
						{ClientOrderID: "open", Symbol: "BTC", Side: side, Quantity: 10000.1},
						{ClientOrderID: "add", Symbol: "BTC", Side: side, Quantity: .2},
					}, nil
				case 2:
					return []OfflineExecutionOrder{{ClientOrderID: "close", Symbol: "BTC", Side: -side, Quantity: 10000.3, ReduceOnly: true}}, nil
				}
				return nil, nil
			})
			if err != nil {
				t.Fatal(err)
			}
			if len(result.Reports) != 3 || result.Reports[2].FilledQty != 10000.3 {
				t.Fatalf("reports: %+v", result.Reports)
			}
			if !reflect.DeepEqual(seen, []float64{0, float64(side) * 10000.3, 0}) || result.PositionQty != 0 || len(result.Episodes) != 1 || !result.Episodes[0].Closed || result.Episodes[0].RemainingQty != 0 {
				t.Fatalf("snapshots=%v result=%+v", seen, result)
			}
		})
	}
}

func TestOfflineReplayPartialAndRejectedReports(t *testing.T) {
	cfg := replayTestConfig()
	cfg.Candles["5m"] = append(cfg.Candles["5m"], replayTestBar("5m", 10*replayMinute, 5*replayMinute, 120))
	run := func() (*OfflineReplayResult, []float64, error) {
		var seen []float64
		policy := func(s OfflineReplaySnapshot) ([]OfflineExecutionOrder, error) {
			seen = append(seen, s.PositionQty)
			if len(seen) == 1 {
				return []OfflineExecutionOrder{
					{ClientOrderID: "partial-open", Symbol: "BTC", Side: 1, Quantity: .6},
					{ClientOrderID: "rejected", Symbol: "BTC", Side: 1, Quantity: .7},
					{ClientOrderID: "remaining-budget", Symbol: "BTC", Side: 1, Quantity: .9},
					{ClientOrderID: "blocked", Symbol: "BTC", Side: -1, Quantity: .2, ReduceOnly: true},
				}, nil
			}
			if len(seen) == 3 {
				return []OfflineExecutionOrder{{ClientOrderID: "no-reversal", Symbol: "BTC", Side: -1, Quantity: 1, ReduceOnly: true}}, nil
			}
			return []OfflineExecutionOrder{
				{ClientOrderID: "partial-close", Symbol: "BTC", Side: -1, Quantity: 2, ReduceOnly: true},
				{ClientOrderID: "close", Symbol: "BTC", Side: -1, Quantity: 2, ReduceOnly: true},
			}, nil
		}
		simulate := func(o OfflineExecutionOrder, mark, rate float64, ts int64) (ExecutionReport, error) {
			want := map[string]float64{"partial-open": .6, "rejected": .7, "remaining-budget": .7, "partial-close": 1, "close": .6}
			if qty, ok := want[o.ClientOrderID]; !ok || o.Quantity != qty {
				t.Fatalf("unexpected admitted order: %+v", o)
			}
			r := ExecutionReport{ClientOrderID: o.ClientOrderID, Symbol: o.Symbol, Side: o.Side, TransactionTimeNS: ts}
			if o.ClientOrderID == "rejected" {
				r.Status = "REJECTED"
				r.RejectReason = "synthetic"
				return r, nil
			}
			r.Status = "FILLED"
			r.FilledQty = o.Quantity
			switch o.ClientOrderID {
			case "partial-open":
				r.Status = "PARTIALLY_FILLED"
				r.FilledQty = .3
			case "partial-close":
				r.Status = "PARTIALLY_FILLED"
				r.FilledQty = .4
			}
			r.AvgPrice = mark
			r.Fee = r.FilledQty * mark * rate
			return r, nil
		}
		result, err := runOfflineReplay(cfg, policy, simulate)
		return result, seen, err
	}
	first, seen, err := run()
	if err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(seen, []float64{0, 1, 0}) {
		t.Fatalf("snapshots: %v", seen)
	}
	if len(first.Reports) != 5 || first.PositionQty != 0 || len(first.Episodes) != 1 || !first.Episodes[0].Closed {
		t.Fatalf("result: %+v", first)
	}
	wantIDs := []string{"partial-open", "rejected", "remaining-budget", "partial-close", "close"}
	wantQty := []float64{.3, 0, .7, .4, .6}
	var fees float64
	for i, r := range first.Reports {
		if r.ClientOrderID != wantIDs[i] {
			t.Fatalf("report %d: %+v", i, r)
		}
		replayNear(t, r.FilledQty, wantQty[i])
		fees += r.Fee
	}
	replayNear(t, fees, first.Episodes[0].Fees)
	again, seenAgain, err := run()
	if err != nil || !reflect.DeepEqual(first, again) || !reflect.DeepEqual(seen, seenAgain) {
		t.Fatalf("nondeterministic: %+v %v %v", again, seenAgain, err)
	}
}
