package research

import (
	"errors"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
)

func TestOfflineReplayReportFeedbackFollowsAccounting(t *testing.T) {
	cfg := replayTestConfig()
	cfg.MaxTradeQtyPerCutoff = 2
	var events []string
	var positions []float64
	cfg.OnReport = func(report ExecutionReport, position float64) error {
		events = append(events, report.ClientOrderID+":"+report.Status)
		positions = append(positions, position)
		return nil
	}
	calls := 0
	result, err := RunOfflineReplay(cfg, func(snapshot OfflineReplaySnapshot) ([]OfflineExecutionOrder, error) {
		calls++
		if calls == 1 {
			return []OfflineExecutionOrder{sessionOrder("filled"), {ClientOrderID: "skipped", Symbol: "BTC", Side: 1}}, nil
		}
		if snapshot.PositionQty != positions[0] || len(events) != 1 {
			t.Fatalf("policy did not observe prior feedback: %+v %v", snapshot, events)
		}
		return nil, nil
	})
	if err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(events, []string{"filled:FILLED"}) || len(result.Reports) != 1 || positions[0] != 1 || result.PositionQty != 1 {
		t.Fatalf("reports=%v positions=%v result=%+v", events, positions, result)
	}
}

func TestOfflineReplayFeedbackOnlyChangesFuturePolicyCalls(t *testing.T) {
	cfg := replayTestConfig()
	cfg.MaxTradeQtyPerCutoff = 2
	feedback := false
	cfg.OnReport = func(_ ExecutionReport, _ float64) error {
		feedback = true
		return nil
	}
	calls := 0
	result, err := RunOfflineReplay(cfg, func(_ OfflineReplaySnapshot) ([]OfflineExecutionOrder, error) {
		calls++
		if calls == 1 {
			return []OfflineExecutionOrder{sessionOrder("first"), sessionOrder("second")}, nil
		}
		if !feedback {
			t.Fatal("future policy call did not observe feedback")
		}
		return nil, nil
	})
	if err != nil {
		t.Fatal(err)
	}
	if len(result.Reports) != 2 || result.Reports[1].ClientOrderID != "second" || result.PositionQty != 2 {
		t.Fatalf("current batch changed: %+v", result)
	}
}

func TestOfflineReplayFundingNormalizedFlipFeedback(t *testing.T) {
	cfg := replayTestConfig()
	cfg.MaxTradeQtyPerCutoff = 1
	cfg.Candles["5m"][1].CloseTimeNS -= 1_000_000
	cfg.Candles["5m"] = append(cfg.Candles["5m"], replayTestBar("5m", 10*replayMinute, 5*replayMinute, 120))
	cfg.Funding = []ReplayFundingRow{{6 * replayMinute, .01}, {7 * replayMinute, .02}, {10 * replayMinute, .03}, {11 * replayMinute, .01}}
	var trace []string
	var callbackPositions []float64
	cfg.OnReport = func(report ExecutionReport, position float64) error {
		trace = append(trace, report.ClientOrderID)
		callbackPositions = append(callbackPositions, position)
		return nil
	}
	calls := 0
	result, err := RunOfflineReplayIntents(cfg, func(s OfflineReplaySnapshot) ([]OfflineReplayIntent, error) {
		calls++
		switch calls {
		case 1:
			trace = append(trace, "policy-entry")
			return []OfflineReplayIntent{{Order: &OfflineExecutionOrder{ClientOrderID: "entry", Symbol: "BTC", Side: 1, Quantity: .4}}}, nil
		case 2:
			trace = append(trace, "policy-flip")
			if s.Event.CloseTimeNS != 10*replayMinute || s.PositionQty != .4 {
				t.Fatalf("normalized flip snapshot: %+v", s)
			}
			return []OfflineReplayIntent{{Flip: &OfflineReplayFlip{Symbol: "BTC", TargetQty: -1, CloseClientOrderID: "close", OpenClientOrderID: "open"}}}, nil
		case 3:
			trace = append(trace, "policy-after")
			if s.PositionQty != -.6 || len(callbackPositions) != 3 || callbackPositions[2] != s.PositionQty {
				t.Fatalf("post-flip snapshot=%+v callbacks=%v", s, callbackPositions)
			}
		}
		return nil, nil
	})
	if err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(trace, []string{"policy-entry", "entry", "policy-flip", "close", "open", "policy-after"}) || !reflect.DeepEqual(callbackPositions, []float64{.4, 0, -.6}) {
		t.Fatalf("trace=%v positions=%v", trace, callbackPositions)
	}
	if calls != 3 || len(result.Reports) != 3 || len(result.Episodes) != 2 || !result.Episodes[0].Closed || result.Episodes[1].Closed {
		t.Fatalf("result=%+v calls=%d", result, calls)
	}
	for i, want := range []struct {
		qty, price, fee float64
		timestamp       int64
	}{
		{.4, 100, .04, 5 * replayMinute}, {.4, 110, .044, 10 * replayMinute}, {.6, 110, .066, 10 * replayMinute},
	} {
		r := result.Reports[i]
		replayNear(t, r.FilledQty, want.qty)
		replayNear(t, r.AvgPrice, want.price)
		replayNear(t, r.Fee, want.fee)
		if r.TransactionTimeNS != want.timestamp {
			t.Fatalf("report timestamp: %+v", r)
		}
	}
	// The two intermediate settlements use 100; the boundary settlement uses 110.
	// All three precede the flip, while the next settlement belongs to the short.
	replayNear(t, result.Episodes[0].FundingPnL, -(.4*100*.01 + .4*100*.02 + .4*110*.03))
	replayNear(t, result.Episodes[1].FundingPnL, .6*110*.01)
	replayNear(t, result.Episodes[0].Fees, .084)
	replayNear(t, result.Episodes[1].Fees, .066)
	replayNear(t, result.PositionQty, -.6)
}

