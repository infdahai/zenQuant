package research

import (
	"errors"
	"math"
	"reflect"
	"testing"
)

const replayMinute int64 = 60 * 1e9

func replayTestBar(tf string, open int64, duration int64, price float64) ReplayCandle {
	return ReplayCandle{Symbol: "BTC", Timeframe: tf, OpenTimeNS: open, CloseTimeNS: open + duration, Open: price, High: price + 10, Low: price - 10, Close: price, Volume: 1}
}

func replayTestConfig() OfflineReplayConfig {
	return OfflineReplayConfig{Symbol: "BTC", Candles: map[string][]ReplayCandle{"5m": {replayTestBar("5m", 0, 5*replayMinute, 100), replayTestBar("5m", 5*replayMinute, 5*replayMinute, 110)}}, Timeframes: []string{"5m"}, ExecutionTimeframe: "5m", HistoryLimit: 3, IndicatorWindow: 2, TakerFeeRate: .001}
}

func TestOfflineReplayRoundTripAndDeterminism(t *testing.T) {
	cfg := replayTestConfig()
	cfg.Funding = []ReplayFundingRow{{5 * replayMinute, .5}, {6 * replayMinute, .01}, {7 * replayMinute, .02}, {7 * replayMinute, .03}}
	policy := func(s OfflineReplaySnapshot) ([]OfflineExecutionOrder, error) {
		side := 1
		if s.PositionQty > 0 {
			side = -1
		}
		return []OfflineExecutionOrder{{Symbol: "BTC", Side: side, Quantity: 1}}, nil
	}
	a, err := RunOfflineReplay(cfg, policy)
	if err != nil {
		t.Fatal(err)
	}
	b, err := RunOfflineReplay(cfg, policy)
	if err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(a, b) {
		t.Fatal("replay is not deterministic")
	}
	if len(a.Episodes) != 1 || len(a.Reports) != 2 || a.PositionQty != 0 {
		t.Fatalf("unexpected result: %+v", a)
	}
	e := a.Episodes[0]
	replayNear(t, e.GrossRealizedPnL, 10)
	replayNear(t, e.FundingPnL, -3.3)
	replayNear(t, e.Fees, .21)
	replayNear(t, e.NetPnL, 6.49)
	replayNear(t, e.MFEPnL, 20)
	replayNear(t, e.MAEPnL, 0)
}

func TestOfflineReplayCausalityPriorityAndBoundedSnapshots(t *testing.T) {
	cfg := replayTestConfig()
	cfg.Timeframes = []string{"5m", "1h"}
	cfg.HistoryLimit = 2
	cfg.IndicatorWindow = 1
	cfg.Candles["1h"] = []ReplayCandle{replayTestBar("1h", 0, 60*replayMinute, 120)}
	cfg.Candles["5m"] = []ReplayCandle{replayTestBar("5m", 45*replayMinute, 5*replayMinute, 100), replayTestBar("5m", 50*replayMinute, 5*replayMinute, 110), replayTestBar("5m", 55*replayMinute, 5*replayMinute, 120), replayTestBar("5m", 60*replayMinute, 5*replayMinute, 130)}
	end := 60 * replayMinute
	cfg.EndNS = &end
	var seen []OfflineReplaySnapshot
	result, err := RunOfflineReplay(cfg, func(s OfflineReplaySnapshot) ([]OfflineExecutionOrder, error) {
		seen = append(seen, s)
		return nil, nil
	})
	if err != nil {
		t.Fatal(err)
	}
	if len(seen) != 3 || seen[2].Event.EventTimeframe != "5m" || len(seen[1].Histories["1h"]) != 0 || len(seen[2].Histories["1h"]) != 1 {
		t.Fatalf("unexpected ordering: %+v", seen)
	}
	if len(seen[2].Histories["5m"]) != 2 || seen[2].Histories["5m"][1].Close != 120 {
		t.Fatal("history contains future data or is unbounded")
	}
	replayNear(t, seen[2].Indicators["5m"].RateOfChange, 120.0/110-1)
	if len(result.Reports) != 0 {
		t.Fatal(result)
	}
	// A policy cannot mutate the replay's internal history.
	_, err = RunOfflineReplay(cfg, func(s OfflineReplaySnapshot) ([]OfflineExecutionOrder, error) {
		for _, h := range s.Histories {
			if len(h) > 0 {
				h[0].Close = -999
			}
		}
		return nil, nil
	})
	if err != nil {
		t.Fatal(err)
	}
}

func TestOfflineReplaySameCloseEntryAfterAccounting(t *testing.T) {
	cfg := replayTestConfig()
	cfg.Timeframes = []string{"1h", "5m"}
	cfg.Candles["1h"] = []ReplayCandle{replayTestBar("1h", 0, 60*replayMinute, 100)}
	cfg.Candles["5m"] = []ReplayCandle{replayTestBar("5m", 55*replayMinute, 5*replayMinute, 100)}
	cfg.Funding = []ReplayFundingRow{{60 * replayMinute, .1}}
	calls := 0
	result, err := RunOfflineReplay(cfg, func(s OfflineReplaySnapshot) ([]OfflineExecutionOrder, error) {
		calls++
		if s.Event.EventTimeframe != "5m" || len(s.Histories["1h"]) != 1 || s.PositionQty != 0 {
			t.Fatalf("unexpected policy snapshot: %+v", s)
		}
		return []OfflineExecutionOrder{{Symbol: "BTC", Side: 1, Quantity: 1}}, nil
	})
	if err != nil {
		t.Fatal(err)
	}
	if calls != 1 || len(result.Episodes) != 1 {
		t.Fatalf("calls=%d result=%+v", calls, result)
	}
	e := result.Episodes[0]
	replayNear(t, e.FundingPnL, 0)
	replayNear(t, e.MFEPnL, 0)
	replayNear(t, e.MAEPnL, 0)
}

