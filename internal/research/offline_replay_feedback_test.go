package research

import (
	"errors"
	"math"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
)

func TestOfflineReplayReportFeedbackFollowsAccounting(t *testing.T) {
	cfg := replayTestConfig()
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
			a, b := sessionOrder("filled"), sessionOrder("rejected")
			b.Quantity = 0
			return []OfflineExecutionOrder{a, b}, nil
		}
		if snapshot.PositionQty != positions[0] || len(events) != 2 {
			t.Fatalf("policy did not observe prior feedback: %+v %v", snapshot, events)
		}
		return nil, nil
	})
	if err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(events, []string{"filled:FILLED", "rejected:REJECTED"}) || len(result.Reports) != 2 || positions[0] != 1 || positions[1] != 1 || result.PositionQty != 1 {
		t.Fatalf("reports=%v positions=%v result=%+v", events, positions, result)
	}
}

func TestOfflineReplayReportFeedbackLedgerPositions(t *testing.T) {
	for _, tc := range []struct {
		name string
		sell float64
		want float64
	}{
		{"ordinary reduction", 0.5, 1},
		{"dust remainder", 1.5 - 5e-13, 0},
		{"above dust threshold", 1.5 - 2e-12, 1.5 - (1.5 - 2e-12)},
		{"dust reversal", 1.5 + 5e-13, 0},
		{"above dust reversal", 1.5 + 2e-12, -(1.5 + 2e-12 - 1.5)},
	} {
		t.Run(tc.name, func(t *testing.T) {
			want := tc.want
			if tc.name == "above dust threshold" || tc.name == "above dust reversal" {
				want = 1.5 - tc.sell
			}
			cfg := replayTestConfig()
			var positions []float64
			cfg.OnReport = func(_ ExecutionReport, position float64) error {
				positions = append(positions, position)
				return nil
			}
			calls := 0
			result, err := RunOfflineReplay(cfg, func(snapshot OfflineReplaySnapshot) ([]OfflineExecutionOrder, error) {
				calls++
				if calls == 1 {
					first, add, reduce := sessionOrder("first"), sessionOrder("add"), sessionOrder("reduce")
					add.Quantity = 0.5
					reduce.Side, reduce.Quantity = -1, tc.sell
					return []OfflineExecutionOrder{first, add, reduce}, nil
				}
				if snapshot.PositionQty != want {
					t.Fatalf("snapshot position = %.17g, want %.17g", snapshot.PositionQty, want)
				}
				return nil, nil
			})
			if err != nil {
				t.Fatal(err)
			}
			if len(positions) != 3 || positions[0] != 1 || positions[1] != 1.5 || positions[2] != want || result.PositionQty != want {
				t.Fatalf("positions=%v result=%g want=%g", positions, result.PositionQty, want)
			}
			if tc.name == "dust remainder" && math.Abs(1.5-tc.sell) == 0 {
				t.Fatal("test must use a nonzero raw fill remainder")
			}
		})
	}
}

func TestOfflineReplayFeedbackOnlyChangesFuturePolicyCalls(t *testing.T) {
	cfg := replayTestConfig()
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