func TestOfflineReplayFlipFeedbackIntegration(t *testing.T) {
	for _, tc := range []struct {
		name                string
		cap, closeFill      float64
		closeStatus, failAt string
		wantIDs             []string
		wantPositions       []float64
		wantPosition        float64
	}{
		{"both legs", 1, .4, "FILLED", "", []string{"entry", "close", "open"}, []float64{.4, 0, -.6}, -.6},
		{"budget exhausted", .4, .4, "FILLED", "", []string{"entry", "close"}, []float64{.4, 0}, 0},
		{"partial close", 1, .2, "PARTIALLY_FILLED", "", []string{"entry", "close"}, []float64{.4, .2}, .2},
		{"rejected close", 1, 0, "REJECTED", "", []string{"entry", "close"}, []float64{.4, .4}, .4},
		{"close feedback fails", 1, .4, "FILLED", "close", []string{"entry", "close"}, []float64{.4, 0}, 0},
		{"open feedback fails", 1, .4, "FILLED", "open", []string{"entry", "close", "open"}, []float64{.4, 0, -.6}, 0},
	} {
		t.Run(tc.name, func(t *testing.T) {
			cfg := replayTestConfig()
			cfg.MaxTradeQtyPerCutoff = tc.cap
			cfg.SessionPath = filepath.Join(t.TempDir(), "session.lock")
			failure := errors.New("feedback failed")
			var ids []string
			var positions []float64
			var submitted []OfflineExecutionOrder
			cfg.OnReport = func(report ExecutionReport, position float64) error {
				ids = append(ids, report.ClientOrderID)
				positions = append(positions, position)
				if report.ClientOrderID == tc.failAt {
					return failure
				}
				return nil
			}
			simulate := func(order OfflineExecutionOrder, mark, rate float64, ts int64) (ExecutionReport, error) {
				submitted = append(submitted, order)
				if order.ClientOrderID == "close" {
					return ExecutionReport{ClientOrderID: order.ClientOrderID, Symbol: order.Symbol, Side: order.Side, Status: tc.closeStatus, FilledQty: tc.closeFill, AvgPrice: mark, Fee: tc.closeFill * mark * rate, TransactionTimeNS: ts}, nil
				}
				order.ReduceOnly = false
				return SimulateOfflineExecution(order, mark, rate, ts)
			}
			calls := 0
			policy := func(s OfflineReplaySnapshot) ([]OfflineReplayIntent, error) {
				calls++
				if calls == 1 {
					return []OfflineReplayIntent{{Order: &OfflineExecutionOrder{ClientOrderID: "entry", Symbol: "BTC", Side: 1, Quantity: .4}}}, nil
				}
				if s.PositionQty != .4 {
					t.Fatalf("snapshot position = %v", s.PositionQty)
				}
				return []OfflineReplayIntent{{Flip: &OfflineReplayFlip{Symbol: "BTC", TargetQty: -1, CloseClientOrderID: "close", OpenClientOrderID: "open"}}}, nil
			}
			result, err := runOfflineReplayIntents(cfg, policy, simulate)
			if !reflect.DeepEqual(ids, tc.wantIDs) || !reflect.DeepEqual(positions, tc.wantPositions) || len(submitted) != len(ids) {
				t.Fatalf("ids=%v positions=%v submitted=%+v", ids, positions, submitted)
			}
			if tc.failAt != "" {
				if result != nil || !errors.Is(err, failure) {
					t.Fatalf("result=%+v err=%v", result, err)
				}
				if _, err := runOfflineReplayIntents(cfg, policy, simulate); err == nil || !strings.Contains(err.Error(), "cannot resume") || calls != 2 {
					t.Fatalf("resume err=%v calls=%d", err, calls)
				}
				return
			}
			if err != nil {
				t.Fatal(err)
			}
			if result.PositionQty != tc.wantPosition || len(result.Reports) != len(ids) {
				t.Fatalf("result=%+v", result)
			}
			if len(submitted) == 3 && (submitted[2].Quantity != .6 || result.Reports[2].FilledQty != .6) {
				t.Fatalf("budget: order=%+v report=%+v", submitted[2], result.Reports[2])
			}
		})
	}
}

