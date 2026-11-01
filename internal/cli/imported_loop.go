package cli

import (
	"bytes"
	"crypto/sha256"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"reflect"
	"runtime/debug"

	"github.com/infdahai/zenQuant/internal/history"
	"github.com/infdahai/zenQuant/internal/localfile"
	"github.com/infdahai/zenQuant/internal/research"
	"github.com/infdahai/zenQuant/internal/workbench"
)

// Decision arrays carry complete indicator and state objects for each close.
const maxDecisionInput = 16 << 20

type importedLoopBuild struct {
	Identity  string `json:"identity"`
	Revision  string `json:"vcs_revision"`
	Modified  string `json:"vcs_modified"`
	GoVersion string `json:"go_version"`
}

func importedLoopBuildInfo() importedLoopBuild {
	build := importedLoopBuild{Identity: "unknown", Revision: "unknown", Modified: "unknown", GoVersion: "unknown"}
	info, ok := debug.ReadBuildInfo()
	if !ok {
		return build
	}
	build.GoVersion = info.GoVersion
	for _, setting := range info.Settings {
		switch setting.Key {
		case "vcs.revision":
			build.Revision = setting.Value
		case "vcs.modified":
			build.Modified = setting.Value
		}
	}
	if build.Revision != "unknown" && build.Modified == "false" {
		build.Identity = "clean VCS revision; build flags and dependencies are not identified"
	}
	return build
}

type importedLoopConfig struct {
	TakerFeeRate         float64
	MaxTradeQtyPerCutoff float64
	HistoryLimit         int
	IndicatorWindow      int
	StrategyConfig       research.StrategyFlowConfig
	RiskConfig           research.StatefulRiskConfig
}

func runImportedLoop(args []string, out, stderr io.Writer) error {
	flags := flag.NewFlagSet("history-closed-loop", flag.ContinueOnError)
	flags.SetOutput(stderr)
	storePath := flags.String("store", "", "existing workbench store directory")
	snapshot := flags.String("snapshot", "", "imported snapshot ID")
	start := flags.Int64("start-ns", 0, "inclusive 4h close in Unix nanoseconds")
	end := flags.Int64("end-ns", 0, "inclusive 4h close in Unix nanoseconds")
	mode := flags.String("mode", "external", "decision mode: external or generated")
	decisionsPath := flags.String("decisions", "", "complete external decision JSON array")
	observationsPath := flags.String("observations", "", "complete generated-state observation JSON array")
	overlay := flags.String("overlay", "", "generated liquidation overlay: on or off (required in generated mode)")
	configPath := flags.String("config", "", "complete configuration JSON")
	source := flags.String("source", "", "decision source declaration")
	availability := flags.String("availability", "", "decision availability declaration")
	quote := flags.String("quote-assumption", "", "explicit USDT/USD assumption")
	if err := flags.Parse(args); err != nil {
		if errors.Is(err, flag.ErrHelp) {
			return nil
		}
		return err
	}
	if flags.NArg() != 0 || *storePath == "" || *snapshot == "" || *configPath == "" {
		return errors.New("store, snapshot and config are required; positional arguments are not accepted")
	}
	provided := make(map[string]bool)
	flags.Visit(func(f *flag.Flag) { provided[f.Name] = true })
	switch *mode {
	case "external":
		if *decisionsPath == "" || provided["observations"] || provided["overlay"] {
			return errors.New("external mode requires decisions and forbids observations and overlay")
		}
	case "generated":
		if *observationsPath == "" || provided["decisions"] || (*overlay != "on" && *overlay != "off") {
			return errors.New("generated mode requires observations and overlay on or off, and forbids decisions")
		}
	default:
		return errors.New("mode must be external or generated")
	}
	info, err := os.Stat(*storePath)
	if err != nil {
		return fmt.Errorf("store: %w", err)
	}
	if !info.IsDir() {
		return errors.New("store must be an existing directory")
	}
	inputPath, inputName := *decisionsPath, "decisions"
	if *mode == "generated" {
		inputPath, inputName = *observationsPath, "observations"
	}
	decisions, err := localfile.Read(inputPath, maxDecisionInput)
	if err != nil {
		return fmt.Errorf("%s: %w", inputName, err)
	}
	rawConfig, err := localfile.Read(*configPath, history.MaxCSV)
	if err != nil {
		return fmt.Errorf("config: %w", err)
	}
	if err := history.RequireCompleteObject(rawConfig, reflect.TypeOf(importedLoopConfig{})); err != nil {
		return fmt.Errorf("config: %w", err)
	}
	var config importedLoopConfig
	dec := json.NewDecoder(bytes.NewReader(rawConfig))
	dec.DisallowUnknownFields()
	if err := dec.Decode(&config); err != nil {
		return fmt.Errorf("config: %w", err)
	}
	if config.MaxTradeQtyPerCutoff <= 0 {
		return errors.New("config: MaxTradeQtyPerCutoff must be positive")
	}
	store, err := workbench.Open(*storePath)
	if err != nil {
		return err
	}
	sourceInfo := history.DecisionSource{Description: *source, Availability: *availability, QuoteAssumption: *quote}
	loopConfig := research.OfflineClosedLoopConfig{
		Replay:         research.OfflineReplayConfig{TakerFeeRate: config.TakerFeeRate, MaxTradeQtyPerCutoff: config.MaxTradeQtyPerCutoff, HistoryLimit: config.HistoryLimit, IndicatorWindow: config.IndicatorWindow},
		StrategyConfig: config.StrategyConfig, RiskConfig: config.RiskConfig,
	}
	if *mode == "generated" {
		result, err := history.RunGeneratedImportedClosedLoop(store, *snapshot, *start, *end, decisions, sourceInfo, loopConfig, *overlay == "on")
		if err != nil {
			return err
		}
		payload, err := json.Marshal(struct {
			Scope   string                             `json:"scope"`
			Mode    string                             `json:"mode"`
			Build   importedLoopBuild                  `json:"build"`
			StartNS int64                              `json:"start_ns"`
			EndNS   int64                              `json:"end_ns"`
			Config  importedLoopConfig                 `json:"config"`
			Result  *history.GeneratedClosedLoopResult `json:"result"`
		}{"research-only", "generated", importedLoopBuildInfo(), *start, *end, config, result})
		if err != nil {
			return err
		}
		_, err = out.Write(append(payload, '\n'))
		return err
	}
	result, err := history.RunImportedClosedLoop(store, *snapshot, *start, *end, decisions, sourceInfo, loopConfig)
	if err != nil {
		return err
	}
	sum := sha256.Sum256(decisions)
	payload, err := json.Marshal(struct {
		Scope           string                            `json:"scope"`
		Build           importedLoopBuild                 `json:"build"`
		StartNS         int64                             `json:"start_ns"`
		EndNS           int64                             `json:"end_ns"`
		DecisionsSHA256 string                            `json:"decisions_sha256"`
		Config          importedLoopConfig                `json:"config"`
		Result          *history.ImportedClosedLoopResult `json:"result"`
	}{"research-only", importedLoopBuildInfo(), *start, *end, fmt.Sprintf("%x", sum), config, result})
	if err != nil {
		return err
	}
	_, err = out.Write(append(payload, '\n'))
	return err
}
