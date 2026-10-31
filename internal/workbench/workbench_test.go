package workbench

import (
	"context"
	"encoding/json"
	"math"
	"os"
	"path/filepath"
	"testing"

	"github.com/infdahai/zenQuant/internal/research"
)

func fixture(t *testing.T) []byte {
	t.Helper()
	d := Dataset{Version: "dataset-v1", Provider: "fixture", Venue: "test", Instrument: "BTC", Timeframe: "5m", Candles: []research.ReplayCandle{{Symbol: "BTC", Timeframe: "5m", OpenTimeNS: 0, CloseTimeNS: 300e9, Open: 100, High: 100, Low: 100, Close: 100, Volume: 1}, {Symbol: "BTC", Timeframe: "5m", OpenTimeNS: 300e9, CloseTimeNS: 600e9, Open: 110, High: 110, Low: 110, Close: 110, Volume: 1}}, Funding: []Funding{{TimeNS: 400e9, Rate: .01}}}
	b, err := json.Marshal(d)
	if err != nil {
		t.Fatal(err)
	}
	return b
}
func TestImportRunReopenCompare(t *testing.T) {
	dir := t.TempDir()
	s, err := Open(dir)
	if err != nil {
		t.Fatal(err)
	}
	snap, err := s.Import(fixture(t))
	if err != nil {
		t.Fatal(err)
	}
	same, err := s.Import(fixture(t))
	if err != nil || same.ID != snap.ID {
		t.Fatalf("snapshot identity: %+v %v", same, err)
	}
	r, err := s.run(context.Background(), snap.ID, .001)
	if err != nil {
		t.Fatal(err)
	}
	if len(r.Episodes) != 1 {
		t.Fatalf("episodes: %+v", r)
	}
	e := r.Episodes[0] // Independent cash reconciliation: -100 - .1 + 110 - .11 - 1.1 = 8.69.
	if math.Abs(e.NetPnL-8.69) > 1e-9 || math.Abs(e.FundingPnL+1.1) > 1e-9 || math.Abs(e.Fees-.21) > 1e-9 {
		t.Fatalf("accounting: %+v", e)
	}
	reopened, err := Open(dir)
	if err != nil {
		t.Fatal(err)
	}
	got, err := reopened.Result(r.ID)
	if err != nil || got.ID != r.ID {
		t.Fatalf("reopen: %+v %v", got, err)
	}
	if _, err = Compare(r, got); err != nil {
		t.Fatal(err)
	}
	if err = os.WriteFile(filepath.Join(dir, "result-"+r.ID+".json"), []byte("{}"), 0600); err != nil {
		t.Fatal(err)
	}
	if _, err = reopened.Result(r.ID); err == nil {
		t.Fatal("corruption accepted")
	}
}
func TestImportRejectsInvalidAndRunCancellation(t *testing.T) {
	s, _ := Open(t.TempDir())
	var d Dataset
	if err := json.Unmarshal(fixture(t), &d); err != nil {
		t.Fatal(err)
	}
	d.Candles[1] = d.Candles[0]
	b, _ := json.Marshal(d)
	if _, err := s.Import(b); err == nil {
		t.Fatal("duplicate accepted")
	}
	snap, err := s.Import(fixture(t))
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if _, err = s.run(ctx, snap.ID, 0); err == nil {
		t.Fatal("cancelled run accepted")
	}
	ids, err := s.List("result")
	if err != nil || len(ids) != 0 {
		t.Fatalf("partial result: %v %v", ids, err)
	}
}
func TestComparisonRejectsDifferentSnapshots(t *testing.T) {
	a := Result{SnapshotID: "a", Engine: "v1", Episodes: []research.PositionEpisode{{}}}
	b := a
	b.SnapshotID = "b"
	if _, err := Compare(a, b); err == nil {
		t.Fatal("different snapshots compared")
	}
}

func TestGapIsVisibleAndBlocksRun(t *testing.T) {
	s, _ := Open(t.TempDir())
	var d Dataset
	_ = json.Unmarshal(fixture(t), &d)
	d.Candles[1].OpenTimeNS += 300e9
	d.Candles[1].CloseTimeNS += 300e9
	b, _ := json.Marshal(d)
	snap, err := s.Import(b)
	if err != nil || snap.Quality.Gaps != 1 {
		t.Fatalf("quality: %+v %v", snap, err)
	}
	if _, err = s.run(context.Background(), snap.ID, 0); err == nil {
		t.Fatal("gap accepted")
	}
}
