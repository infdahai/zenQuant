package history

import (
	"encoding/json"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/infdahai/zenQuant/internal/research"
	"github.com/infdahai/zenQuant/internal/workbench"
)

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
		{"first close", []workbench.Funding{{TimeNS: start, Rate: .01}}, true},
		{"inside", []workbench.Funding{{TimeNS: start + 1, Rate: .01}}, true},
		{"last close", []workbench.Funding{{TimeNS: end, Rate: .01}}, true},
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
