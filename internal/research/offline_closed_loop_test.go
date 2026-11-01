package research

import (
	"fmt"
	"math"
	"reflect"
	"strings"
	"testing"
)

func closedLoopFixture() OfflineClosedLoopConfig {
	const bar int64 = 4 * 60 * 60 * 1e9
	c := OfflineClosedLoopConfig{Replay: OfflineReplayConfig{Symbol: "BTC", Timeframes: []string{"4h"}, ExecutionTimeframe: "4h", Candles: map[string][]ReplayCandle{}, HistoryLimit: 2, IndicatorWindow: 1, TakerFeeRate: .001, MaxTradeQtyPerCutoff: 1000}, StrategyConfig: DefaultStrategyFlowConfig(), RiskConfig: DefaultStatefulRiskConfig()}
	c.RiskConfig.MaxLeverage = .1
	for i := int64(0); i < 3; i++ {
		candle := replayTestBar("4h", i*bar, bar, 100)
		c.Replay.Candles["4h"] = append(c.Replay.Candles["4h"], candle)
		ts := candle.CloseTimeNS
		p := StrategyIndicator{Symbol: "BTC", Timeframe: "4h", CutoffTimeNS: ts, Price: 100, ATR: 1, RealizedVol: .8, CVDSlope: .1}
		s := StrategyState{PlaybookState: PlaybookState{Symbol: "BTC", Timeframe: "4h", CutoffTimeNS: ts, DirectionalState: "UPTREND", DirectionalConfidence: .8, VolatilityState: "NORMAL", VolatilityConfidence: .8, PositioningState: "HEALTHY", PositioningConfidence: .8, QualityState: "TRADABLE", QualityConfidence: .8, DirectionSignal: 1}, Regime: "TREND", RegimeConfidence: .8, QualityFlags: []string{"OK"}}
		c.Inputs = append(c.Inputs, OfflineDecisionInput{Indicator: p, State: s, AvailableTimeNS: ts, ValidUntilNS: ts})
	}
	return c
}

func TestClosedLoopRiskFeesDeterminism(t *testing.T) {
	c := closedLoopFixture()
	a, err := RunOfflineClosedLoop(c)
	if err != nil {
		t.Fatal(err)
	}
	b, err := RunOfflineClosedLoop(c)
	if err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(a, b) {
		t.Fatal("replay is not deterministic")
	}
	if len(a.Replay.Reports) == 0 || len(a.Decisions) != 3 {
		t.Fatalf("result: %+v", a)
	}
	d := a.Decisions[0]
	if d.Risk == nil || d.Risk.Revised.Quantity <= 0 || d.Risk.Revised.Quantity >= d.Evidence.Target.TargetQty || math.Abs(a.Replay.Reports[0].FilledQty-d.Risk.Revised.Quantity) > 1e-6 {
		t.Fatalf("risk did not reduce target: %+v", d)
	}
	r := a.Replay.Reports[0]
	fees := 0.0
	for _, report := range a.Replay.Reports {
		fees += report.Fee
	}
	if r.TransactionTimeNS != c.Inputs[0].Indicator.CutoffTimeNS || math.Abs(r.Fee-r.FilledQty*100*.001) > 1e-9 || math.Abs(a.RiskState.Equity-(100000-fees)) > 1e-6 {
		t.Fatalf("timing or fee: report=%+v risk=%+v", r, a.RiskState)
	}
}

func TestClosedLoopLeverageUsesCurrentRiskEquity(t *testing.T) {
	c := closedLoopFixture()
	c.StrategyConfig.Equity = 50000
	c.RiskConfig.MaxLeverage = 6
	result, err := RunOfflineClosedLoop(c)
	if err != nil {
		t.Fatal(err)
	}
	for i, d := range result.Decisions {
		equity := c.RiskConfig.StartingEquity
		for _, report := range result.Replay.Reports {
			if report.TransactionTimeNS < c.Inputs[i].Indicator.CutoffTimeNS {
				equity -= report.Fee
			}
		}
		want := math.Abs(d.Risk.Original.Quantity) * c.Inputs[i].Indicator.Price / equity
		if math.Abs(d.Risk.Original.Leverage-want) > 1e-12 {
			t.Fatalf("bar %d: risk leverage %g, want %g", i, d.Risk.Original.Leverage, want)
		}
		if math.Abs(d.Risk.Revised.Leverage-math.Abs(d.Risk.Revised.Quantity)*100/equity) > .000101 {
			t.Fatalf("bar %d: inconsistent approved target %+v", i, d.Risk.Revised)
		}
	}
	if result.Decisions[0].Evidence.Target.TargetLeverage == result.Decisions[0].Risk.Original.Leverage {
		t.Fatal("strategy and risk leverage unexpectedly match")
	}
}

