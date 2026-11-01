package history

import (
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"strings"
	"time"

	"github.com/infdahai/zenQuant/internal/research"
	"github.com/infdahai/zenQuant/internal/workbench"
)

// ImportedCandleObservations carries a content-verified snapshot ID and declared
// input hashes. The original bytes are not stored, so their association with the
// embedded dataset cannot be verified. These are not trading signals or a complete indicator pack.
type ImportedCandleObservations struct {
	SnapshotID        string                       `json:"snapshot_id"`
	DatasetSHA256     string                       `json:"dataset_sha256"`
	Provider          string                       `json:"provider"`
	Venue             string                       `json:"venue"`
	Provenance        workbench.DatasetProvenance  `json:"provenance"`
	HashVerification  string                       `json:"hash_verification"`
	HistoricalArrival string                       `json:"historical_arrival"`
	Observations      []research.CandleObservation `json:"observations"`
}

// ObserveImportedCandles computes a cold-start series over exact inclusive close bounds.
func ObserveImportedCandles(store *workbench.Store, snapshotID string, startNS, endNS int64) (*ImportedCandleObservations, error) {
	const bar = int64(4 * time.Hour)
	if store == nil || startNS <= 0 || endNS < startNS || startNS%bar != 0 || endNS%bar != 0 {
		return nil, fmt.Errorf("valid store and aligned inclusive close bounds required")
	}
	snapshot, err := store.Snapshot(snapshotID)
	if err != nil {
		return nil, err
	}
	d := snapshot.Dataset
	if d.Version != "dataset-v1" || d.Provider == "" || d.Venue == "" || d.Instrument != "BTC" || d.Timeframe != "4h" || d.Provenance == nil || d.Provenance.SourceSymbol != "BTCUSDT" {
		return nil, fmt.Errorf("imported BTCUSDT 4h dataset with provenance required")
	}
	p := d.Provenance
	validHash := func(s string) bool {
		b, err := hex.DecodeString(s)
		return err == nil && len(b) == sha256.Size && s == strings.ToLower(s)
	}
	cutoff, err := time.Parse(time.RFC3339Nano, p.AsOf)
	if err != nil || cutoff.IsZero() || p.AsOf != cutoff.UTC().Format(time.RFC3339Nano) || !validHash(p.SourceSHA256) || !validHash(snapshot.SourceSHA256) || (p.FundingSourceSHA256 != "" && !validHash(p.FundingSourceSHA256)) || len(p.SourcePartsSHA256) == 1 {
		return nil, fmt.Errorf("invalid dataset provenance")
	}
	if len(p.SourcePartsSHA256) > 0 {
		h := sha256.New()
		h.Write([]byte("zenquant-ordered-csv-parts-v1\x00"))
		for _, part := range p.SourcePartsSHA256 {
			if !validHash(part) {
				return nil, fmt.Errorf("invalid dataset provenance parts")
			}
			b, _ := hex.DecodeString(part)
			h.Write(b)
		}
		if hex.EncodeToString(h.Sum(nil)) != p.SourceSHA256 {
			return nil, fmt.Errorf("inconsistent dataset provenance parts")
		}
	}
	events, err := research.PrepareReplayEvents(map[string][]research.ReplayCandle{"4h": d.Candles}, []string{"4h"}, "4h", &startNS, &endNS)
	if err != nil {
		return nil, fmt.Errorf("prepare imported candles: %w", err)
	}
	if len(events) == 0 || events[0].CloseTimeNS != startNS || events[len(events)-1].CloseTimeNS != endNS {
		return nil, fmt.Errorf("candle coverage does not match bounds")
	}
	candles := make([]research.ReplayCandle, 0, len(events))
	for i, event := range events {
		if event.Symbol != "BTC" || time.Unix(0, event.CloseTimeNS).After(cutoff) {
			return nil, fmt.Errorf("candle identity or provenance cutoff mismatch")
		}
		if i > 0 && event.CloseTimeNS-events[i-1].CloseTimeNS != bar {
			return nil, fmt.Errorf("candle gap or duplicate within selected window")
		}
		candles = append(candles, event.ReplayCandle)
	}
	observations, err := research.CandleObservations(candles, p.SourceSymbol)
	if err != nil {
		return nil, err
	}
	return &ImportedCandleObservations{SnapshotID: snapshotID, DatasetSHA256: snapshot.SourceSHA256, Provider: d.Provider, Venue: d.Venue, Provenance: *p, HashVerification: "snapshot ID verifies stored snapshot bytes; dataset and source hashes are declarations whose association with embedded data is unverified", HistoricalArrival: "unknown", Observations: observations}, nil
}
