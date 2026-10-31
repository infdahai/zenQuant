package research

import (
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"math"
	"os"
	"reflect"
	"strings"
	"testing"
)

func fill(symbol string, side int, qty, price, fee float64, ts int64) ExecutionReport {
	return ExecutionReport{Symbol: symbol, Side: side, FilledQty: qty, AvgPrice: price, Fee: fee, TransactionTimeNS: ts}
}
func near(t *testing.T, got, want float64) {
	t.Helper()
	if math.Abs(got-want) > 1e-9 {
		t.Fatalf("got %v, want %v", got, want)
	}
}
func must(t *testing.T, err error) {
	t.Helper()
	if err != nil {
		t.Fatal(err)
	}
}

func TestEpisodeAccounting(t *testing.T) {
	var l PositionEpisodeLedger
	must(t, l.OnFill(fill("BTC", 1, 1, 100, 1, 1)))
	must(t, l.OnFill(fill("BTC", 1, 1, 120, 1, 2)))
	must(t, l.ObservePrice(PriceObservation{"BTC", 3, 140, 90, 125}))
	must(t, l.OnFill(fill("BTC", -1, .5, 130, .5, 4)))
	e := l.OpenEpisodes()[0]
	near(t, e.EntryPrice, 110)
	near(t, e.MaxAbsQty, 2)
	near(t, e.RemainingQty, 1.5)
	near(t, e.GrossRealizedPnL, 10)
	near(t, e.MFEPnL, 60)
	near(t, e.MAEPnL, -40)
	near(t, e.UnrealizedPnL, 30)
	must(t, l.AllocateFunding(FundingAllocation{"BTC", 4, -2}))
	must(t, l.OnFill(fill("BTC", -1, 1.5, 130, 1.5, 5)))
	e = l.ClosedEpisodes()[0]
	if !e.Closed || *e.ClosedAtNS != 5 || e.FillCount != 4 || e.AddCount != 1 || e.ReduceCount != 2 || e.HoldingDurationNS != 4 {
		t.Fatalf("unexpected episode: %+v", e)
	}
	near(t, *e.ExitPrice, 130)
	near(t, e.GrossRealizedPnL, 40)
	near(t, e.Fees, 4)
	near(t, e.FundingPnL, -2)
	near(t, e.NetPnL, 34)
	hash := sha256.Sum256([]byte("BTC|1|1|1"))
	if e.EpisodeID != fmt.Sprintf("%x", hash) {
		t.Fatal(e.EpisodeID)
	}
	data, err := json.Marshal(e)
	must(t, err)
	if !strings.Contains(string(data), `"holding_duration_ns":4`) {
		t.Fatal(string(data))
	}
}

func TestFlipAndReopen(t *testing.T) {
	var l PositionEpisodeLedger
	must(t, l.OnFill(fill("BTC", 1, 2, 100, 2, 1)))
	must(t, l.OnFill(fill("BTC", -1, 3, 90, 3, 2)))
	a, b := l.ClosedEpisodes()[0], l.OpenEpisodes()[0]
	near(t, a.GrossRealizedPnL, -20)
	near(t, a.Fees, 4)
	near(t, a.NetPnL, -24)
	near(t, b.RemainingQty, 1)
	near(t, b.Fees, 1)
	must(t, l.ObservePrice(PriceObservation{"BTC", 3, 100, 80, 85}))
	b = l.OpenEpisodes()[0]
	near(t, b.MFEPnL, 10)
	near(t, b.MAEPnL, -10)
	near(t, b.UnrealizedPnL, 5)
	must(t, l.OnFill(fill("BTC", 1, 1, 80, 1, 4)))
	must(t, l.OnFill(fill("BTC", -1, .3, 80, 0, 5)))
	must(t, l.OnFill(fill("BTC", 1, .1+.2, 81, 0, 6)))
	if len(l.ClosedEpisodes()) != 3 || len(l.OpenEpisodes()) != 0 || l.PositionSwitchCount() != 1 {
		t.Fatalf("unexpected state %+v switches %d", l.Episodes(), l.PositionSwitchCount())
	}
	near(t, l.ClosedEpisodes()[1].NetPnL, 8)
}

func TestBatchPriorityOrderAndSymbolIsolation(t *testing.T) {
	fills := []ExecutionReport{fill("BTC", -1, 1, 110, 1, 2), fill("ETH", -1, 2, 50, 0, 1), fill("BTC", 1, 1, 100, 1, 1)}
	funding := []FundingAllocation{{"BTC", 2, -2}, {"ETH", 2, 1.5}, {"BTC", 1, 100}}
	prices := []PriceObservation{{"BTC", 2, 120, 90, 110}, {"BTC", 1, 500, 50, 100}}
	episodes, err := BuildPositionEpisodes(fills, funding, prices)
	must(t, err)
	if len(episodes) != 2 || episodes[0].Symbol != "BTC" || episodes[1].Symbol != "ETH" {
		t.Fatalf("ordering: %+v", episodes)
	}
	btc, eth := episodes[0], episodes[1]
	near(t, btc.FundingPnL, -2)
	near(t, btc.MFEPnL, 20)
	near(t, btc.MAEPnL, -10)
	near(t, btc.NetPnL, 6)
	near(t, eth.FundingPnL, 1.5)
}

