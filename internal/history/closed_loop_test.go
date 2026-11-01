package history

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"math"
	"os"
	"reflect"
	"strings"
	"testing"
	"time"

	"github.com/infdahai/zenQuant/internal/research"
	"github.com/infdahai/zenQuant/internal/workbench"
)

func TestImportedFundingClosedLoopAccounting(t *testing.T) {
	f, err := os.Open("testdata/klines.csv")
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	const startMS int64 = 1704081600000
	const endMS int64 = 1704096000000
	funding := []byte(fmt.Sprintf(`[
		{"symbol":"BTCUSDT","fundingTime":%d,"fundingRate":"0.5"},
		{"symbol":"BTCUSDT","fundingTime":%d,"fundingRate":"0.5"},
		{"symbol":"BTCUSDT","fundingTime":%d,"fundingRate":"0.01"},
		{"symbol":"BTCUSDT","fundingTime":%d,"fundingRate":"-0.02"},
		{"symbol":"BTCUSDT","fundingTime":%d,"fundingRate":"0.03"},
		{"symbol":"BTCUSDT","fundingTime":%d,"fundingRate":"0.5"}
	]`, startMS-1, startMS, startMS+1, startMS+2, endMS, endMS+1))
	csv, err := io.ReadAll(f)
	if err != nil {
		t.Fatal(err)
	}
	lines := bytes.SplitAfter(csv, []byte("\n"))
	raw, err := NormalizePartsWithFunding([]io.Reader{bytes.NewReader(lines[0]), bytes.NewReader(bytes.Join(lines[1:], nil))}, "4h", time.Date(2024, 1, 2, 0, 0, 0, 0, time.UTC), funding)
	if err != nil {
		t.Fatal(err)
	}
	store, err := workbench.Open(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	snap, err := store.Import(raw)
	if err != nil {
		t.Fatal(err)
	}
	inputs := make([]research.OfflineDecisionInput, 0, 2)
	for i, c := range snap.Dataset.Candles {
		state := research.StrategyState{PlaybookState: research.PlaybookState{Symbol: "BTC", Timeframe: "4h", CutoffTimeNS: c.CloseTimeNS, DirectionalState: "UPTREND", DirectionalConfidence: .8, VolatilityState: "NORMAL", VolatilityConfidence: .8, PositioningState: "HEALTHY", PositioningConfidence: .8, QualityState: "TRADABLE", QualityConfidence: .8, DirectionSignal: 1}, Regime: "TREND", RegimeConfidence: .8, QualityFlags: []string{"OK"}}
		if i == 1 {
			state.Regime = "STRESS"
		}
		inputs = append(inputs, research.OfflineDecisionInput{Indicator: research.StrategyIndicator{Symbol: "BTC", Timeframe: "4h", CutoffTimeNS: c.CloseTimeNS, Price: c.Close, ATR: 1, RealizedVol: .8, CVDSlope: .1}, State: state, AvailableTimeNS: c.CloseTimeNS, ValidUntilNS: c.CloseTimeNS})
	}
	payload, err := json.Marshal(inputs)
	if err != nil {
		t.Fatal(err)
	}
	config := research.OfflineClosedLoopConfig{Replay: research.OfflineReplayConfig{HistoryLimit: 2, IndicatorWindow: 1, TakerFeeRate: .001, MaxTradeQtyPerCutoff: 1000}, StrategyConfig: research.DefaultStrategyFlowConfig(), RiskConfig: research.DefaultStatefulRiskConfig()}
	config.RiskConfig.MaxLeverage = .1
	source := DecisionSource{Description: "caller indicator fixture", Availability: "caller asserts availability at each close", QuoteAssumption: USDQuoteAssumption}
	result, err := RunImportedClosedLoop(store, snap.ID, startMS*1e6, endMS*1e6, payload, source, config)
	if err != nil {
		t.Fatal(err)
	}
	if len(result.Provenance.SourcePartsSHA256) != 2 || !reflect.DeepEqual(result.Provenance.SourcePartsSHA256, snap.Dataset.Provenance.SourcePartsSHA256) || result.Provenance.FundingSourceSHA256 == "" || !strings.Contains(result.FundingAssumption, "coverage is unknown") || result.Source.Availability != source.Availability {
		t.Fatalf("missing provenance or limitations: %+v", result)
	}
	r := result.Result
	if len(r.Replay.Reports) != 2 || len(r.Replay.Episodes) != 1 || !r.Replay.Episodes[0].Closed {
		t.Fatalf("expected opening and closing fills: %+v", r.Replay)
	}
	open, close := r.Replay.Reports[0], r.Replay.Reports[1]
	qty := float64(open.Side) * open.FilledQty
	if qty <= 0 || math.Abs(close.FilledQty-open.FilledQty) > 1e-8 || r.RiskState.Positions["BTC"] != 0 {
		t.Fatalf("unexpected actual position: open=%+v close=%+v risk=%+v", open, close, r.RiskState)
	}
	firstPrice, lastPrice := snap.Dataset.Candles[0].Close, snap.Dataset.Candles[1].Close
	wantFunding := -qty*firstPrice*.01 + qty*firstPrice*.02 - qty*lastPrice*.03
	wantFees := open.FilledQty*firstPrice*.001 + close.FilledQty*lastPrice*.001
	wantGross := qty * (lastPrice - firstPrice)
	episode := r.Replay.Episodes[0]
	near := func(name string, got, want float64) {
		t.Helper()
		if math.Abs(got-want) > 1e-7 {
			t.Errorf("%s: got %.12g want %.12g", name, got, want)
		}
	}
	near("funding", episode.FundingPnL, wantFunding)
	near("fees", episode.Fees, wantFees)
	near("gross", episode.GrossRealizedPnL, wantGross)
	near("net", episode.NetPnL, wantGross+wantFunding-wantFees)
	near("decision equity", r.RiskState.EquityHistory[1], config.RiskConfig.StartingEquity-open.Fee+wantGross+wantFunding)
	near("final equity", r.RiskState.Equity, config.RiskConfig.StartingEquity+episode.NetPnL)
}

func TestImportedClosedLoop(t *testing.T) {
	f, err := os.Open("testdata/klines.csv")
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	raw, err := Normalize(f, "4h", time.Date(2024, 1, 2, 0, 0, 0, 0, time.UTC))
	if err != nil {
		t.Fatal(err)
	}
	store, err := workbench.Open(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	snap, err := store.Import(raw)
	if err != nil {
		t.Fatal(err)
	}
	var inputs []research.OfflineDecisionInput
	for _, c := range snap.Dataset.Candles {
		inputs = append(inputs, research.OfflineDecisionInput{Indicator: research.StrategyIndicator{Symbol: "BTC", Timeframe: "4h", CutoffTimeNS: c.CloseTimeNS, Price: c.Close, ATR: 1, RealizedVol: .8, CVDSlope: .1}, State: research.StrategyState{PlaybookState: research.PlaybookState{Symbol: "BTC", Timeframe: "4h", CutoffTimeNS: c.CloseTimeNS, DirectionalState: "UPTREND", DirectionalConfidence: .8, VolatilityState: "NORMAL", VolatilityConfidence: .8, PositioningState: "HEALTHY", PositioningConfidence: .8, QualityState: "TRADABLE", QualityConfidence: .8, DirectionSignal: 1}, Regime: "TREND", RegimeConfidence: .8, QualityFlags: []string{"OK"}}, AvailableTimeNS: c.CloseTimeNS, ValidUntilNS: c.CloseTimeNS})
	}
	payload, err := json.Marshal(inputs)
	if err != nil {
		t.Fatal(err)
	}
	source := DecisionSource{Description: "caller indicator fixture", Availability: "caller asserts availability at each close", QuoteAssumption: USDQuoteAssumption}
	config := research.OfflineClosedLoopConfig{Replay: research.OfflineReplayConfig{HistoryLimit: 2, IndicatorWindow: 1}, StrategyConfig: research.DefaultStrategyFlowConfig(), RiskConfig: research.DefaultStatefulRiskConfig()}
	start, end := snap.Dataset.Candles[0].CloseTimeNS, snap.Dataset.Candles[1].CloseTimeNS
	run := func(data []byte, src DecisionSource, a, b int64) error {
		_, e := RunImportedClosedLoop(store, snap.ID, a, b, data, src, config)
		return e
	}
	result, err := RunImportedClosedLoop(store, snap.ID, start, end, payload, source, config)
	if err != nil {
		t.Fatal(err)
	}
	if len(result.Result.Decisions) != 2 || result.Provenance.SourceSHA256 != snap.Dataset.Provenance.SourceSHA256 || result.SnapshotID != snap.ID || !strings.Contains(result.FundingAssumption, "absent settlements are not modeled") {
		t.Fatal("missing decisions or provenance")
	}
	var objects []map[string]any
	if err := json.Unmarshal(payload, &objects); err != nil {
		t.Fatal(err)
	}
	delete(objects[0]["Indicator"].(map[string]any), "funding_oi_z")
	missing, _ := json.Marshal(objects)
	wrong := append([]research.OfflineDecisionInput(nil), inputs...)
	wrong[0].Indicator.Price++
	wrongJSON, _ := json.Marshal(wrong)
	swapped := []research.OfflineDecisionInput{inputs[1], inputs[0]}
	swappedJSON, _ := json.Marshal(swapped)
	short, _ := json.Marshal(inputs[:1])
	invalidSource := source
	invalidSource.Availability = ""
	wrongTime := append([]research.OfflineDecisionInput(nil), inputs...)
	wrongTime[0].AvailableTimeNS++
	wrongTimeJSON, _ := json.Marshal(wrongTime)
	wrongIdentity := append([]research.OfflineDecisionInput(nil), inputs...)
	wrongIdentity[0].State.Symbol = "ETH"
	wrongIdentityJSON, _ := json.Marshal(wrongIdentity)
	wrongQuote := source
	wrongQuote.QuoteAssumption = ""
	gapDataset := snap.Dataset
	gapDataset.Candles = append([]research.ReplayCandle(nil), snap.Dataset.Candles...)
	gapDataset.Candles[1].OpenTimeNS += 4 * 3600e9
	gapDataset.Candles[1].CloseTimeNS += 4 * 3600e9
	gapRaw, _ := json.Marshal(gapDataset)
	gapSnap, err := store.Import(gapRaw)
	if err != nil {
		t.Fatal(err)
	}
	gapInputs := append([]research.OfflineDecisionInput(nil), inputs...)
	gapInputs[1].Indicator.CutoffTimeNS += 4 * 3600e9
	gapInputs[1].State.CutoffTimeNS += 4 * 3600e9
	gapInputs[1].AvailableTimeNS += 4 * 3600e9
	gapInputs[1].ValidUntilNS += 4 * 3600e9
	gapJSON, _ := json.Marshal(gapInputs)
	if _, err := RunImportedClosedLoop(store, gapSnap.ID, start, end+4*3600e9, gapJSON, source, config); err == nil {
		t.Fatal("expected gap rejection")
	}
	for _, delta := range []int64{1, 1e6} {
		inclusive := snap.Dataset
		inclusive.Candles = append([]research.ReplayCandle(nil), snap.Dataset.Candles...)
		for i := range inclusive.Candles {
			inclusive.Candles[i].CloseTimeNS -= delta
		}
		data, _ := json.Marshal(inclusive)
		stored, err := store.Import(data)
		if err != nil {
			t.Fatal(err)
		}
		if _, err := RunImportedClosedLoop(store, stored.ID, start, end, payload, source, config); err != nil {
			t.Fatalf("inclusive close %d: %v", delta, err)
		}
	}
	for _, tc := range []struct {
		name   string
		rows   []workbench.Funding
		reject bool
	}{
		{"outside", []workbench.Funding{{TimeNS: start - 1, Rate: .01}, {TimeNS: end + 1, Rate: .01}}, false},
		{"first close", []workbench.Funding{{TimeNS: start, Rate: .01}}, false},
		{"inside", []workbench.Funding{{TimeNS: start + 1, Rate: .01}}, false},
		{"last close", []workbench.Funding{{TimeNS: end, Rate: .01}}, false},
		{"zero inside", []workbench.Funding{{TimeNS: start, Rate: 0}}, false},
	} {
		t.Run("funding "+tc.name, func(t *testing.T) {
			d := snap.Dataset
			d.Funding = tc.rows
			data, _ := json.Marshal(d)
			stored, err := store.Import(data)
			if err != nil {
				t.Fatal(err)
			}
			_, err = RunImportedClosedLoop(store, stored.ID, start, end, payload, source, config)
			if (err != nil) != tc.reject {
				t.Fatalf("unexpected error: %v", err)
			}
		})
	}
	duplicate := []byte(strings.Replace(string(payload), `"funding_oi_z":0`, `"funding_oi_z":0,"funding_oi_z":1`, 1))
	caseVariant := []byte(strings.Replace(string(payload), `"funding_oi_z":0`, `"funding_oi_z":0,"FUNDING_OI_Z":1`, 1))
	duplicateRoot := []byte(strings.Replace(string(payload), `"AvailableTimeNS":`, `"AvailableTimeNS":0,"AvailableTimeNS":`, 1))
	for _, tc := range []struct {
		name       string
		data       []byte
		src        DecisionSource
		start, end int64
	}{
		{"missing field", missing, source, start, end}, {"missing row", short, source, start, end}, {"price", wrongJSON, source, start, end}, {"order", swappedJSON, source, start, end}, {"coverage", payload, source, start - 4*3600e9, end}, {"source", payload, invalidSource, start, end}, {"time", wrongTimeJSON, source, start, end}, {"identity", wrongIdentityJSON, source, start, end}, {"quote", payload, wrongQuote, start, end}, {"duplicate field", duplicate, source, start, end}, {"case variant", caseVariant, source, start, end}, {"duplicate root", duplicateRoot, source, start, end},
	} {
		t.Run(tc.name, func(t *testing.T) {
			if err := run(tc.data, tc.src, tc.start, tc.end); err == nil {
				t.Fatal("expected rejection")
			}
		})
	}
}

func TestGeneratedImportedClosedLoop(t *testing.T) {
	f, err := os.Open("testdata/klines.csv")
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	raw, err := Normalize(f, "4h", time.Date(2024, 1, 2, 0, 0, 0, 0, time.UTC))
	if err != nil {
		t.Fatal(err)
	}
	store, err := workbench.Open(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	snap, err := store.Import(raw)
	if err != nil {
		t.Fatal(err)
	}
	stale := false
	var rows []GeneratedDecisionInput
	for _, c := range snap.Dataset.Candles {
		v := map[string]float64{"realized_vol": .8, "vix_z": 0, "funding_oi_z": 0, "cvd_slope": .1, "zen_pulse": 60, "momentum_roc_z": 1, "liq_intensity_z": 0, "liq_imbalance": 0, "yield_10y_z": 0, "cycle_position": 1}
		rows = append(rows, GeneratedDecisionInput{StateInput: research.StateEngineInput{Symbol: "BTC", Timeframe: "4h", CutoffTimeNS: c.CloseTimeNS, AvailableTimeNS: c.CloseTimeNS, Values: v, StructureState: "UPTREND", LiquidationStale: &stale, QualityFlags: []string{"OK"}}, Indicator: research.StrategyIndicator{Symbol: "BTC", Timeframe: "4h", CutoffTimeNS: c.CloseTimeNS, Price: c.Close, ATR: 1, RealizedVol: .8, CVDSlope: .1, StructureState: "UPTREND", MomentumROCZ: 1}, AvailableTimeNS: c.CloseTimeNS, ValidUntilNS: c.CloseTimeNS})
	}
	source := DecisionSource{Description: "fixture", Availability: "at close", QuoteAssumption: USDQuoteAssumption}
	config := research.OfflineClosedLoopConfig{Replay: research.OfflineReplayConfig{HistoryLimit: 2, IndicatorWindow: 1}, StrategyConfig: research.DefaultStrategyFlowConfig(), RiskConfig: research.DefaultStatefulRiskConfig()}
	start, end := rows[0].AvailableTimeNS, rows[len(rows)-1].AvailableTimeNS
	run := func(r []GeneratedDecisionInput, overlay bool) (*GeneratedClosedLoopResult, error) {
		b, e := json.Marshal(r)
		if e != nil {
			t.Fatal(e)
		}
		return RunGeneratedImportedClosedLoop(store, snap.ID, start, end, b, source, config, overlay)
	}
	first, err := run(rows, false)
	if err != nil {
		t.Fatal(err)
	}
	second, err := run(rows, false)
	if err != nil || !reflect.DeepEqual(first, second) {
		t.Fatalf("nondeterministic: %v", err)
	}
	if len(first.Result.Decisions) != len(rows) || first.InputSHA256 == "" || !strings.Contains(first.Limitations, "cold") {
		t.Fatal("missing result or provenance")
	}
	alternate, err := run(rows, true)
	if err != nil || !alternate.LiquidationOverlay {
		t.Fatalf("overlay: %v", err)
	}
	cases := map[string]func([]GeneratedDecisionInput){
		"identity":        func(r []GeneratedDecisionInput) { r[0].StateInput.Symbol = "ETH" },
		"timeframe":       func(r []GeneratedDecisionInput) { r[0].StateInput.Timeframe = "1h" },
		"cutoff":          func(r []GeneratedDecisionInput) { r[0].StateInput.CutoffTimeNS++ },
		"vol":             func(r []GeneratedDecisionInput) { r[0].Indicator.RealizedVol++ },
		"funding":         func(r []GeneratedDecisionInput) { r[0].Indicator.FundingOIZ++ },
		"cvd":             func(r []GeneratedDecisionInput) { r[0].Indicator.CVDSlope++ },
		"momentum":        func(r []GeneratedDecisionInput) { r[0].Indicator.MomentumROCZ++ },
		"structure":       func(r []GeneratedDecisionInput) { r[0].Indicator.StructureState = "NEUTRAL" },
		"price":           func(r []GeneratedDecisionInput) { r[0].Indicator.Price++ },
		"future":          func(r []GeneratedDecisionInput) { r[0].StateInput.AvailableTimeNS++ },
		"strategy future": func(r []GeneratedDecisionInput) { r[0].AvailableTimeNS++ },
		"order":           func(r []GeneratedDecisionInput) { r[0], r[1] = r[1], r[0] },
		"missing value":   func(r []GeneratedDecisionInput) { delete(r[0].StateInput.Values, "vix_z") },
	}
	for name, mutate := range cases {
		t.Run(name, func(t *testing.T) {
			b, _ := json.Marshal(rows)
			var copyRows []GeneratedDecisionInput
			if err := json.Unmarshal(b, &copyRows); err != nil {
				t.Fatal(err)
			}
			mutate(copyRows)
			if _, err := run(copyRows, false); err == nil {
				t.Fatal("accepted invalid input")
			}
		})
	}
	for _, flag := range []string{"null", `""`, `"  \t"`} {
		payload, err := json.Marshal(rows)
		if err != nil {
			t.Fatal(err)
		}
		payload = bytes.Replace(payload, []byte(`"quality_flags":["OK"]`), []byte(`"quality_flags":[`+flag+`]`), 1)
		if _, err := RunGeneratedImportedClosedLoop(store, snap.ID, start, end, payload, source, config, false); err == nil {
			t.Fatalf("accepted invalid quality flag %s", flag)
		}
	}
	changed := append([]GeneratedDecisionInput(nil), rows...)
	changed[0].Indicator.IndicatorVersion = "different"
	third, err := run(changed, false)
	if err != nil || third.InputSHA256 == first.InputSHA256 {
		t.Fatalf("input identity unchanged: %v", err)
	}
}
