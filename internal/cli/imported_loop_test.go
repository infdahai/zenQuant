package cli

import (
	"bytes"
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/infdahai/zenQuant/internal/history"
	"github.com/infdahai/zenQuant/internal/research"
	"github.com/infdahai/zenQuant/internal/workbench"
)

func TestSyntheticLongHistoryCLI(t *testing.T) {
	const count = 14976
	const startMS int64 = 1577836800000
	const barMS int64 = 14400000
	dir, err := filepath.EvalSymlinks(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	binary := filepath.Join(dir, "zenquant")
	if b, err := exec.Command("go", "build", "-o", binary, "../../cmd/zenquant").CombinedOutput(); err != nil {
		t.Fatalf("build: %v %s", err, b)
	}
	var csv, funding bytes.Buffer
	funding.WriteByte('[')
	for i := 0; i < count; i++ {
		open := startMS + int64(i)*barMS
		fmt.Fprintf(&csv, "%d,40000.1234,41000.1234,39000.1234,40500.1234,123.4567,%d,5000000,12345,50,2000000,0\n", open, open+barMS-1)
		if i%2 == 1 {
			if i > 1 {
				funding.WriteByte(',')
			}
			fmt.Fprintf(&funding, `{"symbol":"BTCUSDT","fundingTime":%d,"fundingRate":"0.00010000"}`, open+barMS)
		}
	}
	funding.WriteByte(']')
	csvPath, fundingPath := filepath.Join(dir, "bars.csv"), filepath.Join(dir, "funding.json")
	for p, data := range map[string][]byte{csvPath: csv.Bytes(), fundingPath: funding.Bytes()} {
		if err := os.WriteFile(p, data, 0600); err != nil {
			t.Fatal(err)
		}
	}
	asOf := time.UnixMilli(startMS + count*barMS).UTC().Format(time.RFC3339)
	cmd := exec.Command(binary, "history-import", "--input", csvPath, "--funding-input", fundingPath, "--symbol", "BTCUSDT", "--timeframe", "4h", "--as-of", asOf)
	raw, err := cmd.Output()
	if err != nil {
		t.Fatalf("history import: %v %s", err, err.(*exec.ExitError).Stderr)
	}
	storePath := filepath.Join(dir, "store")
	cmd = exec.Command(binary, "workbench", "--store", storePath, "import")
	cmd.Stdin = bytes.NewReader(raw)
	output, err := cmd.Output()
	if err != nil {
		t.Fatalf("workbench import: %v", err)
	}
	var imported struct {
		ID string `json:"ID"`
	}
	if err := json.Unmarshal(output, &imported); err != nil || imported.ID == "" {
		t.Fatalf("import response: %v", err)
	}
	store, err := workbench.Open(storePath)
	if err != nil {
		t.Fatal(err)
	}
	snap, err := store.Snapshot(imported.ID)
	if err != nil || len(snap.Dataset.Candles) != count || len(snap.Dataset.Funding) != count/2 {
		t.Fatalf("snapshot: %v", err)
	}
	cmd = exec.Command(binary, "workbench", "--store", storePath, "list")
	cmd.Stdin = bytes.NewReader(raw)
	if _, err := cmd.Output(); err == nil {
		t.Fatal("oversized list request accepted")
	}
	var inputs []research.OfflineDecisionInput
	for _, c := range snap.Dataset.Candles {
		inputs = append(inputs, research.OfflineDecisionInput{Indicator: research.StrategyIndicator{Symbol: "BTC", Timeframe: "4h", CutoffTimeNS: c.CloseTimeNS, Price: c.Close, ATR: 1, RealizedVol: .8, CVDSlope: .1}, State: research.StrategyState{PlaybookState: research.PlaybookState{Symbol: "BTC", Timeframe: "4h", CutoffTimeNS: c.CloseTimeNS, DirectionalState: "UPTREND", DirectionalConfidence: .8, VolatilityState: "NORMAL", VolatilityConfidence: .8, PositioningState: "HEALTHY", PositioningConfidence: .8, QualityState: "TRADABLE", QualityConfidence: .8, DirectionSignal: 1}, Regime: "TREND", RegimeConfidence: .8, QualityFlags: []string{"OK"}}, AvailableTimeNS: c.CloseTimeNS, ValidUntilNS: c.CloseTimeNS})
	}
	decisions, err := json.Marshal(inputs)
	if err != nil {
		t.Fatal(err)
	}
	config, err := json.Marshal(importedLoopConfig{TakerFeeRate: .001, MaxTradeQtyPerCutoff: 1000, HistoryLimit: 2, IndicatorWindow: 1, StrategyConfig: research.DefaultStrategyFlowConfig(), RiskConfig: research.DefaultStatefulRiskConfig()})
	if err != nil {
		t.Fatal(err)
	}
	decisionPath, configPath := filepath.Join(dir, "decisions.json"), filepath.Join(dir, "config.json")
	for p, data := range map[string][]byte{decisionPath: decisions, configPath: config} {
		if err := os.WriteFile(p, data, 0600); err != nil {
			t.Fatal(err)
		}
	}
	snapshotInfo, err := os.Stat(filepath.Join(storePath, "snapshot-"+imported.ID+".json"))
	if err != nil {
		t.Fatal(err)
	}
	t.Logf("synthetic sizes: CSV=%d funding=%d dataset=%d snapshot=%d decisions=%d", csv.Len(), funding.Len(), len(raw), snapshotInfo.Size(), len(decisions))
	cmd = exec.Command(binary, "history-closed-loop", "--store", storePath, "--snapshot", imported.ID, "--start-ns", fmt.Sprint(inputs[0].AvailableTimeNS), "--end-ns", fmt.Sprint(inputs[count-1].AvailableTimeNS), "--decisions", decisionPath, "--config", configPath, "--source", "synthetic", "--availability", "synthetic at close", "--quote-assumption", history.USDQuoteAssumption)
	output, err = cmd.Output()
	if err != nil {
		if e, ok := err.(*exec.ExitError); ok {
			t.Fatalf("closed loop: %v %s", err, e.Stderr)
		}
		t.Fatal(err)
	}
	var result struct {
		Result history.ImportedClosedLoopResult `json:"result"`
	}
	if err := json.Unmarshal(output, &result); err != nil || result.Result.Result == nil || len(result.Result.Result.Decisions) != count {
		t.Fatalf("closed-loop result: %v", err)
	}
}

func TestImportedLoopCLI(t *testing.T) {
	dir, err := filepath.EvalSymlinks(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	binary := filepath.Join(dir, "zenquant")
	if b, err := exec.Command("go", "build", "-o", binary, "../../cmd/zenquant").CombinedOutput(); err != nil {
		t.Fatalf("build: %v %s", err, b)
	}
	csv, err := os.Open("../history/testdata/klines.csv")
	if err != nil {
		t.Fatal(err)
	}
	defer csv.Close()
	raw, err := history.Normalize(csv, "4h", time.Date(2024, 1, 2, 0, 0, 0, 0, time.UTC))
	if err != nil {
		t.Fatal(err)
	}
	storePath := filepath.Join(dir, "store")
	store, err := workbench.Open(storePath)
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
	data, err := json.Marshal(inputs)
	if err != nil {
		t.Fatal(err)
	}
	config, err := json.Marshal(importedLoopConfig{TakerFeeRate: .001, MaxTradeQtyPerCutoff: 1000, HistoryLimit: 2, IndicatorWindow: 1, StrategyConfig: research.DefaultStrategyFlowConfig(), RiskConfig: research.DefaultStatefulRiskConfig()})
	if err != nil {
		t.Fatal(err)
	}
	decisionsPath, configPath := filepath.Join(dir, "decisions.json"), filepath.Join(dir, "config.json")
	if err := os.WriteFile(decisionsPath, data, 0600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(configPath, config, 0600); err != nil {
		t.Fatal(err)
	}
	args := []string{"history-closed-loop", "--store", storePath, "--snapshot", snap.ID, "--start-ns", fmt.Sprint(inputs[0].AvailableTimeNS), "--end-ns", fmt.Sprint(inputs[1].AvailableTimeNS), "--decisions", decisionsPath, "--config", configPath, "--source", "fixture", "--availability", "caller asserts close availability", "--quote-assumption", history.USDQuoteAssumption}
	run := func(a []string) ([]byte, string, error) {
		t.Helper()
		cmd := exec.Command(binary, a...)
		var stderr bytes.Buffer
		cmd.Stderr = &stderr
		b, e := cmd.Output()
		return b, stderr.String(), e
	}
	first, stderr, err := run(args)
	if err != nil {
		t.Fatalf("run: %v %s", err, stderr)
	}
	second, stderr, err := run(args)
	if err != nil || !bytes.Equal(first, second) {
		t.Fatalf("nondeterministic output: %v %s", err, stderr)
	}
	var result struct {
		Build  importedLoopBuild                `json:"build"`
		Scope  string                           `json:"scope"`
		Config importedLoopConfig               `json:"config"`
		Result history.ImportedClosedLoopResult `json:"result"`
	}
	if err := json.Unmarshal(first, &result); err != nil || result.Scope != "research-only" || result.Result.SnapshotID != snap.ID || len(result.Result.Result.Decisions) != 2 || len(result.Result.Result.Replay.Reports) == 0 || result.Result.Source.Description != "fixture" {
		t.Fatalf("result: %s %v", first, err)
	}
	if result.Build.Identity == "" || result.Build.Revision == "" || result.Build.Modified == "" || result.Build.GoVersion == "" {
		t.Fatalf("incomplete build identity: %+v", result.Build)
	}
	// Reconstruct a content-addressed legacy snapshot that predates normalized
	// cutoff validation, then exercise the actual CLI against it.
	legacy := snap
	legacy.ID = ""
	legacy.Dataset.Candles = append([]research.ReplayCandle(nil), snap.Dataset.Candles...)
	for i := range legacy.Dataset.Candles {
		legacy.Dataset.Candles[i].CloseTimeNS -= 1_000_000
	}
	provenance := *legacy.Dataset.Provenance
	legacy.Dataset.Provenance = &provenance
	legacy.Dataset.Provenance.AsOf = time.Unix(0, inputs[1].AvailableTimeNS-1_000_000).UTC().Format(time.RFC3339Nano)
	legacyBytes, err := json.Marshal(legacy)
	if err != nil {
		t.Fatal(err)
	}
	sum := sha256.Sum256(legacyBytes)
	legacyID := fmt.Sprintf("%x", sum)
	if err := os.WriteFile(filepath.Join(storePath, "snapshot-"+legacyID+".json"), legacyBytes, 0600); err != nil {
		t.Fatal(err)
	}
	legacyArgs := append([]string(nil), args...)
	legacyArgs[4] = legacyID
	if b, _, err := run(legacyArgs); err == nil || len(b) != 0 {
		t.Fatalf("legacy future close accepted: %q %v", b, err)
	}
	badConfig := filepath.Join(dir, "bad.json")
	if err := os.WriteFile(badConfig, []byte(`{}`), 0600); err != nil {
		t.Fatal(err)
	}
	badDecisions := filepath.Join(dir, "bad-decisions.json")
	if err := os.WriteFile(badDecisions, []byte(`[{}]`), 0600); err != nil {
		t.Fatal(err)
	}
	oversize := filepath.Join(dir, "oversize.json")
	if err := os.WriteFile(oversize, bytes.Repeat([]byte(" "), maxDecisionInput+1), 0600); err != nil {
		t.Fatal(err)
	}
	for _, tc := range []struct {
		name   string
		change func([]string)
	}{
		{"missing config fields", func(a []string) { a[12] = badConfig }},
		{"incomplete decisions", func(a []string) { a[10] = badDecisions }},
		{"oversize decisions", func(a []string) { a[10] = oversize }},
		{"wrong quote", func(a []string) { a[len(a)-1] = "USD" }},
		{"missing snapshot", func(a []string) { a[4] = strings.Repeat("a", 64) }},
		{"unaligned", func(a []string) { a[6] = "1" }},
	} {
		t.Run(tc.name, func(t *testing.T) {
			a := append([]string(nil), args...)
			tc.change(a)
			b, _, err := run(a)
			if err == nil || len(b) != 0 {
				t.Fatalf("expected failure without output: %q %v", b, err)
			}
		})
	}
}
