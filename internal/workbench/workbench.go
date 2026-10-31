// Package workbench implements an isolated, local diagnostic research store.
package workbench

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math"
	"os"
	"path/filepath"
	"regexp"
	"sync"

	"github.com/infdahai/zenQuant/internal/research"
)

const MaxInput = 1 << 20
const maxCandles = 20000

// Dataset is an imported historical dataset. Availability is unknown; event
// timestamps alone are not evidence of when observations became available.
type Dataset struct {
	Version    string                  `json:"version"`
	Provider   string                  `json:"provider"`
	Venue      string                  `json:"venue"`
	Instrument string                  `json:"instrument"`
	Timeframe  string                  `json:"timeframe"`
	Candles    []research.ReplayCandle `json:"candles"`
	Funding    []Funding               `json:"funding"`
}
type Funding struct {
	TimeNS int64   `json:"time_ns"`
	Rate   float64 `json:"rate"`
}
type Quality struct {
	Count         int    `json:"count"`
	Gaps          int    `json:"gaps"`
	FirstCloseNS  int64  `json:"first_close_ns"`
	LastCloseNS   int64  `json:"last_close_ns"`
	FundingStatus string `json:"funding_status"`
	Availability  string `json:"availability"`
}
type Snapshot struct {
	ID           string  `json:"id"`
	SourceSHA256 string  `json:"source_sha256"`
	Quality      Quality `json:"quality"`
	Dataset      Dataset `json:"dataset"`
}
type Result struct {
	ID          string                     `json:"id"`
	SnapshotID  string                     `json:"snapshot_id"`
	Engine      string                     `json:"engine"`
	Assumptions string                     `json:"assumptions"`
	FeeRate     float64                    `json:"fee_rate"`
	Reports     []research.ExecutionReport `json:"reports"`
	Episodes    []research.PositionEpisode `json:"episodes"`
	PositionQty float64                    `json:"position_qty"`
}
type Store struct {
	root   string
	worker chan struct{}
	mu     sync.Mutex
}

func Open(root string) (*Store, error) {
	if root == "" {
		return nil, errors.New("store path required")
	}
	if err := os.MkdirAll(root, 0700); err != nil {
		return nil, err
	}
	return &Store{root: root, worker: make(chan struct{}, 1)}, nil
}

var idPattern = regexp.MustCompile(`^[a-f0-9]{64}$`)

func digest(b []byte) string       { h := sha256.Sum256(b); return hex.EncodeToString(h[:]) }
func encode(v any) ([]byte, error) { return json.Marshal(v) }
func strictDecode(b []byte, v any) error {
	d := json.NewDecoder(bytes.NewReader(b))
	d.DisallowUnknownFields()
	if err := d.Decode(v); err != nil {
		return err
	}
	if err := d.Decode(new(any)); err != io.EOF {
		return errors.New("trailing JSON data")
	}
	return nil
}
func (s *Store) path(kind, id string) (string, error) {
	if !idPattern.MatchString(id) {
		return "", errors.New("invalid identifier")
	}
	return filepath.Join(s.root, kind+"-"+id+".json"), nil
}

