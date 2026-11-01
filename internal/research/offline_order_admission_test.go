package research

import (
	"encoding/json"
	"os"
	"reflect"
	"testing"
)

type routerFixture struct {
	Cap  float64 `json:"cap"`
	Rows []struct {
		Cutoff       int64                 `json:"cutoff"`
		Order        OfflineExecutionOrder `json:"order"`
		Report       *ExecutionReport      `json:"report"`
		Position     float64               `json:"position"`
		RiskPosition float64               `json:"risk_position"`
	} `json:"rows"`
}

func loadRouterFixture(t *testing.T) routerFixture {
	t.Helper()
	data, err := os.ReadFile("testdata/offline_router_reference.json")
	if err != nil {
		t.Fatal(err)
	}
	var f routerFixture
	if err := json.Unmarshal(data, &f); err != nil {
		t.Fatal(err)
	}
	return f
}
func TestOfflineOrderAdmissionPythonReference(t *testing.T) {
	f := loadRouterFixture(t)
	a := &offlineOrderAdmission{maxQty: f.Cap, positions: map[string]float64{}, filled: map[admissionKey]float64{}}
	for _, row := range f.Rows {
		order, ok, err := a.admit(row.Order, row.Cutoff)
		if err != nil {
			t.Fatal(err)
		}
		if ok != (row.Report != nil) {
			t.Fatalf("%s admission=%v", row.Order.ClientOrderID, ok)
		}
		if ok {
			order.ReduceOnly = false
			report, err := SimulateOfflineExecution(order, 100, .001, row.Cutoff)
			if err != nil || !reflect.DeepEqual(report, *row.Report) {
				t.Fatalf("%s got %+v err=%v want %+v", order.ClientOrderID, report, err, row.Report)
			}
			a.record(report, row.Cutoff)
		}
		replayNear(t, a.positions[row.Order.Symbol], row.Position)
		replayNear(t, a.positions[row.Order.Symbol], row.RiskPosition)
	}
}

func TestOfflineAdmissionUsesPartialFills(t *testing.T) {
	a := &offlineOrderAdmission{maxQty: 1, positions: map[string]float64{}, filled: map[admissionKey]float64{}}
	ledger := NewPositionEpisodeLedger()
	session, err := newSimulationSession("", ledger.OnFill)
	if err != nil {
		t.Fatal(err)
	}
	session.simulate = func(order OfflineExecutionOrder, _, _ float64, ts int64) (ExecutionReport, error) {
		return ExecutionReport{ClientOrderID: order.ClientOrderID, Symbol: order.Symbol, Side: order.Side, Status: "PARTIALLY_FILLED", FilledQty: order.Quantity / 2, AvgPrice: 100, Fee: order.Quantity / 20, TransactionTimeNS: ts}, nil
	}
	for i, order := range []OfflineExecutionOrder{{ClientOrderID: "a", Symbol: "BTC", Side: 1, Quantity: 1}, {ClientOrderID: "b", Symbol: "BTC", Side: -1, Quantity: 1, ReduceOnly: true}} {
		admitted, ok, err := a.admit(order, 1)
		if err != nil || !ok {
			t.Fatalf("admit: %+v %v", admitted, err)
		}
		if i == 1 {
			replayNear(t, admitted.Quantity, .5)
		}
		report, err := session.submit(admitted, 100, .001, 1)
		if err != nil {
			t.Fatal(err)
		}
		a.record(report, 1)
	}
	replayNear(t, a.positions["BTC"], .25)
	replayNear(t, ledger.PositionQty("BTC"), .25)
	replayNear(t, a.filled[admissionKey{"BTC", 1}], .75)
}

func TestOfflineReplayRouterReferenceAndFeeConservation(t *testing.T) {
	f := loadRouterFixture(t)
	cfg := replayTestConfig()
	cfg.MaxTradeQtyPerCutoff = f.Cap
	cfg.Candles["5m"] = nil
	for i := int64(0); i < 5; i++ {
		cfg.Candles["5m"] = append(cfg.Candles["5m"], replayTestBar("5m", i*5*replayMinute, 5*replayMinute, 100))
	}
	policy := func(s OfflineReplaySnapshot) ([]OfflineExecutionOrder, error) {
		cutoff := s.Event.CloseTimeNS / (5 * replayMinute)
		var orders []OfflineExecutionOrder
		for _, row := range f.Rows {
			if row.Cutoff == cutoff && row.Order.Symbol == "BTC" {
				orders = append(orders, row.Order)
			}
		}
		return orders, nil
	}
	result, err := RunOfflineReplay(cfg, policy)
	if err != nil {
		t.Fatal(err)
	}
	var expected []ExecutionReport
	var pos float64
	for _, row := range f.Rows {
		if row.Order.Symbol == "BTC" {
			pos = row.Position
			if row.Report != nil {
				r := *row.Report
				r.TransactionTimeNS = row.Cutoff * 5 * replayMinute
				expected = append(expected, r)
			}
		}
	}
	if !reflect.DeepEqual(result.Reports, expected) {
		t.Fatalf("reports got %+v want %+v", result.Reports, expected)
	}
	replayNear(t, result.PositionQty, pos)
	var fees, allocated float64
	for _, r := range result.Reports {
		fees += r.Fee
	}
	for _, e := range result.Episodes {
		allocated += e.Fees
	}
	replayNear(t, fees, allocated)
	again, err := RunOfflineReplay(cfg, policy)
	if err != nil || !reflect.DeepEqual(result, again) {
		t.Fatalf("replay differs: %+v %v", again, err)
	}
}
