package research

import (
	"encoding/json"
	"math"
	"os"
	"reflect"
	"testing"
)

func TestTradeProxyPythonSynthetic(t *testing.T) {
	data, err := os.ReadFile("testdata/trade_proxy_golden.json")
	if err != nil {
		t.Fatal(err)
	}
	var fixture struct {
		Rows    []RecentTrade      `json:"rows"`
		Trades  []ProxyTrade       `json:"trades"`
		Buckets []TradeProxyBucket `json:"buckets"`
	}
	if err := json.Unmarshal(data, &fixture); err != nil {
		t.Fatal(err)
	}
	for i := range fixture.Trades {
		fixture.Trades[i].Source = "hyperliquid_public"
	}
	trades, err := NormalizeRecentTrades("btc", fixture.Rows)
	if err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(trades, fixture.Trades) {
		t.Fatalf("trades = %+v, want %+v", trades, fixture.Trades)
	}
	buckets, err := AggregateTradeProxy(trades, 50000)
	if err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(buckets, fixture.Buckets) {
		t.Fatalf("buckets = %+v, want %+v", buckets, fixture.Buckets)
	}
	if len(buckets) != 2 || buckets[1].LongCount != 2 || buckets[1].ShortCount != 1 {
		t.Fatalf("boundary or duplicate counts: %+v", buckets)
	}
}

func TestRecentTradeJSONRequiredFields(t *testing.T) {
	fields := map[string]any{"side": "B", "px": "10", "sz": "2", "time": 1, "tid": 0}
	for key := range fields {
		for _, missing := range []bool{true, false} {
			input := make(map[string]any)
			for k, v := range fields {
				input[k] = v
			}
			if missing {
				delete(input, key)
			} else {
				input[key] = nil
			}
			data, err := json.Marshal(input)
			if err != nil {
				t.Fatal(err)
			}
			var row RecentTrade
			if err := json.Unmarshal(data, &row); err == nil {
				t.Errorf("accepted missing=%v %s", missing, key)
			}
		}
	}
	var row RecentTrade
	if err := json.Unmarshal([]byte(`{"side":"B","px":"10","sz":"2","time":1,"tid":0,"hash":null}`), &row); err != nil {
		t.Fatal(err)
	}
	trades, err := NormalizeRecentTrades("btc", []RecentTrade{row})
	if err != nil || len(trades) != 1 || trades[0].TradeID != 0 {
		t.Fatalf("zero tid: %+v, %v", trades, err)
	}
	if err := json.Unmarshal([]byte(`{"side":"B","px":"10","sz":"2","time":1,"tid":"0"}`), &row); err == nil {
		t.Fatal("accepted wrong tid type")
	}
}

func TestTradeProxyRejections(t *testing.T) {
	valid := RecentTrade{Side: "B", Price: "10", Size: "2", TimeMS: 1, TradeID: 1}
	tests := []struct {
		name   string
		change func(*RecentTrade)
	}{
		{"side", func(r *RecentTrade) { r.Side = "x" }}, {"empty price", func(r *RecentTrade) { r.Price = "" }},
		{"nan", func(r *RecentTrade) { r.Price = "NaN" }}, {"infinite", func(r *RecentTrade) { r.Size = "Inf" }},
		{"zero", func(r *RecentTrade) { r.Size = "0" }}, {"overflow product", func(r *RecentTrade) { r.Price = "1e308"; r.Size = "10" }},
		{"timestamp overflow", func(r *RecentTrade) { r.TimeMS = math.MaxInt64/1_000_000 + 1 }}, {"negative timestamp", func(r *RecentTrade) { r.TimeMS = -1 }},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			r := valid
			tt.change(&r)
			if got, err := NormalizeRecentTrades("BTC", []RecentTrade{r}); err == nil || got != nil {
				t.Fatalf("got %v, %v", got, err)
			}
		})
	}
	trades, _ := NormalizeRecentTrades("btc", []RecentTrade{valid})
	if _, err := AggregateTradeProxy(trades, math.NaN()); err == nil {
		t.Fatal("accepted NaN threshold")
	}
	bad := trades[0]
	bad.Source = "other"
	if _, err := AggregateTradeProxy([]ProxyTrade{trades[0], bad}, 50000); err == nil {
		t.Fatal("accepted mixed sources below threshold")
	}
	bad = trades[0]
	bad.NotionalUSD = 50000
	if _, err := AggregateTradeProxy([]ProxyTrade{bad}, 0); err == nil {
		t.Fatal("accepted inconsistent notional")
	}
	bad = trades[0]
	bad.Side = "BAD"
	if _, err := AggregateTradeProxy([]ProxyTrade{bad}, 50000); err == nil {
		t.Fatal("accepted invalid side below threshold")
	}
	bad = trades[0]
	bad.TimeMS = math.MaxInt64 / 1_000_000
	if _, err := AggregateTradeProxy([]ProxyTrade{bad}, 0); err == nil {
		t.Fatal("accepted bucket end overflow")
	}
}

func TestTradeProxySortingAndAmounts(t *testing.T) {
	trades := []ProxyTrade{
		{Symbol: "z", Side: "SELL", Price: 10, Size: 1, TimeMS: 14400000, NotionalUSD: 10, Source: "s"},
		{Symbol: "a", Side: "BUY", Price: 30, Size: 1, TimeMS: 14400000, NotionalUSD: 30, Source: "s"},
		{Symbol: "a", Side: "SELL", Price: 10, Size: 1, TimeMS: 14400000, NotionalUSD: 10, Source: "s"},
		{Symbol: "a", Side: "BUY", Price: 20, Size: 1, TimeMS: 1, NotionalUSD: 20, Source: "s"},
	}
	got, err := AggregateTradeProxy(trades, 0)
	if err != nil {
		t.Fatal(err)
	}
	if len(got) != 3 || got[0].Symbol != "A" || got[0].StartNS != 0 || got[1].Symbol != "A" || got[2].Symbol != "Z" || got[1].TotalUSD != 40 || got[1].MaxUSD != 30 || got[1].Imbalance != -0.5 {
		t.Fatalf("unexpected buckets: %+v", got)
	}
}
