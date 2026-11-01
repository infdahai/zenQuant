package research

import (
	"math"
	"testing"
)

func replayNear(t *testing.T, got, want float64) {
	t.Helper()
	if math.IsNaN(got) || math.IsNaN(want) || math.Abs(got-want) > 1e-9 {
		t.Fatalf("got %v, want %v", got, want)
	}
}

func TestReplayAccountingRollsBackFundingWhenPriceOverflows(t *testing.T) {
	var ledger PositionEpisodeLedger
	a, err := NewReplayAccounting("BTC", &ledger, []ReplayFundingRow{{2, .1}})
	if err != nil {
		t.Fatal(err)
	}
	_, err = a.CollectReport(ExecutionReport{Symbol: "BTC", Side: 1, FilledQty: 2, AvgPrice: 1, TransactionTimeNS: 1})
	if err != nil {
		t.Fatal(err)
	}
	before := ledger.OpenEpisodes()[0]
	if err := a.OnExecutionBar(ReplayExecutionBar{2, math.MaxFloat64, 1, 1}); err == nil {
		t.Fatal("expected excursion overflow")
	}
	if got := ledger.OpenEpisodes()[0]; got != before {
		t.Fatalf("ledger changed: before=%+v after=%+v", before, got)
	}
	if a.hasCutoff || a.fundingIndex != -1 || a.lastFundingTime != 0 {
		t.Fatalf("mutated adapter: %+v", a)
	}
	// A timestamp between the original fill and rejected bar proves the ledger clock was restored.
	_, err = a.CollectReport(ExecutionReport{Symbol: "BTC", Side: 1, FilledQty: 1, AvgPrice: 1, TransactionTimeNS: 1})
	if err != nil {
		t.Fatal(err)
	}
	if err := a.OnExecutionBar(ReplayExecutionBar{2, 2, 1, 1}); err != nil {
		t.Fatal(err)
	}
	replayNear(t, ledger.OpenEpisodes()[0].FundingPnL, -.3)
}

func TestReplayAccountingRejectsOverflowingFundingWithoutMutation(t *testing.T) {
	var ledger PositionEpisodeLedger
	a, err := NewReplayAccounting("BTC", &ledger, []ReplayFundingRow{{2, 2}})
	if err != nil {
		t.Fatal(err)
	}
	_, err = a.CollectReport(ExecutionReport{Symbol: "BTC", Side: 1, FilledQty: 2, AvgPrice: 1, TransactionTimeNS: 1})
	if err != nil {
		t.Fatal(err)
	}
	if err := a.OnExecutionBar(ReplayExecutionBar{2, math.MaxFloat64, 1, math.MaxFloat64}); err == nil {
		t.Fatal("expected funding overflow error")
	}
	if a.hasCutoff || a.fundingIndex != -1 || a.lastFundingTime != 0 {
		t.Fatalf("mutated adapter: %+v", a)
	}
	replayNear(t, ledger.OpenEpisodes()[0].FundingPnL, 0)
	if err := a.OnExecutionBar(ReplayExecutionBar{2, 2, 1, 1}); err != nil {
		t.Fatal(err)
	}
	replayNear(t, ledger.OpenEpisodes()[0].FundingPnL, -4)
}

