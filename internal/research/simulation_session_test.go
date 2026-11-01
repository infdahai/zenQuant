package research

import (
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
)

func sessionOrder(id string) OfflineExecutionOrder {
	return OfflineExecutionOrder{ClientOrderID: id, Symbol: "BTC", Side: 1, Quantity: 1, MaxSlippageBPS: 8, OrderType: "MARKET"}
}

func mustSession(t *testing.T, path string, consumers ...func(ExecutionReport) error) *simulationSession {
	t.Helper()
	s, err := newSimulationSession(path, consumers...)
	if err != nil {
		t.Fatal(err)
	}
	return s
}

func TestSimulationSessionPythonGolden(t *testing.T) {
	data, err := os.ReadFile("testdata/simulation_session_golden.json")
	if err != nil {
		t.Fatal(err)
	}
	var fixture struct {
		Cases []struct {
			Name  string
			Steps []struct {
				Order   OfflineExecutionOrder
				Report  ExecutionReport `json:"simulator_report"`
				Failed  bool
				Blocked bool
			}
			Submitted []OfflineExecutionOrder
			Published []ExecutionReport
		}
	}
	if err := json.Unmarshal(data, &fixture); err != nil {
		t.Fatal(err)
	}
	for _, tc := range fixture.Cases {
		t.Run(tc.Name, func(t *testing.T) {
			submitted := []OfflineExecutionOrder{}
			published := []ExecutionReport{}
			s := mustSession(t, "", func(r ExecutionReport) error { published = append(published, r); return nil })
			s.onSubmit = func(o OfflineExecutionOrder) error { submitted = append(submitted, o); return nil }
			for _, step := range tc.Steps {
				s.simulate = func(OfflineExecutionOrder, float64, float64, int64) (ExecutionReport, error) { return step.Report, nil }
				got, err := s.submit(step.Order, 100, .0004, step.Report.TransactionTimeNS)
				if (err != nil) != step.Failed || s.blocked != step.Blocked {
					t.Fatalf("got err=%v blocked=%v; want failed=%v blocked=%v", err, s.blocked, step.Failed, step.Blocked)
				}
				if err == nil && got != step.Report {
					t.Fatalf("report: %+v; want %+v", got, step.Report)
				}
			}
			if !reflect.DeepEqual(submitted, tc.Submitted) || !reflect.DeepEqual(published, tc.Published) {
				t.Fatalf("trace differs: submitted=%+v published=%+v", submitted, published)
			}
		})
	}
}

func TestSimulationSessionMalformedReportsBeforeAccounting(t *testing.T) {
	mutations := map[string]func(*ExecutionReport){
		"identity":        func(r *ExecutionReport) { r.ClientOrderID = "unknown" },
		"symbol":          func(r *ExecutionReport) { r.Symbol = "OTHER" },
		"side":            func(r *ExecutionReport) { r.Side = 0 },
		"opposing side":   func(r *ExecutionReport) { r.Side = -1 },
		"status":          func(r *ExecutionReport) { r.Status = "UNKNOWN" },
		"negative fill":   func(r *ExecutionReport) { r.FilledQty = -1 },
		"cumulative fill": func(r *ExecutionReport) { r.FilledQty = 2 },
		"negative price":  func(r *ExecutionReport) { r.AvgPrice = -1 },
		"zero fill price": func(r *ExecutionReport) { r.AvgPrice = 0 },
		"negative fee":    func(r *ExecutionReport) { r.Fee = -1 },
		"rejected fill":   func(r *ExecutionReport) { r.Status = "REJECTED" },
		"pending fee":     func(r *ExecutionReport) { r.Status = "PENDING"; r.FilledQty = 0 },
	}
	for _, value := range []float64{math.NaN(), math.Inf(1), math.Inf(-1)} {
		for _, field := range []string{"quantity", "price", "fee"} {
			mutations[field+" "+fmt.Sprint(value)] = func(r *ExecutionReport) {
				switch field {
				case "quantity":
					r.FilledQty = value
				case "price":
					r.AvgPrice = value
				case "fee":
					r.Fee = value
				}
			}
		}
	}
	for name, mutate := range mutations {
		t.Run(name, func(t *testing.T) {
			ledger := NewPositionEpisodeLedger()
			accounting, err := NewReplayAccounting("BTC", ledger, nil)
			if err != nil {
				t.Fatal(err)
			}
			published, calls := 0, 0
			s := mustSession(t, "", func(r ExecutionReport) error {
				published++
				_, err := accounting.CollectReport(r)
				return err
			})
			s.simulate = func(o OfflineExecutionOrder, m, f float64, ts int64) (ExecutionReport, error) {
				calls++
				r, err := SimulateOfflineExecution(o, m, f, ts)
				mutate(&r)
				return r, err
			}
			if _, err := s.submit(sessionOrder("first"), 100, .001, 1); err == nil {
				t.Fatal("accepted malformed result")
			}
			if _, err := s.submit(sessionOrder("second"), 100, .001, 2); err == nil {
				t.Fatal("session not blocked")
			}
			if published != 0 || calls != 1 || len(ledger.Episodes()) != 0 {
				t.Fatal("malformed report reached consumer or retried")
			}
		})
	}
}