func TestOfflineReplayRequiresExecutionBarsWithinBounds(t *testing.T) {
	for _, filtered := range []bool{false, true} {
		cfg := replayTestConfig()
		cfg.Timeframes = []string{"1h", "5m"}
		cfg.Candles["1h"] = []ReplayCandle{replayTestBar("1h", 0, 60*replayMinute, 100)}
		if filtered {
			start := 60 * replayMinute
			cfg.StartNS = &start
		} else {
			delete(cfg.Candles, "5m")
		}
		calls := 0
		result, err := RunOfflineReplay(cfg, func(OfflineReplaySnapshot) ([]OfflineExecutionOrder, error) { calls++; return nil, nil })
		if err == nil || result != nil || calls != 0 {
			t.Fatalf("filtered=%v result=%+v err=%v calls=%d", filtered, result, err, calls)
		}
	}
}

func TestOfflineReplayRejectsDuplicateExecutionIntervalsBeforePolicy(t *testing.T) {
	for _, closeOffset := range []int64{0, -1, -1_000_000} {
		cfg := replayTestConfig()
		duplicate := cfg.Candles["5m"][1]
		duplicate.CloseTimeNS += closeOffset
		cfg.Candles["5m"] = append(cfg.Candles["5m"], duplicate)
		calls := 0
		result, err := RunOfflineReplay(cfg, func(OfflineReplaySnapshot) ([]OfflineExecutionOrder, error) {
			calls++
			return []OfflineExecutionOrder{{Symbol: "BTC", Side: 1, Quantity: 1}}, nil
		})
		if err == nil || result != nil || calls != 0 {
			t.Fatalf("close offset=%d result=%+v err=%v calls=%d", closeOffset, result, err, calls)
		}
	}
}

func TestOfflineReplayRejectionsAndErrors(t *testing.T) {
	cfg := replayTestConfig()
	r, err := RunOfflineReplay(cfg, func(s OfflineReplaySnapshot) ([]OfflineExecutionOrder, error) {
		return []OfflineExecutionOrder{{Symbol: "BTC", Side: 1, Quantity: 0}}, nil
	})
	if err != nil {
		t.Fatal(err)
	}
	if len(r.Reports) != 2 || r.Reports[0].Status != "REJECTED" || len(r.Episodes) != 0 {
		t.Fatalf("unexpected rejected orders: %+v", r)
	}
	fail := errors.New("policy failed")
	calls := 0
	r, err = RunOfflineReplay(cfg, func(s OfflineReplaySnapshot) ([]OfflineExecutionOrder, error) {
		calls++
		if calls == 2 {
			return nil, fail
		}
		return []OfflineExecutionOrder{{Symbol: "BTC", Side: 1, Quantity: 1}}, nil
	})
	if r != nil || !errors.Is(err, fail) {
		t.Fatalf("partial result or wrong error: %+v %v", r, err)
	}
	tests := []struct {
		name   string
		mutate func(*OfflineReplayConfig)
		policy OfflineReplayPolicy
	}{
		{"empty symbol", func(c *OfflineReplayConfig) { c.Symbol = "" }, nil},
		{"nil policy", func(c *OfflineReplayConfig) {}, nil},
		{"history", func(c *OfflineReplayConfig) { c.HistoryLimit = 2 }, nil},
		{"fee", func(c *OfflineReplayConfig) { c.TakerFeeRate = math.NaN() }, nil},
		{"funding", func(c *OfflineReplayConfig) { c.Funding = []ReplayFundingRow{{2, 0}, {1, 0}} }, nil},
		{"event symbol", func(c *OfflineReplayConfig) { c.Symbol = "ETH" }, nil},
		{"order symbol", func(c *OfflineReplayConfig) {}, func(OfflineReplaySnapshot) ([]OfflineExecutionOrder, error) {
			return []OfflineExecutionOrder{{Symbol: "ETH", Side: 1, Quantity: 1}}, nil
		}},
		{"invalid order", func(c *OfflineReplayConfig) {}, func(OfflineReplaySnapshot) ([]OfflineExecutionOrder, error) {
			return []OfflineExecutionOrder{{Symbol: "BTC", Side: 1, Quantity: 1, ReduceOnly: true}}, nil
		}},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			c := replayTestConfig()
			tt.mutate(&c)
			p := tt.policy
			if p == nil && tt.name != "nil policy" {
				p = func(OfflineReplaySnapshot) ([]OfflineExecutionOrder, error) { return nil, nil }
			}
			got, err := RunOfflineReplay(c, p)
			if err == nil || got != nil {
				t.Fatalf("got %+v, %v", got, err)
			}
		})
	}
}