func TestChronologyValidationAndSnapshots(t *testing.T) {
	var l PositionEpisodeLedger
	must(t, l.ObservePrice(PriceObservation{"BTC", 5, 101, 99, 100}))
	if err := l.OnFill(fill("BTC", 1, 1, 100, 0, 4)); err == nil || !strings.Contains(err.Error(), "chronological") {
		t.Fatalf("expected chronological error: %v", err)
	}
	must(t, l.OnFill(fill("BTC", 0, 0, 0, 0, 0)))
	if err := l.OnFill(fill("BTC", 0, 1, 100, 0, 5)); err == nil {
		t.Fatal("invalid side accepted")
	}
	if err := l.ObservePrice(PriceObservation{"BTC", 6, 90, 100, 100}); err == nil {
		t.Fatal("invalid range accepted")
	}
	must(t, l.OnFill(fill("ETH", 1, 1, 100, 0, 1)))
	must(t, l.OnFill(fill("ETH", -1, 1, 110, 0, 2)))
	copy := l.ClosedEpisodes()
	*copy[0].ExitPrice = 0
	*copy[0].ClosedAtNS = 0
	again := l.ClosedEpisodes()[0]
	near(t, *again.ExitPrice, 110)
	if *again.ClosedAtNS != 2 {
		t.Fatal("snapshot changed internal state")
	}
}

func TestExcursionsAfterReduction(t *testing.T) {
	var l PositionEpisodeLedger
	must(t, l.OnFill(fill("BTC", 1, 2, 100, 2, 1)))
	must(t, l.OnFill(fill("BTC", -1, 1, 110, 1, 2)))
	must(t, l.ObservePrice(PriceObservation{"BTC", 3, 120, 80, 105}))
	e := l.OpenEpisodes()[0]
	near(t, e.GrossRealizedPnL, 10)
	near(t, e.MFEPnL, 30)
	near(t, e.MAEPnL, -10)
	near(t, e.UnrealizedPnL, 5)
	near(t, e.NetPnL, 7)
}

func TestLedgerCopiesShareCompleteState(t *testing.T) {
	l := NewPositionEpisodeLedger()
	copy := *l
	must(t, copy.OnFill(fill("BTC", 1, 1, 100, 0, 1)))
	must(t, l.OnFill(fill("BTC", -1, 2, 110, 0, 2)))
	if !reflect.DeepEqual(l.Episodes(), copy.Episodes()) || l.PositionSwitchCount() != 1 || copy.PositionSwitchCount() != 1 || len(copy.ClosedEpisodes()) != 1 {
		t.Fatalf("copies diverged: original=%+v copy=%+v", l.Episodes(), copy.Episodes())
	}
}

func TestDurationOverflowRejectedWithoutMutation(t *testing.T) {
	var l PositionEpisodeLedger
	must(t, l.OnFill(fill("BTC", 1, 1, 100, 0, math.MinInt64)))
	must(t, l.AllocateFunding(FundingAllocation{"BTC", -1, 1}))
	before := l.Episodes()
	if before[0].HoldingDurationNS != math.MaxInt64 {
		t.Fatalf("duration: %d", before[0].HoldingDurationNS)
	}
	for _, apply := range []func() error{
		func() error { return l.ObservePrice(PriceObservation{"BTC", 0, 100, 100, 100}) },
		func() error { return l.AllocateFunding(FundingAllocation{"BTC", 0, 1}) },
		func() error { return l.OnFill(fill("BTC", -1, 1, 100, 0, 0)) },
	} {
		if err := apply(); err == nil || !strings.Contains(err.Error(), "exceeds int64") {
			t.Fatalf("expected overflow error, got %v", err)
		}
		if !reflect.DeepEqual(l.Episodes(), before) {
			t.Fatal("rejected event changed state")
		}
	}
	must(t, l.OnFill(fill("BTC", -1, 1, 100, 0, -1)))
	if l.ClosedEpisodes()[0].HoldingDurationNS != math.MaxInt64 {
		t.Fatal("boundary duration not preserved")
	}
}