// publish writes and syncs a temporary file before an atomic no-replace link.
// Existing content must match; a crash can leave only an unreferenced temp file.
func (s *Store) publish(kind, id string, b []byte) error {
	p, err := s.path(kind, id)
	if err != nil {
		return err
	}
	f, err := os.CreateTemp(s.root, ".publish-")
	if err != nil {
		return err
	}
	tmp := f.Name()
	defer os.Remove(tmp)
	if err = f.Chmod(0600); err == nil {
		_, err = f.Write(b)
	}
	if err == nil {
		err = f.Sync()
	}
	closeErr := f.Close()
	if err == nil {
		err = closeErr
	}
	if err != nil {
		return err
	}
	if err = os.Link(tmp, p); err != nil {
		if !os.IsExist(err) {
			return err
		}
		existing, e := os.ReadFile(p)
		if e != nil {
			return e
		}
		if !bytes.Equal(existing, b) {
			return errors.New("existing artifact differs")
		}
		return nil
	}
	dir, err := os.Open(s.root)
	if err != nil {
		return err
	}
	defer dir.Close()
	return dir.Sync()
}
func (s *Store) read(kind, id string, v any) error {
	p, err := s.path(kind, id)
	if err != nil {
		return err
	}
	b, err := os.ReadFile(p)
	if err != nil {
		return err
	}
	if len(b) > MaxInput*4 {
		return errors.New("artifact too large")
	}
	if digest(b) != id {
		return errors.New("artifact integrity check failed")
	}
	return strictDecode(b, v)
}
func (s *Store) Import(raw []byte) (Snapshot, error) {
	var out Snapshot
	if len(raw) > MaxInput {
		return out, errors.New("input exceeds 1 MiB")
	}
	var d Dataset
	if err := strictDecode(raw, &d); err != nil {
		return out, err
	}
	if d.Version != "dataset-v1" || d.Provider == "" || d.Venue == "" || d.Instrument != "BTC" || len(d.Candles) == 0 || len(d.Candles) > maxCandles {
		return out, errors.New("invalid dataset metadata or candle count")
	}
	duration := map[string]int64{"5m": 300e9, "1h": 3600e9, "4h": 14400e9, "1d": 86400e9}[d.Timeframe]
	if duration == 0 {
		return out, errors.New("unsupported timeframe")
	}
	if _, err := research.PrepareReplayEvents(map[string][]research.ReplayCandle{d.Timeframe: d.Candles}, []string{d.Timeframe}, d.Timeframe, nil, nil); err != nil {
		return out, err
	}
	q := Quality{Count: len(d.Candles), FirstCloseNS: d.Candles[0].CloseTimeNS, LastCloseNS: d.Candles[len(d.Candles)-1].CloseTimeNS, FundingStatus: "coverage unknown; missing rows are not inferred", Availability: "unknown historical arrival time"}
	if len(d.Funding) == 0 {
		q.FundingStatus = "no funding observations supplied"
	}
	for i, c := range d.Candles {
		if c.Symbol != d.Instrument {
			return out, errors.New("instrument mismatch")
		}
		if i > 0 {
			delta := c.OpenTimeNS - d.Candles[i-1].OpenTimeNS
			if delta <= 0 {
				return out, errors.New("duplicate or unordered candles")
			}
			if delta != duration {
				q.Gaps++
			}
		}
	}
	for i, f := range d.Funding {
		if math.IsNaN(f.Rate) || math.IsInf(f.Rate, 0) || f.TimeNS <= 0 || (i > 0 && f.TimeNS <= d.Funding[i-1].TimeNS) {
			return out, errors.New("invalid or unordered funding")
		}
	}
	out = Snapshot{SourceSHA256: digest(raw), Quality: q, Dataset: d}
	b, err := encode(out)
	if err != nil {
		return out, err
	}
	id := digest(b)
	if err = s.publish("snapshot", id, b); err != nil {
		return Snapshot{}, err
	}
	out.ID = id
	return out, nil
}
func (s *Store) Snapshot(id string) (Snapshot, error) {
	var x Snapshot
	err := s.read("snapshot", id, &x)
	if err == nil {
		x.ID = id
	}
	return x, err
}
func (s *Store) Result(id string) (Result, error) {
	var x Result
	err := s.read("result", id, &x)
	if err == nil {
		x.ID = id
	}
	return x, err
}
func (s *Store) List(kind string) ([]string, error) {
	if kind != "snapshot" && kind != "result" {
		return nil, errors.New("invalid artifact type")
	}
	entries, err := os.ReadDir(s.root)
	if err != nil {
		return nil, err
	}
	ids := []string{}
	for _, e := range entries {
		n := e.Name()
		prefix := kind + "-"
		if len(n) == len(prefix)+64+5 && n[:len(prefix)] == prefix && n[len(n)-5:] == ".json" {
			id := n[len(prefix) : len(n)-5]
			if idPattern.MatchString(id) {
				ids = append(ids, id)
			}
		}
	}
	return ids, nil
}