func TestOfflineReplayFeedbackDustPositionMatchesSnapshot(t *testing.T) {
	cfg := replayTestConfig()
	cfg.MaxTradeQtyPerCutoff = 2
	var feedback float64
	cfg.OnReport = func(_ ExecutionReport, position float64) error { feedback = position; return nil }
	simulate := func(order OfflineExecutionOrder, mark, rate float64, ts int64) (ExecutionReport, error) {
		order.ReduceOnly = false
		report, err := SimulateOfflineExecution(order, mark, rate, ts)
		if order.ClientOrderID == "close" {
			report.Status = "PARTIALLY_FILLED"
			report.FilledQty = 1 - 5e-13
			report.Fee = report.FilledQty * mark * rate
		}
		return report, err
	}
	calls := 0
	_, err := runOfflineReplayIntents(cfg, func(s OfflineReplaySnapshot) ([]OfflineReplayIntent, error) {
		calls++
		if calls == 1 {
			return []OfflineReplayIntent{{Order: &OfflineExecutionOrder{ClientOrderID: "entry", Symbol: "BTC", Side: 1, Quantity: 1}}, {Order: &OfflineExecutionOrder{ClientOrderID: "close", Symbol: "BTC", Side: -1, Quantity: 1, ReduceOnly: true}}}, nil
		}
		if s.PositionQty != feedback {
			t.Fatalf("snapshot=%g feedback=%g", s.PositionQty, feedback)
		}
		return nil, nil
	}, simulate)
	if err != nil {
		t.Fatal(err)
	}
}

func TestOfflineReplayReportFeedbackErrorStopsSession(t *testing.T) {
	cfg := replayTestConfig()
	cfg.SessionPath = filepath.Join(t.TempDir(), "session.lock")
	failure := errors.New("feedback failed")
	callbacks, policies := 0, 0
	cfg.OnReport = func(report ExecutionReport, position float64) error {
		callbacks++
		if report.Status != "FILLED" || position != 1 {
			t.Fatalf("report=%+v position=%v", report, position)
		}
		return failure
	}
	policy := func(OfflineReplaySnapshot) ([]OfflineExecutionOrder, error) {
		policies++
		return []OfflineExecutionOrder{sessionOrder("first"), sessionOrder("never")}, nil
	}
	result, err := RunOfflineReplay(cfg, policy)
	if result != nil || !errors.Is(err, failure) || callbacks != 1 || policies != 1 {
		t.Fatalf("result=%+v err=%v callbacks=%d policies=%d", result, err, callbacks, policies)
	}
	if _, err := RunOfflineReplay(cfg, policy); err == nil || !strings.Contains(err.Error(), "cannot resume") || policies != 1 {
		t.Fatalf("failed session resumed: %v policies=%d", err, policies)
	}
}
