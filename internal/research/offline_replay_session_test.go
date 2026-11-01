package research

import (
	"fmt"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
)

func TestOfflineReplaySessionRejectsIdentityReuse(t *testing.T) {
	for _, mode := range []string{"empty", "same bar", "later bar"} {
		t.Run(mode, func(t *testing.T) {
			calls := 0
			result, err := RunOfflineReplay(replayTestConfig(), func(OfflineReplaySnapshot) ([]OfflineExecutionOrder, error) {
				calls++
				order := sessionOrder("reused")
				if mode == "empty" {
					order.ClientOrderID = ""
				}
				if mode == "same bar" {
					order.Quantity = .4
					return []OfflineExecutionOrder{order, order}, nil
				}
				return []OfflineExecutionOrder{order}, nil
			})
			if err == nil || !strings.Contains(err.Error(), "identity must be unique") || result != nil {
				t.Fatalf("result=%+v err=%v", result, err)
			}
			if (mode == "empty" || mode == "same bar") && calls != 1 {
				t.Fatal("continued policy after failed admission")
			}
		})
	}
}

func TestOfflineReplayPersistentSessionIsFreshAndNonResumable(t *testing.T) {
	cfg := replayTestConfig()
	cfg.SessionPath = filepath.Join(t.TempDir(), "first.lock")
	if _, err := RunOfflineReplay(cfg, func(OfflineReplaySnapshot) ([]OfflineExecutionOrder, error) { return nil, nil }); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(cfg.SessionPath); !os.IsNotExist(err) {
		t.Fatalf("marker created without submission: %v", err)
	}
	calls := 0
	policy := func(s OfflineReplaySnapshot) ([]OfflineExecutionOrder, error) {
		calls++
		if s.Event.OpenTimeNS == 0 && s.PositionQty != 0 {
			t.Fatal("new account restored inventory")
		}
		return []OfflineExecutionOrder{sessionOrder(fmt.Sprint(s.Event.CloseTimeNS))}, nil
	}
	first, err := RunOfflineReplay(cfg, policy)
	if err != nil {
		t.Fatal(err)
	}
	calls = 0
	if result, err := RunOfflineReplay(cfg, policy); err == nil || result != nil || calls != 0 {
		t.Fatalf("reopened used account: %+v %v calls=%d", result, err, calls)
	}
	cfg.SessionPath = filepath.Join(filepath.Dir(cfg.SessionPath), "explicit-new.lock")
	fresh, err := RunOfflineReplay(cfg, policy)
	if err != nil || !reflect.DeepEqual(first, fresh) {
		t.Fatalf("fresh account differs: %+v %v", fresh, err)
	}
}

func TestOfflineReplaySessionErrorsRefuseRestart(t *testing.T) {
	for _, phase := range []string{"simulator", "accounting"} {
		t.Run(phase, func(t *testing.T) {
			cfg := replayTestConfig()
			cfg.MaxTradeQtyPerCutoff = 1e308
			cfg.SessionPath = filepath.Join(t.TempDir(), "failed.lock")
			calls := 0
			policy := func(OfflineReplaySnapshot) ([]OfflineExecutionOrder, error) {
				calls++
				a, b := sessionOrder("first"), sessionOrder("second")
				if phase == "simulator" {
					a.Quantity = 1e308
				} else {
					a.Quantity = 1e306
					b.Quantity = 1e306
				}
				// Each notional is finite, but their sum overflows accounting.
				return []OfflineExecutionOrder{a, b, sessionOrder("never")}, nil
			}
			result, err := RunOfflineReplay(cfg, policy)
			if err == nil || result != nil || calls != 1 {
				t.Fatalf("continued failed run: %+v %v calls=%d", result, err, calls)
			}
			want := "simulate order"
			if phase == "accounting" {
				want = "publish report"
			}
			if !strings.Contains(err.Error(), want) {
				t.Fatalf("wrong failure boundary: %v", err)
			}
			if _, err := RunOfflineReplay(cfg, policy); err == nil || !strings.Contains(err.Error(), "cannot resume") || calls != 1 {
				t.Fatalf("restarted failed run: %v", err)
			}
		})
	}
}
