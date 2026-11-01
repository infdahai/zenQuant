package history

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"reflect"
	"strings"

	"github.com/infdahai/zenQuant/internal/research"
	"github.com/infdahai/zenQuant/internal/workbench"
)

// DecisionSource is the caller's declaration, not proof of historical arrival.
// QuoteAssumption must explicitly acknowledge that USDT prices are treated as USD.
type DecisionSource struct {
	Description     string
	Availability    string
	QuoteAssumption string
}

const USDQuoteAssumption = "BTCUSDT prices treated as USD at 1:1"

// ImportedClosedLoopResult retains the imported source identity and caller claims.
type ImportedClosedLoopResult struct {
	SnapshotID        string
	Provenance        workbench.DatasetProvenance
	Source            DecisionSource
	FundingAssumption string
	Result            *research.OfflineClosedLoopResult
}

// RunImportedClosedLoop runs an inclusive, 4h-close-aligned window from a saved
// import. decisions is a JSON array of complete OfflineDecisionInput objects;
// every indicator and state field must be present, including explicit zeros.
// Neither the supplied as-of nor the availability declaration proves arrival.
// Missing funding is not evidence of zero funding; supplied settlements within
// the window are passed to the closed loop.
func RunImportedClosedLoop(store *workbench.Store, snapshotID string, startNS, endNS int64, decisions []byte, source DecisionSource, config research.OfflineClosedLoopConfig) (*ImportedClosedLoopResult, error) {
	const bar = int64(4 * 60 * 60 * 1e9)
	if store == nil || startNS <= 0 || endNS < startNS || startNS%bar != 0 || endNS%bar != 0 {
		return nil, fmt.Errorf("valid store and aligned inclusive close bounds required")
	}
	if strings.TrimSpace(source.Description) == "" || strings.TrimSpace(source.Availability) == "" || source.QuoteAssumption != USDQuoteAssumption {
		return nil, fmt.Errorf("decision source, availability declaration and USD quote assumption required")
	}
	snapshot, err := store.Snapshot(snapshotID)
	if err != nil {
		return nil, err
	}
	d := snapshot.Dataset
	if d.Instrument != "BTC" || d.Timeframe != "4h" || d.Provenance == nil || d.Provenance.SourceSymbol != "BTCUSDT" {
		return nil, fmt.Errorf("imported BTCUSDT 4h dataset with provenance required")
	}
	var rows []json.RawMessage
	if err := json.Unmarshal(decisions, &rows); err != nil || rows == nil {
		return nil, fmt.Errorf("decision inputs must be a JSON array")
	}
	inputs := make([]research.OfflineDecisionInput, 0, len(rows))
	for i, row := range rows {
		if err := requireFields(row, reflect.TypeOf(research.OfflineDecisionInput{})); err != nil {
			return nil, fmt.Errorf("decision %d: %w", i, err)
		}
		var input research.OfflineDecisionInput
		dec := json.NewDecoder(bytes.NewReader(row))
		dec.DisallowUnknownFields()
		if err := dec.Decode(&input); err != nil {
			return nil, fmt.Errorf("decision %d: %w", i, err)
		}
		inputs = append(inputs, input)
	}
	events, err := research.PrepareReplayEvents(map[string][]research.ReplayCandle{"4h": d.Candles}, []string{"4h"}, "4h", &startNS, &endNS)
	if err != nil {
		return nil, fmt.Errorf("prepare imported candles: %w", err)
	}
	candles := make([]research.ReplayCandle, 0, len(events))
	for _, event := range events {
		candles = append(candles, event.ReplayCandle)
	}
	if len(candles) == 0 || candles[0].CloseTimeNS != startNS || candles[len(candles)-1].CloseTimeNS != endNS || len(candles) != len(inputs) {
		return nil, fmt.Errorf("candle coverage or decision count mismatch")
	}
	for i, candle := range candles {
		if i > 0 && candle.CloseTimeNS-candles[i-1].CloseTimeNS != bar {
			return nil, fmt.Errorf("candle gap")
		}
		in := inputs[i]
		if in.Indicator.CutoffTimeNS != candle.CloseTimeNS || in.State.CutoffTimeNS != candle.CloseTimeNS || in.Indicator.Symbol != "BTC" || in.State.Symbol != "BTC" || in.Indicator.Timeframe != "4h" || in.State.Timeframe != "4h" || in.AvailableTimeNS != candle.CloseTimeNS || in.ValidUntilNS < candle.CloseTimeNS || in.Indicator.Price != candle.Close {
			return nil, fmt.Errorf("decision %d does not match candle identity, time or price", i)
		}
	}
	config.Replay.Symbol = "BTC"
	config.Replay.Timeframes = []string{"4h"}
	config.Replay.ExecutionTimeframe = "4h"
	config.Replay.Candles = map[string][]research.ReplayCandle{"4h": candles}
	config.Replay.StartNS, config.Replay.EndNS = &startNS, &endNS
	config.Replay.Funding = nil
	for _, f := range d.Funding {
		// The fresh replay is flat before the first close; settlements there have
		// no position to charge. The first close itself is processed before fills.
		if f.TimeNS >= startNS && f.TimeNS <= endNS {
			config.Replay.Funding = append(config.Replay.Funding, research.ReplayFundingRow{FundingTimeNS: f.TimeNS, FundingRate: f.Rate})
		}
	}
	config.Inputs = inputs
	result, err := research.RunOfflineClosedLoop(config)
	if err != nil {
		return nil, err
	}
	return &ImportedClosedLoopResult{SnapshotID: snapshotID, Provenance: *d.Provenance, Source: source, FundingAssumption: "Funding coverage is unknown; absent settlements are not modeled.", Result: result}, nil
}

// requireFields checks presence separately from decoding so zero remains valid.
func requireFields(raw json.RawMessage, typ reflect.Type) error {
	dec := json.NewDecoder(bytes.NewReader(raw))
	token, err := dec.Token()
	if err != nil || token != json.Delim('{') {
		return fmt.Errorf("expected object")
	}
	fields := make(map[string]json.RawMessage)
	for dec.More() {
		token, err := dec.Token()
		if err != nil {
			return err
		}
		name, ok := token.(string)
		if !ok {
			return fmt.Errorf("expected field name")
		}
		if _, exists := fields[name]; exists {
			return fmt.Errorf("duplicate field %s", name)
		}
		var value json.RawMessage
		if err := dec.Decode(&value); err != nil {
			return err
		}
		fields[name] = value
	}
	if _, err := dec.Token(); err != nil {
		return err
	}
	if _, err := dec.Token(); err != io.EOF {
		return fmt.Errorf("trailing data")
	}
	expected := make(map[string]bool)
	var check func(reflect.Type) error
	check = func(t reflect.Type) error {
		for i := 0; i < t.NumField(); i++ {
			f := t.Field(i)
			if f.Anonymous {
				if err := check(f.Type); err != nil {
					return err
				}
				continue
			}
			name := strings.Split(f.Tag.Get("json"), ",")[0]
			if name == "" {
				name = f.Name
			}
			expected[name] = true
			value, ok := fields[name]
			if !ok || bytes.Equal(bytes.TrimSpace(value), []byte("null")) {
				return fmt.Errorf("missing field %s", name)
			}
			if f.Type.Kind() == reflect.Struct {
				if err := requireFields(value, f.Type); err != nil {
					return fmt.Errorf("%s: %w", name, err)
				}
			}
		}
		return nil
	}
	if err := check(typ); err != nil {
		return err
	}
	for name := range fields {
		if !expected[name] {
			return fmt.Errorf("unexpected field %s", name)
		}
	}
	return nil
}
