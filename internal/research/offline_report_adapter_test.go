package research

import (
	"math"
	"reflect"
	"testing"
)

func TestOfflineReportAdapterSimulatedExecution(t *testing.T) {
	flow, err := NewStrategyFlow(DefaultStrategyFlowConfig())
	if err != nil {
		t.Fatal(err)
	}
	risk, err := NewStatefulRiskOverlay(DefaultStatefulRiskConfig())
	if err != nil {
		t.Fatal(err)
	}
	adapter, err := NewOfflineReportAdapter(flow, risk)
	if err != nil {
		t.Fatal(err)
	}
	report, err := SimulateOfflineExecution(OfflineExecutionOrder{ClientOrderID: "one", Symbol: "BTCUSDT_PERP", Side: 1, Quantity: 2}, 100, .01, 10)
	if err != nil {
		t.Fatal(err)
	}
	if err = adapter.Dispatch(report, "USD", 20); err != nil {
		t.Fatal(err)
	}
	state := risk.State()
	if state.Positions[report.Symbol] != 2 || state.Cash != 99798 || state.Equity != 99998 || flow.positions[report.Symbol].CurrentQty != 2 {
		t.Fatalf("incorrect feedback: risk=%+v strategy=%+v", state, flow.positions[report.Symbol])
	}
	checkRejected := func(r ExecutionReport, currency string, expiry int64) {
		t.Helper()
		before := risk.State()
		pos := *flow.positions[report.Symbol]
		if err := adapter.Dispatch(r, currency, expiry); err == nil {
			t.Fatalf("accepted invalid report: %+v", r)
		}
		if !reflect.DeepEqual(before, risk.State()) || pos != *flow.positions[report.Symbol] {
			t.Fatal("rejected report changed component state")
		}
	}
	checkRejected(report, "USD", 20)
	second := report
	second.ClientOrderID = "two"
	second.TransactionTimeNS = 9
	checkRejected(second, "USD", 20)
	second.TransactionTimeNS = 11
	second.Status = "PARTIALLY_FILLED"
	second.FilledQty = 0
	checkRejected(second, "USD", 20)
	second.Status = "REJECTED"
	second.FilledQty = 0
	second.Fee = 0
	checkRejected(second, "USD", 20)
	second = report
	second.ClientOrderID = "two"
	second.TransactionTimeNS = 11
	checkRejected(second, "BTC", 20)
	checkRejected(second, "USD", 10)
	second.Fee = math.NaN()
	checkRejected(second, "USD", 20)
	second.Fee = 1
	second.Side = -1
	second.FilledQty = .5
	if err := adapter.Dispatch(second, "USD", 20); err != nil {
		t.Fatal(err)
	}
	if risk.State().Positions[report.Symbol] != 1.5 || flow.positions[report.Symbol].CurrentQty != 1.5 || risk.State().Equity != 99997 {
		t.Fatal("second incremental fill or fee incorrect")
	}
}

func TestOfflineReportAdapterPartialFillAndRetry(t *testing.T) {
	flow, _ := NewStrategyFlow(DefaultStrategyFlowConfig())
	risk, _ := NewStatefulRiskOverlay(DefaultStatefulRiskConfig())
	adapter, _ := NewOfflineReportAdapter(flow, risk)
	partial := ExecutionReport{ClientOrderID: "attempt-1", Symbol: "BTC", Side: 1, Status: "PARTIALLY_FILLED", FilledQty: 0.4, AvgPrice: 100, Fee: 0.4, TransactionTimeNS: 10}
	if err := adapter.Dispatch(partial, "USD", 20); err != nil {
		t.Fatal(err)
	}
	before := risk.State()
	position := *flow.positions["BTC"]
	if before.Positions["BTC"] != 0.4 || before.Cash != 99959.6 || before.Equity != 99999.6 || position.CurrentQty != 0.4 {
		t.Fatalf("partial fill incorrect: %+v %+v", before, position)
	}
	if err := adapter.Dispatch(partial, "USD", 20); err == nil {
		t.Fatal("duplicate partial fill accepted")
	}
	if !reflect.DeepEqual(before, risk.State()) || position != *flow.positions["BTC"] {
		t.Fatal("duplicate changed state")
	}
	retry := partial
	retry.ClientOrderID = "attempt-2"
	retry.Status = "FILLED"
	retry.FilledQty = 0.6
	retry.Fee = 0.6
	retry.TransactionTimeNS = 11
	if err := adapter.Dispatch(retry, "USD", 20); err != nil {
		t.Fatal(err)
	}
	state := risk.State()
	if state.Positions["BTC"] != 1 || flow.positions["BTC"].CurrentQty != 1 || state.Cash != 99899 || state.Equity != 99999 {
		t.Fatalf("retry was not incremental: %+v %+v", state, flow.positions["BTC"])
	}
}

