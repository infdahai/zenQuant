package research

import (
	"math"
	"reflect"
	"testing"
)

func observationCandles(n int) []ReplayCandle {
	const step int64 = 4 * 3600 * 1e9
	rows := make([]ReplayCandle, n)
	for i := range rows {
		p := 100 + float64(i)*.3 + math.Sin(float64(i))
		rows[i] = ReplayCandle{Symbol: "BTC", Timeframe: "4h", OpenTimeNS: int64(i) * step, CloseTimeNS: int64(i+1)*step - 1_000_000, Open: p, High: p + 2, Low: p - 2, Close: p + .2, Volume: 1}
	}
	return rows
}

func TestCandleObservationsWarmupAndPrefix(t *testing.T) {
	rows := observationCandles(50)
	original := append([]ReplayCandle(nil), rows...)
	got, err := CandleObservations(rows, "fixture")
	if err != nil {
		t.Fatal(err)
	}
	for i, o := range got {
		if o.RateOfChangeReady != (i >= 12) || o.ATRReady != (i >= 14) || o.RealizedVolReady != (i >= 20) || o.OUKappaReady != (i >= 20) {
			t.Fatalf("warmup at %d: %+v", i, o)
		}
		if o.CutoffTimeNS != rows[i].OpenTimeNS+4*3600*1e9 || o.HistoricalArrivalTimeNS != nil || o.Source != "fixture" || o.Symbol != "BTC" || o.Timeframe != "4h" {
			t.Fatalf("provenance at %d: %+v", i, o)
		}
		prefix, err := CandleObservations(rows[:i+1], "fixture")
		if err != nil || !reflect.DeepEqual(prefix, got[:i+1]) {
			t.Fatalf("prefix %d: %v", i, err)
		}
	}
	if !reflect.DeepEqual(rows, original) {
		t.Fatal("input mutated")
	}
	// Python indicator.engine helpers on prefixes of this fixture, with defaults.
	for _, tc := range []struct {
		n                 int
		atr, vol, roc, ou float64
	}{
		{1, 0, 0, 0, 0}, {2, 4, 0, 0, 0},
		{3, 4, 0.25494870532976543, 0, 0},
		{12, 4, 0.32392090889883135, 0, 0},
		{13, 4, 0.31721263055679355, 0.030573124570853816, 0},
		{14, 4, 0.3282110795065774, 0.03136619215341052, 0},
		{15, 4, 0.32158952270413627, 0.036194428847743154, 0},
		{20, 4, 0.3177777376434114, 0.03004060931774255, 0},
		{21, 4, 0.31782829840427457, 0.03401495157171808, 0.03366561975042852},
		{22, 4, 0.3055175362807832, 0.038955131424095235, 0},
		{50, 4, 0.284431644459397, 0.029729718672572458, 0.11536605628776363},
	} {
		o := got[tc.n-1]
		for _, pair := range [][2]float64{{o.ATR, tc.atr}, {o.RealizedVol, tc.vol}, {o.RateOfChange, tc.roc}, {o.OUKappa, tc.ou}} {
			if math.Abs(pair[0]-pair[1]) > 1e-12 {
				t.Fatalf("Python differential at %d: got %.17g want %.17g", tc.n, pair[0], pair[1])
			}
		}
	}
}

func TestCandleObservationsRejectInvalidSeries(t *testing.T) {
	for _, tc := range []struct {
		name   string
		change func([]ReplayCandle)
	}{
		{"gap", func(c []ReplayCandle) { c[1].OpenTimeNS += 4 * 3600 * 1e9; c[1].CloseTimeNS += 4 * 3600 * 1e9 }},
		{"duplicate", func(c []ReplayCandle) { c[1] = c[0] }},
		{"order", func(c []ReplayCandle) { c[0], c[1] = c[1], c[0] }},
		{"mixed symbol", func(c []ReplayCandle) { c[1].Symbol = "ETH" }},
		{"invalid ohlc", func(c []ReplayCandle) { c[1].Close = 0 }},
		{"unfinished interval", func(c []ReplayCandle) { c[1].CloseTimeNS-- }},
	} {
		t.Run(tc.name, func(t *testing.T) {
			rows := observationCandles(3)
			tc.change(rows)
			if _, err := CandleObservations(rows, "fixture"); err == nil {
				t.Fatal("accepted invalid input")
			}
		})
	}
	if _, err := CandleObservations(nil, ""); err == nil {
		t.Fatal("accepted empty source")
	}
}
