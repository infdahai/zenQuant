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
	if err := os.WriteFile(oversize, bytes.Repeat([]byte(" "), history.MaxCSV+1), 0600); err != nil {
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