func TestSimulationSessionCopiesSubmittedMaterialAndReports(t *testing.T) {
	price := 100.0
	order := sessionOrder("immutable")
	order.LimitPrice = &price
	var second ExecutionReport
	s := mustSession(t, "", func(r ExecutionReport) error { r.FilledQty = 999; return nil }, func(r ExecutionReport) error { second = r; return nil })
	s.onSubmit = func(o OfflineExecutionOrder) error {
		o.Side = -1
		*o.LimitPrice = -1
		price = 200 // Caller-owned pointer also cannot change the submitted snapshot.
		return nil
	}
	s.simulate = func(o OfflineExecutionOrder, m, f float64, ts int64) (ExecutionReport, error) {
		if o.Side != 1 || *o.LimitPrice != 100 {
			t.Fatalf("mutated submission: %+v", o)
		}
		*o.LimitPrice = 300
		o.LimitPrice = nil // Test seam; production still rejects limit orders.
		return SimulateOfflineExecution(o, m, f, ts)
	}
	r, err := s.submit(order, 100, .001, 1)
	if err != nil || r.FilledQty != 1 || second != r || price != 200 {
		t.Fatalf("copies: %+v %+v %v", r, second, err)
	}
}

func TestSimulationSessionPartialEndsAttempt(t *testing.T) {
	ledger := NewPositionEpisodeLedger()
	accounting, err := NewReplayAccounting("BTC", ledger, nil)
	if err != nil {
		t.Fatal(err)
	}
	var reports []ExecutionReport
	s := mustSession(t, "", func(r ExecutionReport) error { _, err := accounting.CollectReport(r); return err }, func(r ExecutionReport) error { reports = append(reports, r); return nil })
	calls := 0
	s.simulate = func(o OfflineExecutionOrder, m, f float64, ts int64) (ExecutionReport, error) {
		calls++
		r, err := SimulateOfflineExecution(o, m, f, ts)
		if calls == 1 {
			r.Status = "PARTIALLY_FILLED"
			r.FilledQty = .4
			r.Fee = .04
		}
		return r, err
	}
	if _, err := s.submit(sessionOrder("partial"), 100, .001, 1); err != nil {
		t.Fatal(err)
	}
	replayNear(t, ledger.PositionQty("BTC"), .4)
	remaining := sessionOrder("remaining")
	remaining.Quantity = .6
	if _, err := s.submit(remaining, 100, .001, 2); err != nil {
		t.Fatal(err)
	}
	replayNear(t, ledger.PositionQty("BTC"), 1)
	replayNear(t, ledger.Episodes()[0].Fees, .1)
	if _, err := s.submit(sessionOrder("partial"), 100, .001, 3); err == nil {
		t.Fatal("reapplied completed partial")
	}
	if calls != 2 || len(reports) != 2 {
		t.Fatal("duplicate reached simulator or consumers")
	}
}