func TestClosedLoopPartialAndRejected(t *testing.T) {
	for _, status := range []string{"PARTIALLY_FILLED", "REJECTED"} {
		t.Run(status, func(t *testing.T) {
			c := closedLoopFixture()
			calls := 0
			simulate := func(o OfflineExecutionOrder, mark, rate float64, ts int64) (ExecutionReport, error) {
				calls++
				if calls == 1 {
					qty := o.Quantity / 2
					if status == "REJECTED" {
						qty = 0
					}
					return ExecutionReport{ClientOrderID: o.ClientOrderID, Symbol: o.Symbol, Side: o.Side, Status: status, FilledQty: qty, AvgPrice: mark, Fee: qty * mark * rate, TransactionTimeNS: ts}, nil
				}
				o.ReduceOnly = false
				return SimulateOfflineExecution(o, mark, rate, ts)
			}
			result, err := runOfflineClosedLoop(c, simulate)
			if err != nil {
				t.Fatal(err)
			}
			if len(result.Replay.Reports) < 2 {
				t.Fatalf("missing retry: %+v", result.Replay)
			}
			first, second := result.Replay.Reports[0], result.Replay.Reports[1]
			if first.Status != status || second.TransactionTimeNS <= first.TransactionTimeNS || result.RiskState.Positions["BTC"] != result.Replay.PositionQty {
				t.Fatalf("reports=%+v state=%+v", result.Replay.Reports, result.RiskState)
			}
			wantDelta := result.Decisions[1].Risk.Revised.Quantity - first.FilledQty
			if second.Side != 1 || math.Abs(second.FilledQty-wantDelta) > 1e-6 {
				t.Fatalf("retry differs from approved target minus actual fill: want %g report %+v", wantDelta, second)
			}
			if status == "REJECTED" && first.FilledQty != 0 {
				t.Fatal(first)
			}
		})
	}
}

func TestClosedLoopFlipAndFailure(t *testing.T) {
	for _, mode := range []string{"filled", "rejected", "partial"} {
		c := closedLoopFixture()
		for i := 1; i < len(c.Inputs); i++ {
			c.Inputs[i].Indicator.CVDSlope = -.1
		}
		var orders []OfflineExecutionOrder
		simulate := func(o OfflineExecutionOrder, mark, rate float64, ts int64) (ExecutionReport, error) {
			orders = append(orders, o)
			if mode != "filled" && strings.HasSuffix(o.ClientOrderID, "-close") {
				qty, status := 0.0, "REJECTED"
				if mode == "partial" {
					qty, status = o.Quantity/2, "PARTIALLY_FILLED"
				}
				return ExecutionReport{ClientOrderID: o.ClientOrderID, Symbol: o.Symbol, Side: o.Side, Status: status, FilledQty: qty, AvgPrice: mark, Fee: qty * mark * rate, TransactionTimeNS: ts}, nil
			}
			o.ReduceOnly = false
			return SimulateOfflineExecution(o, mark, rate, ts)
		}
		result, err := runOfflineClosedLoop(c, simulate)
		if err != nil {
			t.Fatal(err)
		}
		if len(orders) < 2 || !strings.HasSuffix(orders[1].ClientOrderID, "-close") || !orders[1].ReduceOnly || orders[1].Side != -1 || orders[1].Quantity != result.Replay.Reports[0].FilledQty {
			t.Fatalf("incorrect reversal close: mode=%s orders=%+v", mode, orders)
		}
		for _, r := range result.Replay.Reports[1:] {
			if r.TransactionTimeNS == result.Replay.Reports[1].TransactionTimeNS && r.ClientOrderID != result.Replay.Reports[1].ClientOrderID {
				t.Fatal("opened on closing bar")
			}
		}
		if mode == "filled" && (len(result.Replay.Reports) < 3 || result.Replay.Reports[2].Side != -1 || result.Replay.Reports[2].TransactionTimeNS <= result.Replay.Reports[1].TransactionTimeNS) {
			t.Fatalf("missing later approved opening: %+v", result.Replay.Reports)
		}
		if mode != "filled" && result.Replay.PositionQty < 0 {
			t.Fatalf("opened before flat: %+v", result.Replay.Reports)
		}
	}
	c := closedLoopFixture()
	calls := 0
	_, err := runOfflineClosedLoop(c, func(o OfflineExecutionOrder, mark, rate float64, ts int64) (ExecutionReport, error) {
		calls++
		return ExecutionReport{ClientOrderID: o.ClientOrderID, Symbol: o.Symbol, Side: o.Side, Status: "FILLED", FilledQty: o.Quantity, AvgPrice: mark, Fee: 1, RejectReason: "invalid", TransactionTimeNS: ts}, nil
	})
	if err == nil || calls != 1 {
		t.Fatalf("invalid report did not stop loop: err=%v calls=%d", err, calls)
	}
}