func TestRejectNonFiniteInputsWithoutMutation(t *testing.T) {
	for _, value := range []float64{math.NaN(), math.Inf(1), math.Inf(-1)} {
		t.Run(fmt.Sprint(value), func(t *testing.T) {
			var l PositionEpisodeLedger
			must(t, l.OnFill(fill("BTC", 1, 1, 100, 1, 1)))
			before := l.Episodes()
			badFills := []ExecutionReport{
				fill("BTC", 1, value, 100, 1, 3), fill("BTC", 1, 1, value, 1, 3),
				fill("BTC", 1, 1, 100, value, 3), fill("BTC", 1, 0, 100, value, 3),
			}
			var operations []func() error
			for _, report := range badFills {
				report := report
				operations = append(operations, func() error { return l.OnFill(report) })
			}
			for _, observation := range []PriceObservation{
				{"BTC", 3, value, 90, 100}, {"BTC", 3, 110, value, 100}, {"BTC", 3, 110, 90, value},
			} {
				observation := observation
				operations = append(operations, func() error { return l.ObservePrice(observation) })
			}
			operations = append(operations, func() error { return l.AllocateFunding(FundingAllocation{"BTC", 3, value}) })
			for _, operation := range operations {
				if err := operation(); err == nil || !strings.Contains(err.Error(), "finite") {
					t.Fatalf("expected finite validation error, got %v", err)
				}
				if !reflect.DeepEqual(l.Episodes(), before) {
					t.Fatal("rejected event changed state")
				}
			}
			// An earlier valid event proves rejected calls did not advance the clock.
			must(t, l.AllocateFunding(FundingAllocation{"BTC", 2, 1}))
			near(t, l.OpenEpisodes()[0].FundingPnL, 1)
		})
	}
}

type goldenLedgerState struct {
	Episodes            []PositionEpisode  `json:"episodes"`
	OpenEpisodes        []PositionEpisode  `json:"open_episodes"`
	ClosedEpisodes      []PositionEpisode  `json:"closed_episodes"`
	PositionQty         map[string]float64 `json:"position_qty"`
	PositionSwitchCount int                `json:"position_switch_count"`
}

type ledgerGolden struct {
	Version int `json:"version"`
	Cases   []struct {
		Name  string `json:"name"`
		Steps []struct {
			Event json.RawMessage `json:"event"`
			Error *struct {
				Type    string `json:"type"`
				Message string `json:"message"`
			} `json:"error"`
			State goldenLedgerState `json:"state"`
		} `json:"steps"`
	} `json:"cases"`
	Builder struct {
		Input struct {
			Fills   []ExecutionReport   `json:"fills"`
			Funding []FundingAllocation `json:"funding"`
			Prices  []PriceObservation  `json:"prices"`
		} `json:"input"`
		ExpectedEpisodes []PositionEpisode `json:"expected_episodes"`
	} `json:"builder"`
}

func TestEpisodeLedgerPythonGolden(t *testing.T) {
	data, err := os.ReadFile("testdata/episode_ledger_golden.json")
	must(t, err)
	var fixture ledgerGolden
	must(t, json.Unmarshal(data, &fixture))
	if fixture.Version != 1 || len(fixture.Cases) == 0 {
		t.Fatal("unexpected or empty golden fixture")
	}
	for _, c := range fixture.Cases {
		t.Run(c.Name, func(t *testing.T) {
			var ledger PositionEpisodeLedger
			for i, step := range c.Steps {
				var kind struct {
					Type string `json:"type"`
				}
				must(t, json.Unmarshal(step.Event, &kind))
				var gotErr error
				switch kind.Type {
				case "fill":
					var event ExecutionReport
					must(t, json.Unmarshal(step.Event, &event))
					gotErr = ledger.OnFill(event)
				case "funding":
					var event FundingAllocation
					must(t, json.Unmarshal(step.Event, &event))
					gotErr = ledger.AllocateFunding(event)
				case "price":
					var event PriceObservation
					must(t, json.Unmarshal(step.Event, &event))
					gotErr = ledger.ObservePrice(event)
				default:
					t.Fatalf("step %d: unknown event %q", i, kind.Type)
				}
				if step.Error == nil {
					if gotErr != nil {
						t.Fatalf("step %d: unexpected error: %v", i, gotErr)
					}
				} else if step.Error.Type != "ValueError" || gotErr == nil || gotErr.Error() != step.Error.Message {
					t.Fatalf("step %d: error = %v, want %s: %s", i, gotErr, step.Error.Type, step.Error.Message)
				}
				quantities := make(map[string]float64, len(step.State.PositionQty))
				for symbol := range step.State.PositionQty {
					quantities[symbol] = ledger.PositionQty(symbol)
				}
				got := goldenLedgerState{ledger.Episodes(), ledger.OpenEpisodes(), ledger.ClosedEpisodes(), quantities, ledger.PositionSwitchCount()}
				if !reflect.DeepEqual(got, step.State) {
					gotJSON, _ := json.Marshal(got)
					wantJSON, _ := json.Marshal(step.State)
					t.Fatalf("step %d: state mismatch\ngot  %s\nwant %s", i, gotJSON, wantJSON)
				}
			}
		})
	}
	t.Run("builder", func(t *testing.T) {
		in := fixture.Builder.Input
		got, err := BuildPositionEpisodes(in.Fills, in.Funding, in.Prices)
		must(t, err)
		if !reflect.DeepEqual(got, fixture.Builder.ExpectedEpisodes) {
			gotJSON, _ := json.Marshal(got)
			wantJSON, _ := json.Marshal(fixture.Builder.ExpectedEpisodes)
			t.Fatalf("builder mismatch\ngot  %s\nwant %s", gotJSON, wantJSON)
		}
	})
}
