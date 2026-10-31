package research

import (
	"encoding/json"
	"math"
	"os"
	"reflect"
	"testing"
)

func TestOfflineExecutionPythonGolden(t *testing.T) {
	data, err := os.ReadFile("testdata/offline_execution_golden.json")
	if err != nil {
		t.Fatal(err)
	}
	var fixture struct {
		Cases []struct {
			Name        string                `json:"name"`
			Order       OfflineExecutionOrder `json:"order"`
			Mark        float64               `json:"mark_price"`
			Rate        float64               `json:"taker_fee_rate"`
			Time        int64                 `json:"timestamp_ns"`
			Report      ExecutionReport       `json:"report"`
			Unsupported bool                  `json:"unsupported"`
		} `json:"cases"`
	}
	if err := json.Unmarshal(data, &fixture); err != nil {
		t.Fatal(err)
	}
	for _, tc := range fixture.Cases {
		t.Run(tc.Name, func(t *testing.T) {
			got, err := SimulateOfflineExecution(tc.Order, tc.Mark, tc.Rate, tc.Time)
			if tc.Unsupported {
				if err == nil {
					t.Fatal("accepted unsupported order semantics")
				}
				return
			}
			if err != nil || !reflect.DeepEqual(got, tc.Report) {
				t.Fatalf("got %+v, err %v; want %+v", got, err, tc.Report)
			}
		})
	}
}

func TestOfflineExecutionValidation(t *testing.T) {
	order := OfflineExecutionOrder{Symbol: "BTC", Side: 1, Quantity: 1}
	for _, value := range []float64{math.NaN(), math.Inf(1), math.Inf(-1)} {
		bad := order
		bad.Quantity = value
		if _, err := SimulateOfflineExecution(bad, 100, .001, 1); err == nil {
			t.Fatal("accepted nonfinite quantity")
		}
		bad = order
		bad.MaxSlippageBPS = value
		if _, err := SimulateOfflineExecution(bad, 100, .001, 1); err == nil {
			t.Fatal("accepted nonfinite slippage")
		}
		if _, err := SimulateOfflineExecution(order, value, .001, 1); err == nil {
			t.Fatal("accepted nonfinite mark")
		}
		if _, err := SimulateOfflineExecution(order, 100, value, 1); err == nil {
			t.Fatal("accepted nonfinite fee rate")
		}
	}
	if _, err := SimulateOfflineExecution(order, 100, -1, 1); err == nil {
		t.Fatal("accepted negative fee rate")
	}
	bad := order
	bad.Side = 0
	if _, err := SimulateOfflineExecution(bad, 100, .001, 1); err == nil {
		t.Fatal("accepted invalid side")
	}
	bad = order
	bad.Quantity = math.MaxFloat64
	if _, err := SimulateOfflineExecution(bad, 2, 0, 1); err == nil {
		t.Fatal("accepted overflowing notional with zero fee")
	}
	if _, err := SimulateOfflineExecution(order, 100, math.MaxFloat64, 1); err == nil {
		t.Fatal("accepted overflowing fee")
	}
	price := 100.0
	for _, unsupported := range []OfflineExecutionOrder{
		{Side: 1, Quantity: 1, ReduceOnly: true},
		{Side: 1, Quantity: 1, OrderType: "LIMIT"},
		{Side: 1, Quantity: 1, OrderType: "MARKET", LimitPrice: &price},
		{Side: 1, Quantity: 1, OrderType: "UNKNOWN"},
	} {
		if _, err := SimulateOfflineExecution(unsupported, 100, .001, 1); err == nil {
			t.Fatalf("accepted unsupported order: %+v", unsupported)
		}
	}
	report, err := SimulateOfflineExecution(order, 100, .001, 0)
	if err != nil || report.TransactionTimeNS != 0 {
		t.Fatalf("zero timestamp: %+v %v", report, err)
	}
}

func TestOfflineExecutionReplayAccounting(t *testing.T) {
	var ledger PositionEpisodeLedger
	accounting, err := NewReplayAccounting("BTC", &ledger, nil)
	if err != nil {
		t.Fatal(err)
	}
	for _, tc := range []struct {
		time       int64
		side       int
		qty, price float64
	}{
		{1, 1, 0, 100}, {2, 1, 2, 100}, {3, -1, 2, 110},
	} {
		if err := accounting.OnExecutionBar(ReplayExecutionBar{tc.time, tc.price, tc.price, tc.price}); err != nil {
			t.Fatal(err)
		}
		report, err := SimulateOfflineExecution(OfflineExecutionOrder{Symbol: "BTC", Side: tc.side, Quantity: tc.qty}, tc.price, .001, tc.time)
		if err != nil {
			t.Fatal(err)
		}
		if _, err := accounting.CollectReport(report); err != nil {
			t.Fatal(err)
		}
		if tc.qty == 0 && len(ledger.Episodes()) != 0 {
			t.Fatal("rejection created an episode")
		}
	}
	episodes := ledger.ClosedEpisodes()
	if len(episodes) != 1 {
		t.Fatalf("episodes: %+v", episodes)
	}
	replayNear(t, episodes[0].GrossRealizedPnL, 20)
	replayNear(t, episodes[0].Fees, .42)
}