func TestClosedLoopRiskRunsDuringConfirmation(t *testing.T) {
	c := closedLoopFixture()
	c.StrategyConfig.FlipConfirmationBars = 2
	c.Inputs[1].Indicator.CVDSlope = -.1
	c.Inputs[2].Indicator.CVDSlope = -.1
	result, err := RunOfflineClosedLoop(c)
	if err != nil {
		t.Fatal(err)
	}
	d := result.Decisions[1]
	if d.Evidence.Target != nil || d.Risk == nil || d.Risk.Original.Quantity != result.Replay.Reports[0].FilledQty {
		t.Fatalf("confirmation skipped risk: %+v", d)
	}
}

func TestClosedLoopPriceMoveAccounting(t *testing.T) {
	c := closedLoopFixture()
	c.Replay.Candles["4h"] = c.Replay.Candles["4h"][:2]
	c.Inputs = c.Inputs[:2]
	c.Replay.Candles["4h"][1] = replayTestBar("4h", 4*60*60*1e9, 4*60*60*1e9, 110)
	c.Inputs[1].Indicator.Price = 110
	c.Inputs[1].State.Regime = "STRESS"
	result, err := RunOfflineClosedLoop(c)
	if err != nil {
		t.Fatal(err)
	}
	if len(result.Replay.Episodes) != 1 || !result.Replay.Episodes[0].Closed {
		t.Fatalf("episode: %+v", result.Replay.Episodes)
	}
	e := result.Replay.Episodes[0]
	wantGross := result.Replay.Reports[0].FilledQty * 10
	if math.Abs(e.GrossRealizedPnL-wantGross) > 1e-6 || math.Abs(result.RiskState.Equity-(100000+e.NetPnL)) > 1e-6 {
		t.Fatalf("episode=%+v risk=%+v", e, result.RiskState)
	}
}