func TestSimulationSessionPartialClosePreservesSignedResidual(t *testing.T) {
	for _, side := range []int{-1, 1} {
		t.Run(fmt.Sprint(side), func(t *testing.T) {
			ledger := NewPositionEpisodeLedger()
			accounting, err := NewReplayAccounting("BTC", ledger, nil)
			if err != nil {
				t.Fatal(err)
			}
			var reports []ExecutionReport
			s := mustSession(t, "", func(r ExecutionReport) error {
				_, err := accounting.CollectReport(r)
				return err
			}, func(r ExecutionReport) error {
				reports = append(reports, r)
				return nil
			})
			s.simulate = func(o OfflineExecutionOrder, mark, rate float64, ts int64) (ExecutionReport, error) {
				partial := o.ClientOrderID == "partial-close"
				if partial {
					o.Quantity = .4
				}
				r, err := SimulateOfflineExecution(o, mark, rate, ts)
				if partial {
					r.Status = "PARTIALLY_FILLED"
				}
				return r, err
			}
			open := sessionOrder("open")
			open.Side = side
			if _, err := s.submit(open, 100, .001, 1); err != nil {
				t.Fatal(err)
			}
			closeOrder := sessionOrder("partial-close")
			closeOrder.Side = -side
			partialPrice, finalPrice := 100+10*float64(side), 100+20*float64(side)
			if _, err := s.submit(closeOrder, partialPrice, .001, 2); err != nil {
				t.Fatal(err)
			}
			replayNear(t, ledger.PositionQty("BTC"), float64(side)*.6)
			episodes := ledger.Episodes()
			if len(episodes) != 1 || episodes[0].Closed || episodes[0].Side != side || len(ledger.ClosedEpisodes()) != 0 {
				t.Fatalf("partial close ended or reversed episode: %+v", episodes)
			}
			partialFees := .1 + .4*partialPrice*.001
			replayNear(t, episodes[0].RemainingQty, .6)
			replayNear(t, episodes[0].GrossRealizedPnL, 4)
			replayNear(t, episodes[0].Fees, partialFees)
			replayNear(t, episodes[0].NetPnL, 4-partialFees)
			closeOrder.ClientOrderID = "close-remainder"
			closeOrder.Quantity = .6
			if _, err := s.submit(closeOrder, finalPrice, .001, 3); err != nil {
				t.Fatal(err)
			}
			closed := ledger.ClosedEpisodes()
			if ledger.PositionQty("BTC") != 0 || len(closed) != 1 || len(ledger.Episodes()) != 1 || len(reports) != 3 {
				t.Fatalf("remaining close: position=%v episodes=%+v reports=%+v", ledger.PositionQty("BTC"), closed, reports)
			}
			if closed[0].EpisodeID != episodes[0].EpisodeID || closed[0].FillCount != 3 || closed[0].ReduceCount != 2 {
				t.Fatalf("close did not retain original episode: %+v", closed[0])
			}
			totalFees := partialFees + .6*finalPrice*.001
			replayNear(t, closed[0].GrossRealizedPnL, 16)
			replayNear(t, closed[0].Fees, totalFees)
			replayNear(t, closed[0].NetPnL, 16-totalFees)
		})
	}
}

func TestSimulationSessionReentrancyAndConcurrentSubmit(t *testing.T) {
	s := mustSession(t, "")
	s.onSubmit = func(OfflineExecutionOrder) error {
		if _, err := s.submit(sessionOrder("nested"), 100, 0, 1); err == nil {
			t.Fatal("accepted reentrant order")
		}
		done := make(chan error, 1)
		go func() { _, err := s.submit(sessionOrder("concurrent"), 100, 0, 1); done <- err }()
		if err := <-done; err == nil {
			t.Fatal("accepted concurrent order")
		}
		return nil
	}
	if _, err := s.submit(sessionOrder("first"), 100, 0, 1); err != nil {
		t.Fatal(err)
	}
	if _, err := s.submit(sessionOrder("next"), 100, 0, 2); err != nil {
		t.Fatal(err)
	}
	if len(s.orderIDs) != 2 {
		t.Fatal("busy rejection consumed an identity")
	}
	// If a subscriber propagates the reentrancy error, publication failed.
	s.onSubmit = func(OfflineExecutionOrder) error { _, err := s.submit(sessionOrder("nested"), 100, 0, 3); return err }
	if _, err := s.submit(sessionOrder("outer"), 100, 0, 3); err == nil || !s.blocked {
		t.Fatal("publication failure did not block")
	}
}

func TestSimulationSessionFailuresBlockAndRefuseRestart(t *testing.T) {
	for _, phase := range []string{"submit publication", "simulator", "fanout", "panic", "success"} {
		t.Run(phase, func(t *testing.T) {
			path := filepath.Join(t.TempDir(), "account.lock")
			failure := errors.New("injected failure")
			first, last, calls := 0, 0, 0
			s := mustSession(t, path,
				func(ExecutionReport) error { first++; return nil },
				func(ExecutionReport) error {
					if phase == "fanout" {
						return failure
					}
					if phase == "panic" {
						panic(failure)
					}
					return nil
				},
				func(ExecutionReport) error { last++; return nil },
			)
			s.onSubmit = func(OfflineExecutionOrder) error {
				if phase == "submit publication" {
					return failure
				}
				return nil
			}
			s.simulate = func(o OfflineExecutionOrder, m, f float64, ts int64) (ExecutionReport, error) {
				calls++
				if phase == "simulator" {
					return ExecutionReport{}, failure
				}
				return SimulateOfflineExecution(o, m, f, ts)
			}
			func() {
				if phase == "panic" {
					defer func() {
						if recover() != failure {
							t.Error("panic was swallowed or changed")
						}
					}()
				}
				_, err := s.submit(sessionOrder("first"), 100, .001, 1)
				if phase != "success" && !errors.Is(err, failure) {
					t.Fatalf("wrong error: %v", err)
				}
				if phase == "success" && err != nil {
					t.Fatal(err)
				}
			}()
			if phase != "success" {
				before := calls
				if _, err := s.submit(sessionOrder("next"), 100, .001, 2); err == nil || calls != before || !s.blocked {
					t.Fatal("failed session retried")
				}
			}
			if (phase == "fanout" || phase == "panic") && (first != 1 || last != 0) {
				t.Fatalf("fanout offsets: %d %d", first, last)
			}
			if _, err := newSimulationSession(path); err == nil || !strings.Contains(err.Error(), "cannot resume") {
				t.Fatalf("restart: %v", err)
			}
			fresh := mustSession(t, filepath.Join(filepath.Dir(path), "explicit-new-account.lock"))
			if _, err := fresh.submit(sessionOrder("first"), 100, .001, 1); err != nil {
				t.Fatal(err)
			}
		})
	}
}