// Run executes a fixed buy-first/sell-last diagnostic. The policy has no
// predictive or trading authority. The queue is bounded to one active run.
func (s *Store) Run(ctx context.Context, id string, fee float64) (Result, error) {
	var out Result
	select {
	case s.worker <- struct{}{}:
		defer func() { <-s.worker }()
	default:
		return out, errors.New("research worker busy")
	}
	if math.IsNaN(fee) || math.IsInf(fee, 0) || fee < 0 || fee > 0.1 {
		return out, errors.New("fee rate must be between 0 and 0.1")
	}
	snap, err := s.Snapshot(id)
	if err != nil {
		return out, err
	}
	d := snap.Dataset
	if len(d.Candles) < 2 {
		return out, errors.New("at least two candles required")
	}
	if snap.Quality.Gaps > 0 {
		return out, errors.New("dataset has gaps")
	}
	funding := make([]research.ReplayFundingRow, len(d.Funding))
	for i, f := range d.Funding {
		funding[i] = research.ReplayFundingRow{FundingTimeNS: f.TimeNS, FundingRate: f.Rate}
	}
	cfg := research.OfflineReplayConfig{Symbol: d.Instrument, Candles: map[string][]research.ReplayCandle{d.Timeframe: d.Candles}, Timeframes: []string{d.Timeframe}, ExecutionTimeframe: d.Timeframe, Funding: funding, TakerFeeRate: fee, HistoryLimit: 2, IndicatorWindow: 1}
	count := 0
	replay, err := research.RunOfflineReplay(cfg, func(x research.OfflineReplaySnapshot) ([]research.OfflineExecutionOrder, error) {
		if err := ctx.Err(); err != nil {
			return nil, err
		}
		count++
		side := 0
		if count == 1 {
			side = 1
		}
		if count == len(d.Candles) {
			side = -1
		}
		if side == 0 {
			return nil, nil
		}
		return []research.OfflineExecutionOrder{{Symbol: d.Instrument, Side: side, Quantity: 1}}, nil
	})
	if err != nil {
		return out, err
	}
	if err = ctx.Err(); err != nil {
		return out, err
	}
	out = Result{SnapshotID: id, Engine: "fixed-buy-first-sell-last-v1", Assumptions: "Synthetic same-close full fills, no slippage; only the latest visible funding row is charged per bar. Historical availability unknown. Diagnostic only; no live parity or profit claim.", FeeRate: fee, Reports: replay.Reports, Episodes: replay.Episodes, PositionQty: replay.PositionQty}
	b, err := encode(out)
	if err != nil {
		return Result{}, err
	}
	rid := digest(b)
	s.mu.Lock()
	defer s.mu.Unlock()
	if err = s.publish("result", rid, b); err != nil {
		return Result{}, err
	}
	out.ID = rid
	return out, nil
}
func Compare(a, b Result) (string, error) {
	if len(a.Episodes) != 1 || len(b.Episodes) != 1 {
		return "", fmt.Errorf("comparison requires one episode per result")
	}
	if a.SnapshotID != b.SnapshotID || a.Engine != b.Engine || a.Assumptions != b.Assumptions {
		return "", errors.New("comparison requires the same snapshot and engine assumptions")
	}
	return fmt.Sprintf("Same snapshot; fee rates %.6g vs %.6g. Net P&L: %.8f vs %.8f; difference %.8f", a.FeeRate, b.FeeRate, a.Episodes[0].NetPnL, b.Episodes[0].NetPnL, b.Episodes[0].NetPnL-a.Episodes[0].NetPnL), nil
}
