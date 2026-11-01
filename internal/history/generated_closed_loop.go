package history

import (
	"bytes"
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"io"
	"reflect"

	"github.com/infdahai/zenQuant/internal/research"
	"github.com/infdahai/zenQuant/internal/workbench"
)

// GeneratedDecisionInput supplies complete observed state and strategy snapshots.
// AvailableTimeNS and ValidUntilNS are caller declarations for the strategy input.
// The state input has its own availability declaration.
type GeneratedDecisionInput struct {
	StateInput      research.StateEngineInput  `json:"state_input"`
	Indicator       research.StrategyIndicator `json:"indicator"`
	AvailableTimeNS int64                      `json:"available_time_ns"`
	ValidUntilNS    int64                      `json:"valid_until_ns"`
}

// GeneratedClosedLoopResult records the exact input bytes and classifier settings.
// Input hashes do not establish observation provenance or historical availability.
type GeneratedClosedLoopResult struct {
	*ImportedClosedLoopResult
	InputSHA256        string
	LiquidationOverlay bool
	Limitations        string
}

// RunGeneratedImportedClosedLoop is a research-only entry point. Each call starts
// the classifier cold at the first supplied window observation. No warmup history
// or missing observations are synthesized. The imported loop verifies candle
// alignment, price, source declarations and strategy availability at each close.
func RunGeneratedImportedClosedLoop(store *workbench.Store, snapshotID string, startNS, endNS int64, observations []byte, source DecisionSource, config research.OfflineClosedLoopConfig, liquidationOverlay bool) (*GeneratedClosedLoopResult, error) {
	var rows []json.RawMessage
	if err := json.Unmarshal(observations, &rows); err != nil || rows == nil {
		return nil, fmt.Errorf("observations must be a JSON array")
	}
	engine := research.NewStateEngine(liquidationOverlay)
	inputs := make([]research.OfflineDecisionInput, 0, len(rows))
	var previous int64
	for i, row := range rows {
		if err := RequireCompleteObject(row, reflect.TypeOf(GeneratedDecisionInput{})); err != nil {
			return nil, fmt.Errorf("observation %d: %w", i, err)
		}
		var fields struct {
			StateInput struct {
				Values json.RawMessage `json:"values"`
			} `json:"state_input"`
		}
		if err := json.Unmarshal(row, &fields); err != nil {
			return nil, fmt.Errorf("observation %d: %w", i, err)
		}
		if err := uniqueValueKeys(fields.StateInput.Values); err != nil {
			return nil, fmt.Errorf("observation %d: %w", i, err)
		}
		var in GeneratedDecisionInput
		dec := json.NewDecoder(bytes.NewReader(row))
		dec.DisallowUnknownFields()
		if err := dec.Decode(&in); err != nil {
			return nil, fmt.Errorf("observation %d: %w", i, err)
		}
		s, p := in.StateInput, in.Indicator
		if s.Symbol != p.Symbol || s.Timeframe != p.Timeframe || s.CutoffTimeNS != p.CutoffTimeNS || s.CutoffTimeNS <= previous || s.StructureState != p.StructureState || s.Values["realized_vol"] != p.RealizedVol || s.Values["funding_oi_z"] != p.FundingOIZ || s.Values["cvd_slope"] != p.CVDSlope || s.Values["momentum_roc_z"] != p.MomentumROCZ {
			return nil, fmt.Errorf("observation %d: conflicting identity, order or common indicators", i)
		}
		state, err := engine.Classify(s)
		if err != nil {
			return nil, fmt.Errorf("observation %d: %w", i, err)
		}
		previous = s.CutoffTimeNS
		inputs = append(inputs, research.OfflineDecisionInput{Indicator: p, State: research.StrategyState{PlaybookState: state.PlaybookState, Regime: state.Regime, RegimeConfidence: state.RegimeConfidence, QualityFlags: append([]string{}, s.QualityFlags...)}, AvailableTimeNS: in.AvailableTimeNS, ValidUntilNS: in.ValidUntilNS})
	}
	payload, err := json.Marshal(inputs)
	if err != nil {
		return nil, err
	}
	result, err := RunImportedClosedLoop(store, snapshotID, startNS, endNS, payload, source, config)
	if err != nil {
		return nil, err
	}
	hash := sha256.Sum256(observations)
	return &GeneratedClosedLoopResult{ImportedClosedLoopResult: result, InputSHA256: fmt.Sprintf("%x", hash), LiquidationOverlay: liquidationOverlay, Limitations: "Research only; classifier starts cold at window start without warmup. Input hash covers exact observation bytes only; source and availability are caller claims, not proof. No missing observations are synthesized."}, nil
}

func uniqueValueKeys(raw json.RawMessage) error {
	dec := json.NewDecoder(bytes.NewReader(raw))
	tok, err := dec.Token()
	if err != nil || tok != json.Delim('{') {
		return fmt.Errorf("values must be an object")
	}
	seen := map[string]bool{}
	for dec.More() {
		tok, err := dec.Token()
		if err != nil {
			return err
		}
		key := tok.(string)
		if seen[key] {
			return fmt.Errorf("duplicate indicator %s", key)
		}
		seen[key] = true
		var value json.RawMessage
		if err := dec.Decode(&value); err != nil {
			return err
		}
		if bytes.Equal(bytes.TrimSpace(value), []byte("null")) {
			return fmt.Errorf("null indicator %s", key)
		}
	}
	if _, err := dec.Token(); err != nil {
		return err
	}
	if _, err := dec.Token(); err != io.EOF {
		return fmt.Errorf("trailing values data")
	}
	return nil
}
