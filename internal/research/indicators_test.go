package research

import (
	"encoding/json"
	"math"
	"os"
	"testing"
)

func TestIndicatorsPythonGolden(t *testing.T) {
	data, err := os.ReadFile("testdata/indicators_golden.json")
	if err != nil {
		t.Fatal(err)
	}
	var cases []struct {
		Name          string            `json:"name"`
		Candles       []IndicatorCandle `json:"candles"`
		Window        int               `json:"window"`
		Timeframe     string            `json:"timeframe"`
		ATR, Vol, ROC float64
	}
	if err := json.Unmarshal(data, &cases); err != nil {
		t.Fatal(err)
	}
	if len(cases) != 100 {
		t.Fatalf("expected 100 cases, got %d", len(cases))
	}
	for _, tc := range cases {
		values := []struct {
			name string
			want float64
			calc func() (float64, error)
		}{
			{"atr", tc.ATR, func() (float64, error) { return OfflineATR(tc.Candles, tc.Window) }},
			{"vol", tc.Vol, func() (float64, error) { return OfflineRealizedVol(tc.Candles, tc.Window, tc.Timeframe) }},
			{"roc", tc.ROC, func() (float64, error) { return OfflineRateOfChange(tc.Candles, tc.Window) }},
		}
		for _, v := range values {
			got, err := v.calc()
			// Absolute and relative tolerance allow differences in libm implementations.
			if err != nil || math.IsNaN(got) || math.IsInf(got, 0) || math.Abs(got-v.want) > 1e-12+1e-12*math.Abs(v.want) {
				t.Errorf("%s window=%d timeframe=%s %s: got %g (%v), want %g", tc.Name, tc.Window, tc.Timeframe, v.name, got, err, v.want)
			}
		}
	}
}

func TestIndicatorsRejectInvalidInputAndOverflow(t *testing.T) {
	fns := []func([]IndicatorCandle, int) (float64, error){OfflineATR, func(c []IndicatorCandle, w int) (float64, error) { return OfflineRealizedVol(c, w, "4h") }, OfflineRateOfChange}
	for _, fn := range fns {
		for _, w := range []int{0, -1, MaxIndicatorWindow + 1, int(^uint(0) >> 1)} {
			if _, err := fn(nil, w); err == nil {
				t.Errorf("accepted window %d", w)
			}
		}
		for _, c := range []IndicatorCandle{{High: math.NaN()}, {Low: math.Inf(1)}, {Close: math.Inf(-1)}} {
			if _, err := fn([]IndicatorCandle{c}, 1); err == nil {
				t.Errorf("accepted nonfinite %+v", c)
			}
		}
		if _, err := fn(make([]IndicatorCandle, MaxIndicatorWindow+2), 1); err == nil {
			t.Error("accepted oversized input")
		}
	}
	m := math.MaxFloat64
	if _, err := OfflineATR([]IndicatorCandle{{Close: -m}, {High: m}}, 1); err == nil {
		t.Error("accepted true range overflow")
	}
	if _, err := OfflineATR([]IndicatorCandle{{}, {High: m}, {High: m}}, 2); err == nil {
		t.Error("accepted true range sum overflow")
	}
	for _, fn := range fns[1:] {
		if _, err := fn([]IndicatorCandle{{Close: math.SmallestNonzeroFloat64}, {Close: m}}, 1); err == nil {
			t.Error("accepted ratio overflow")
		}
	}
	if _, err := OfflineRealizedVol([]IndicatorCandle{{Close: m}, {Close: math.SmallestNonzeroFloat64}}, 1, "1h"); err == nil {
		t.Error("accepted ratio underflow")
	}
}