func TestReplayAccountingRejectsNonFiniteInputs(t *testing.T) {
	for _, value := range []float64{math.NaN(), math.Inf(1), math.Inf(-1)} {
		var ledger PositionEpisodeLedger
		if _, err := NewReplayAccounting("BTC", &ledger, []ReplayFundingRow{{1, value}}); err == nil {
			t.Fatalf("accepted funding rate %v", value)
		}
		for field := 0; field < 3; field++ {
			var ledger PositionEpisodeLedger
			a, err := NewReplayAccounting("BTC", &ledger, []ReplayFundingRow{{2, .1}})
			if err != nil {
				t.Fatal(err)
			}
			_, err = a.CollectReport(ExecutionReport{Symbol: "BTC", Side: 1, FilledQty: 1, AvgPrice: 100, TransactionTimeNS: 1})
			if err != nil {
				t.Fatal(err)
			}
			bar := ReplayExecutionBar{2, 110, 90, 100}
			switch field {
			case 0:
				bar.High = value
			case 1:
				bar.Low = value
			case 2:
				bar.Close = value
			}
			if err := a.OnExecutionBar(bar); err == nil {
				t.Fatalf("accepted field %d value %v", field, value)
			}
			if a.hasCutoff || a.fundingIndex != -1 || a.lastFundingTime != 0 {
				t.Fatalf("mutated adapter: %+v", a)
			}
			replayNear(t, ledger.OpenEpisodes()[0].FundingPnL, 0)
			if err := a.OnExecutionBar(ReplayExecutionBar{2, 110, 90, 100}); err != nil {
				t.Fatal(err)
			}
			replayNear(t, ledger.OpenEpisodes()[0].FundingPnL, -10)
		}
	}
}

func TestReplayAccountingRejectedBarDoesNotConsumeFunding(t *testing.T) {
	var ledger PositionEpisodeLedger
	a, err := NewReplayAccounting("BTC", &ledger, []ReplayFundingRow{{2, .1}})
	if err != nil {
		t.Fatal(err)
	}
	_, err = a.CollectReport(ExecutionReport{Symbol: "BTC", Side: 1, FilledQty: 1, AvgPrice: 100, TransactionTimeNS: 1})
	if err != nil {
		t.Fatal(err)
	}
	if err := a.OnExecutionBar(ReplayExecutionBar{2, 90, 110, 120}); err == nil {
		t.Fatal("expected invalid bar error")
	}
	replayNear(t, ledger.OpenEpisodes()[0].FundingPnL, 0)
	if err := a.OnExecutionBar(ReplayExecutionBar{2, 110, 90, 100}); err != nil {
		t.Fatal(err)
	}
	replayNear(t, ledger.OpenEpisodes()[0].FundingPnL, -10)
	if _, err := a.CollectReport(ExecutionReport{Symbol: "ETH", Side: 1, FilledQty: 1, AvgPrice: 10, TransactionTimeNS: 2}); err == nil {
		t.Fatal("expected symbol mismatch")
	}
	if len(ledger.Episodes()) != 1 {
		t.Fatalf("unexpected episodes: %+v", ledger.Episodes())
	}
}

func TestReplayAccountingFundingAndFillTiming(t *testing.T) {
	var ledger PositionEpisodeLedger
	rows := []ReplayFundingRow{{2, .01}, {4, .02}, {5, .03}, {20, .5}}
	a, err := NewReplayAccounting("BTC", &ledger, rows)
	if err != nil {
		t.Fatal(err)
	}
	rows[2].FundingRate = 99 // The adapter owns a snapshot.
	if err := a.OnExecutionBar(ReplayExecutionBar{1, 200, 50, 100}); err != nil {
		t.Fatal(err)
	}
	_, err = a.CollectReport(ExecutionReport{Symbol: "BTC", Side: 1, FilledQty: 2, AvgPrice: 100, TransactionTimeNS: 0})
	if err != nil {
		t.Fatal(err)
	}
	// Earlier settlements use the previous close; the boundary uses this close.
	if err := a.OnExecutionBar(ReplayExecutionBar{5, 120, 80, 110}); err != nil {
		t.Fatal(err)
	}
	if err := a.OnExecutionBar(ReplayExecutionBar{6, 115, 90, 105}); err != nil {
		t.Fatal(err)
	}
	report, err := a.CollectReport(ExecutionReport{Symbol: "BTC", Side: -1, FilledQty: 2, AvgPrice: 105, TransactionTimeNS: 3})
	if err != nil {
		t.Fatal(err)
	}
	if report.TransactionTimeNS != 6 {
		t.Fatalf("normalized report: %+v", report)
	}
	e := ledger.ClosedEpisodes()[0]
	replayNear(t, e.FundingPnL, -12.6)
	replayNear(t, e.MFEPnL, 40)
	replayNear(t, e.MAEPnL, -40)
	if e.OpenedAtNS != 1 || *e.ClosedAtNS != 6 {
		t.Fatalf("episode times: %+v", e)
	}
}