func TestOfflineReportAdapterRejectsClampedFees(t *testing.T) {
	for _, tc := range []struct {
		name     string
		quantity float64
	}{
		{"fully invested", 1000},
		{"leveraged", 2000},
	} {
		t.Run(tc.name, func(t *testing.T) {
			flow, _ := NewStrategyFlow(DefaultStrategyFlowConfig())
			risk, _ := NewStatefulRiskOverlay(DefaultStatefulRiskConfig())
			adapter, _ := NewOfflineReportAdapter(flow, risk)
			report, err := SimulateOfflineExecution(OfflineExecutionOrder{ClientOrderID: "fill", Symbol: "BTC", Side: 1, Quantity: tc.quantity}, 100, .001, 1)
			if err != nil {
				t.Fatal(err)
			}
			before := risk.State()
			if err := adapter.Dispatch(report, "USD", 10); err == nil {
				t.Fatal("accepted fee that would be clamped")
			}
			if !reflect.DeepEqual(before, risk.State()) || len(flow.positions) != 0 {
				t.Fatal("rejected fill changed state")
			}
			// Rejection does not consume the ID; a corrected report can be sent.
			report.Fee = 0
			if err := adapter.Dispatch(report, "USD", 10); err != nil {
				t.Fatal(err)
			}
			if risk.State().Cash != 100000-tc.quantity*100 || risk.State().Equity != 100000 {
				t.Fatalf("incorrect zero-fee accounting: %+v", risk.State())
			}
		})
	}
}

func TestOfflineReportAdapterPropagatesRiskAndStrategyErrors(t *testing.T) {
	flow, _ := NewStrategyFlow(DefaultStrategyFlowConfig())
	risk, _ := NewStatefulRiskOverlay(DefaultStatefulRiskConfig())
	a, _ := NewOfflineReportAdapter(flow, risk)
	r := ExecutionReport{ClientOrderID: "x", Symbol: "BTC", Side: 1, Status: "FILLED", FilledQty: 1, AvgPrice: 100, TransactionTimeNS: 1}
	if err := risk.OnIndicator(StatefulRiskIndicator{Symbol: "BTC", Timeframe: "4h", Price: 100, CutoffTimeNS: 2, AvailableTimeNS: 2, ValidUntilNS: 20}); err != nil {
		t.Fatal(err)
	}
	if err := a.Dispatch(r, "USD", 20); err == nil || len(flow.positions) != 0 {
		t.Fatal("risk clock failure not propagated")
	}
	r.TransactionTimeNS = 2
	r.FilledQty = 1e308
	r.AvgPrice = 2
	if err := a.Dispatch(r, "USD", 20); err == nil || len(flow.positions) != 0 {
		t.Fatal("risk overflow not propagated")
	}
	r.FilledQty = 1
	r.AvgPrice = 100
	if err := a.Dispatch(r, "USD", 20); err != nil {
		t.Fatal(err)
	}
	// A strategy overflow after a successful risk preview must propagate.
	flow.positions["BTC"].CurrentQty = math.MaxFloat64
	r.ClientOrderID = "y"
	r.FilledQty = math.MaxFloat64
	r.AvgPrice = 1e-308
	before := risk.State()
	pos := *flow.positions["BTC"]
	if err := a.Dispatch(r, "USD", 20); err == nil || !reflect.DeepEqual(before, risk.State()) || pos != *flow.positions["BTC"] {
		t.Fatal("strategy error was lost or changed state")
	}
}