func TestClosedLoopFundingSettlesBeforeDecisionAndFill(t *testing.T) {
	const bar int64 = 4 * 60 * 60 * 1e9
	for _, direction := range []float64{1, -1} {
		t.Run(fmt.Sprintf("direction_%g", direction), func(t *testing.T) {
			c := closedLoopFixture()
			c.Replay.TakerFeeRate = 0
			for i := range c.Inputs {
				c.Inputs[i].Indicator.CVDSlope *= direction
			}
			c.Replay.Candles["4h"][1] = replayTestBar("4h", bar, bar, 110)
			c.Inputs[1].Indicator.Price = 110
			c.Replay.Candles["4h"][2] = replayTestBar("4h", 2*bar, bar, 110)
			c.Inputs[2].Indicator.Price = 110
			c.Inputs[1].State.Regime = "STRESS" // Close after the settlements.
			c.Inputs[2].State.Regime = "STRESS"
			c.Replay.Funding = []ReplayFundingRow{{bar, .5}, {bar + 1, -.02}, {bar + 2, .01}, {2 * bar, -.03}, {3*bar + 1, .9}}
			a, err := RunOfflineClosedLoop(c)
			if err != nil {
				t.Fatal(err)
			}
			b, err := RunOfflineClosedLoop(c)
			if err != nil {
				t.Fatal(err)
			}
			if !reflect.DeepEqual(a, b) {
				t.Fatal("funding replay is not deterministic")
			}
			if len(a.Replay.Episodes) != 1 || !a.Replay.Episodes[0].Closed {
				t.Fatalf("episodes: %+v", a.Replay.Episodes)
			}
			qty := float64(a.Replay.Reports[0].Side) * a.Replay.Reports[0].FilledQty
			if qty*direction <= 0 {
				t.Fatalf("unexpected position %g", qty)
			}
			funding := -qty*100*(-.02) - qty*100*.01 - qty*110*(-.03)
			e := a.Replay.Episodes[0]
			if math.Abs(e.FundingPnL-funding) > 1e-8 || math.Abs(a.RiskState.Equity-(c.RiskConfig.StartingEquity+e.NetPnL)) > 1e-7 {
				t.Fatalf("funding=%g episode=%+v risk=%+v", funding, e, a.RiskState)
			}
			wantEquity := c.RiskConfig.StartingEquity + qty*10 + funding
			if math.Abs(a.RiskState.EquityHistory[1]-wantEquity) > 1e-7 {
				t.Fatalf("risk decision missed funding: %+v want %g", a.RiskState.EquityHistory, wantEquity)
			}
			// Profitable intermediate settlements establish a high water mark even
			// if later settlements reverse them before the next decision.
			hwm := c.RiskConfig.StartingEquity
			cash := c.RiskConfig.StartingEquity
			for _, pnl := range []float64{-qty * 100 * (-.02), -qty * 100 * .01} {
				cash += pnl
				hwm = math.Max(hwm, cash)
			}
			cash += qty*10 - qty*110*(-.03)
			hwm = math.Max(hwm, cash)
			if math.Abs(a.RiskState.HighWaterMark-hwm) > 1e-7 {
				t.Fatalf("high water %g want %g", a.RiskState.HighWaterMark, hwm)
			}
		})
	}
}

func TestReplayFundingRiskFailureIsAtomic(t *testing.T) {
	c := DefaultStatefulRiskConfig()
	c.StartingEquity = math.MaxFloat64
	r, err := NewStatefulRiskOverlay(c)
	if err != nil {
		t.Fatal(err)
	}
	before := r.State()
	for _, settlement := range []replayFundingSettlement{{quantity: 1, price: 100, pnl: 1}, {pnl: math.MaxFloat64}} {
		if err := r.applyReplayFunding("BTC", settlement, 1); err == nil {
			t.Fatal("expected funding error")
		}
		if !reflect.DeepEqual(before, r.State()) {
			t.Fatal("funding failure mutated risk state")
		}
	}
}

func TestClosedLoopRejectsInvalidInputs(t *testing.T) {
	for _, tc := range []struct {
		name   string
		mutate func(*OfflineClosedLoopConfig)
	}{
		{"future", func(c *OfflineClosedLoopConfig) { c.Inputs[0].Indicator.CutoffTimeNS++ }},
		{"early availability", func(c *OfflineClosedLoopConfig) { c.Inputs[0].AvailableTimeNS-- }},
		{"late availability", func(c *OfflineClosedLoopConfig) { c.Inputs[0].AvailableTimeNS++ }},
		{"expired", func(c *OfflineClosedLoopConfig) { c.Inputs[0].ValidUntilNS-- }},
		{"missing", func(c *OfflineClosedLoopConfig) { c.Inputs = c.Inputs[1:] }},
		{"price", func(c *OfflineClosedLoopConfig) { c.Inputs[0].Indicator.Price++ }},
		{"daily", func(c *OfflineClosedLoopConfig) { c.Replay.Timeframes = append(c.Replay.Timeframes, "1d") }},
	} {
		t.Run(tc.name, func(t *testing.T) {
			c := closedLoopFixture()
			tc.mutate(&c)
			if _, err := RunOfflineClosedLoop(c); err == nil {
				t.Fatal("expected error")
			}
		})
	}
	c := closedLoopFixture()
	c.Inputs[0].Indicator.RealizedVol = math.NaN()
	_, err := RunOfflineClosedLoop(c)
	if err == nil || !strings.Contains(err.Error(), "indicator") {
		t.Fatal(err)
	}
}
