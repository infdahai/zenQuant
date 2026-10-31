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
	rows := []ReplayFundingRow{{2, .01}, {4, .02}, {4, .03}, {20, .5}}
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
	// Only the latest visible row (the second source-time 4 row) is used.
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
	replayNear(t, e.FundingPnL, -6.6)
	replayNear(t, e.MFEPnL, 40)
	replayNear(t, e.MAEPnL, -40)
	if e.OpenedAtNS != 1 || *e.ClosedAtNS != 6 {
		t.Fatalf("episode times: %+v", e)
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
	if _, err := NewReplayAccounting("BTC", &ledger, []ReplayFundingRow{{2, 0}, {1, 0}}); err == nil {
		t.Fatal("expected unordered funding error")
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
	a.SetCutoff(5) // A non-execution event can also make reports visible.
	r, err = a.CollectReport(ExecutionReport{Symbol: "BTC", Side: 1, FilledQty: 1, AvgPrice: 100, TransactionTimeNS: 7})
	if err != nil || r.TransactionTimeNS != 7 {
		t.Fatalf("future report=%+v err=%v", r, err)
	}
	if err := a.OnExecutionBar(ReplayExecutionBar{6, 110, 90, 100}); err == nil {
		t.Fatal("expected ledger chronological error")
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