func TestReplayAccountingRollsBackMultipleSettlements(t *testing.T) {
	var ledger PositionEpisodeLedger
	a, err := NewReplayAccounting("BTC", &ledger, []ReplayFundingRow{{2, .1}, {3, math.MaxFloat64}})
	if err != nil {
		t.Fatal(err)
	}
	if err := a.OnExecutionBar(ReplayExecutionBar{1, 2, 1, 1}); err != nil {
		t.Fatal(err)
	}
	if _, err := a.CollectReport(ExecutionReport{Symbol: "BTC", Side: 1, FilledQty: 2, AvgPrice: 1, TransactionTimeNS: 1}); err != nil {
		t.Fatal(err)
	}
	if err := a.OnExecutionBar(ReplayExecutionBar{3, 2, 1, 1}); err == nil {
		t.Fatal("expected second settlement to overflow")
	}
	replayNear(t, ledger.OpenEpisodes()[0].FundingPnL, 0)
	if a.fundingIndex != -1 || a.cutoff != 1 || a.lastFundingTime != 0 {
		t.Fatalf("settlement cursor changed: %+v", a)
	}
}

func TestReplayAccountingFlatFundingAndNoInventedFill(t *testing.T) {
	var ledger PositionEpisodeLedger
	a, err := NewReplayAccounting("BTC", &ledger, []ReplayFundingRow{{0, .5}, {2, .1}, {4, .2}})
	if err != nil {
		t.Fatal(err)
	}
	if err := a.OnExecutionBar(ReplayExecutionBar{2, 120, 80, 100}); err != nil {
		t.Fatal(err)
	}
	// A rejected report carries no fill; it must not open an episode.
	r, err := a.CollectReport(ExecutionReport{Symbol: "BTC", Status: "REJECTED", TransactionTimeNS: 1})
	if err != nil || r.TransactionTimeNS != 2 || len(ledger.Episodes()) != 0 {
		t.Fatalf("report=%+v episodes=%+v err=%v", r, ledger.Episodes(), err)
	}
	_, err = a.CollectReport(ExecutionReport{Symbol: "BTC", Side: -1, FilledQty: 1, AvgPrice: 100, TransactionTimeNS: 2})
	if err != nil {
		t.Fatal(err)
	}
	// Previously consumed funding must not be charged after opening.
	if err := a.OnExecutionBar(ReplayExecutionBar{3, 110, 90, 100}); err != nil {
		t.Fatal(err)
	}
	replayNear(t, ledger.OpenEpisodes()[0].FundingPnL, 0)
	if err := a.OnExecutionBar(ReplayExecutionBar{5, 110, 90, 100}); err != nil {
		t.Fatal(err)
	}
	replayNear(t, ledger.OpenEpisodes()[0].FundingPnL, 20)
}

func TestReplayAccountingReportCutoffsAndErrors(t *testing.T) {
	var ledger PositionEpisodeLedger
	if _, err := NewReplayAccounting("BTC", nil, nil); err == nil {
		t.Fatal("expected nil ledger error")
	}
	for _, rows := range [][]ReplayFundingRow{{{2, 0}, {1, 0}}, {{2, 0}, {2, 0}}} {
		if _, err := NewReplayAccounting("BTC", &ledger, rows); err == nil {
			t.Fatal("expected unordered or duplicate funding error")
		}
	}
	a, err := NewReplayAccounting("BTC", &ledger, nil)
	if err != nil {
		t.Fatal(err)
	}
	// Without an event cutoff, the source report time is retained.
	r, err := a.CollectReport(ExecutionReport{Symbol: "BTC", Side: 1, FilledQty: 1, AvgPrice: 100, TransactionTimeNS: 3})
	if err != nil || r.TransactionTimeNS != 3 {
		t.Fatalf("report=%+v err=%v", r, err)
	}
	if err := a.SetCutoff(5); err != nil {
		t.Fatal(err)
	}
	r, err = a.CollectReport(ExecutionReport{Symbol: "BTC", Side: 1, FilledQty: 1, AvgPrice: 100, TransactionTimeNS: 7})
	if err == nil {
		t.Fatalf("accepted future report=%+v", r)
	}
	if err := a.SetCutoff(4); err == nil {
		t.Fatal("accepted backwards cutoff")
	}
	if err := a.OnExecutionBar(ReplayExecutionBar{4, 110, 90, 100}); err == nil {
		t.Fatal("accepted bar before cutoff")
	}
	if err := a.OnExecutionBar(ReplayExecutionBar{6, 110, 90, 100}); err != nil {
		t.Fatal(err)
	}
}