func TestSimulationSessionMarkerDurabilityAndCompetition(t *testing.T) {
	for _, failSync := range []int{0, 1, 2} {
		t.Run(fmt.Sprint(failSync), func(t *testing.T) {
			path := filepath.Join(t.TempDir(), "account.lock")
			s, competitor := mustSession(t, path), mustSession(t, path)
			syncs, submitted := 0, 0
			failure := errors.New("sync failed")
			s.syncFile = func(f *os.File) error {
				syncs++
				info, err := f.Stat()
				if err != nil {
					return err
				}
				if info.IsDir() != (syncs == 2) {
					t.Fatal("expected file sync then parent directory sync")
				}
				if syncs == failSync {
					return failure
				}
				return f.Sync()
			}
			s.onSubmit = func(OfflineExecutionOrder) error {
				submitted++
				if syncs != 2 {
					t.Fatal("submit preceded durable marker")
				}
				return nil
			}
			_, err := s.submit(sessionOrder("first"), 100, 0, 1)
			if failSync != 0 && (!errors.Is(err, failure) || submitted != 0 || !s.blocked) {
				t.Fatalf("sync failure: %v", err)
			}
			if failSync == 0 {
				if err != nil {
					t.Fatal(err)
				}
				if _, err := s.submit(sessionOrder("second"), 100, 0, 2); err != nil || syncs != 2 {
					t.Fatalf("marker recreated: %v", err)
				}
			}
			competitor.onSubmit = func(OfflineExecutionOrder) error { t.Fatal("competing session submitted"); return nil }
			if _, err := competitor.submit(sessionOrder("first"), 100, 0, 1); !errors.Is(err, os.ErrExist) || !competitor.blocked {
				t.Fatalf("exclusive create: %v", err)
			}
			if _, err := newSimulationSession(path); err == nil {
				t.Fatal("reopened marker")
			}
		})
	}
}

func TestSimulationSessionMissingParentAndExistingMarkers(t *testing.T) {
	dir := t.TempDir()
	s := mustSession(t, filepath.Join(dir, "missing", "account.lock"))
	s.onSubmit = func(OfflineExecutionOrder) error { t.Fatal("submitted without marker"); return nil }
	if _, err := s.submit(sessionOrder("first"), 100, 0, 1); err == nil || !s.blocked {
		t.Fatal("missing parent admitted")
	}
	for _, name := range []string{"empty", "garbage", "symlink"} {
		path := filepath.Join(dir, name)
		var err error
		if name == "symlink" {
			err = os.Symlink(filepath.Join(dir, "absent"), path)
		} else {
			err = os.WriteFile(path, []byte(strings.TrimPrefix(name, "empty")), 0600)
		}
		if err != nil {
			t.Fatal(err)
		}
		if _, err := newSimulationSession(path); err == nil {
			t.Fatalf("accepted %s marker", name)
		}
	}
}

func TestSimulationSessionCrashRefusesRestart(t *testing.T) {
	if path := os.Getenv("ZENQUANT_TEST_CRASH_MARKER"); path != "" {
		s := mustSession(t, path)
		s.onSubmit = func(OfflineExecutionOrder) error { os.Exit(23); return nil }
		_, _ = s.submit(sessionOrder("crash"), 100, 0, 1)
		t.Fatal("did not exit during submission")
	}
	path := filepath.Join(t.TempDir(), "crash.lock")
	cmd := exec.Command(os.Args[0], "-test.run=^TestSimulationSessionCrashRefusesRestart$")
	cmd.Env = append(os.Environ(), "ZENQUANT_TEST_CRASH_MARKER="+path)
	output, err := cmd.CombinedOutput()
	var exit *exec.ExitError
	if !errors.As(err, &exit) || exit.ExitCode() != 23 {
		t.Fatalf("child exit: %v %s", err, output)
	}
	if _, err := newSimulationSession(path); err == nil || !strings.Contains(err.Error(), "cannot resume") {
		t.Fatalf("crash restart: %v", err)
	}
}