func TestReplayAccountingRejectsReportsAheadOfSettlements(t *testing.T) {
	for _, initiallyOpen := range []bool{false, true} {
		var ledger PositionEpisodeLedger
		a, err := NewReplayAccounting("BTC", &ledger, []ReplayFundingRow{{2, .1}})
		if err != nil {
			t.Fatal(err)
		}
		if err := a.OnExecutionBar(ReplayExecutionBar{1, 100, 100, 100}); err != nil {
			t.Fatal(err)
		}
		if initiallyOpen {
			if _, err := a.CollectReport(ExecutionReport{Symbol: "BTC", Side: 1, FilledQty: 1, AvgPrice: 100, TransactionTimeNS: 1}); err != nil {
				t.Fatal(err)
			}
		}
		side := 1
		if initiallyOpen {
			side = -1
		}
		future := ExecutionReport{Symbol: "BTC", Side: side, FilledQty: 1, AvgPrice: 100, TransactionTimeNS: 4}
		if _, err := a.CollectReport(future); err == nil {
			t.Fatal("accepted report beyond cutoff")
		}
		if err := a.SetCutoff(4); err != nil {
			t.Fatal(err)
		}
		if _, err := a.CollectReport(future); err == nil {
			t.Fatal("accepted report before pending settlement")
		}
		if err := a.OnExecutionBar(ReplayExecutionBar{5, 110, 100, 110}); err != nil {
			t.Fatal(err)
		}
		if initiallyOpen {
			replayNear(t, ledger.OpenEpisodes()[0].FundingPnL, -10)
		} else if len(ledger.Episodes()) != 0 {
			t.Fatal("future fill changed the position")
		}
		if _, err := a.CollectReport(future); err != nil {
			t.Fatal(err)
		}
	}
}

func TestReplayAccountingPriceBeforeOpeningAndClosingFills(t *testing.T) {
	var ledger PositionEpisodeLedger
	a, err := NewReplayAccounting("BTC", &ledger, []ReplayFundingRow{{1, .1}, {2, .1}})
	if err != nil {
		t.Fatal(err)
	}
	if err := a.OnExecutionBar(ReplayExecutionBar{1, 200, 50, 100}); err != nil {
		t.Fatal(err)
	}
	_, err = a.CollectReport(ExecutionReport{Symbol: "BTC", Side: 1, FilledQty: 1, AvgPrice: 100, TransactionTimeNS: 1})
	if err != nil {
		t.Fatal(err)
	}
	if err := a.OnExecutionBar(ReplayExecutionBar{2, 110, 90, 100}); err != nil {
		t.Fatal(err)
	}
	_, err = a.CollectReport(ExecutionReport{Symbol: "BTC", Side: -1, FilledQty: 1, AvgPrice: 100, TransactionTimeNS: 2})
	if err != nil {
		t.Fatal(err)
	}
	e := ledger.ClosedEpisodes()[0]
	replayNear(t, e.MFEPnL, 10)
	replayNear(t, e.MAEPnL, -10)
	replayNear(t, e.FundingPnL, -10)
}
