package workbench

import (
	"context"
	"encoding/json"
	"errors"
	"math"
	"os"
	"path/filepath"
	"reflect"
	"strings"
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
func TestExistingPublicationRetriesDirectorySync(t *testing.T) {
	s, _ := Open(t.TempDir())
	calls := 0
	s.publishHook = func(string) error { calls++; return errors.New("injected sync failure") }
	if _, err := s.Import(fixture(t)); err == nil {
		t.Fatal("first sync accepted")
	}
	if _, err := s.Import(fixture(t)); err == nil {
		t.Fatal("existing link acknowledged without sync")
	}
	if calls != 2 {
		t.Fatalf("sync attempts: %d", calls)
	}
	s.publishHook = nil
	snap, err := s.Import(fixture(t))
	if err != nil {
		t.Fatal(err)
	}
	reopened, _ := Open(s.root)
	if _, err = reopened.Snapshot(snap.ID); err != nil {
		t.Fatal(err)
	}
}
func TestMetadataExpansionRejectedBeforePublish(t *testing.T) {
	s, _ := Open(t.TempDir())
	var d Dataset
	_ = json.Unmarshal(fixture(t), &d)
	d.Provider = strings.Repeat("<", 800000)
	raw, _ := json.Marshal(d)
	if _, err := s.Import(raw); err == nil {
		t.Fatal("oversized metadata accepted")
	}
	ids, err := s.List("snapshot")
	if err != nil || len(ids) != 0 {
		t.Fatalf("published: %v %v", ids, err)
	}
	d.Provider = strings.Repeat("<", 256)
	raw, _ = json.Marshal(d)
	snap, err := s.Import(raw)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = s.Snapshot(snap.ID); err != nil {
		t.Fatal(err)
	}
}
func TestTimestampResponsePreservesNanoseconds(t *testing.T) {
	s, _ := Open(t.TempDir())
	var d Dataset
	_ = json.Unmarshal(fixture(t), &d)
	const start int64 = 1700000100000000000
	for i := range d.Candles {
		d.Candles[i].OpenTimeNS = start + int64(i)*300e9
		d.Candles[i].CloseTimeNS = d.Candles[i].OpenTimeNS + 300e9 - 1
	}
	d.Funding = nil
	raw, _ := json.Marshal(d)
	b, err := s.API("/api/research/import", raw)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(b), `"close_time_ns":"1700000399999999999"`) {
		t.Fatalf("timestamp lost: %s", b)
	}
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
	if len(r.Reports) != 2 || r.Reports[0].ClientOrderID != "diagnostic-1" || r.Reports[1].ClientOrderID != "diagnostic-2" {
		t.Fatalf("attempt identities: %+v", r.Reports)
	}
	repeated, err := s.run(context.Background(), snap.ID, .001)
	if err != nil || !reflect.DeepEqual(r, repeated) {
		t.Fatalf("ephemeral replay differs: %+v %v", repeated, err)
	}
	e := r.Episodes[0] // Independent cash reconciliation: -100 - .1 + 110 - .11 - 1.1 = 8.69.
	if math.Abs(e.NetPnL-8.69) > 1e-9 || math.Abs(e.FundingPnL+1.1) > 1e-9 || math.Abs(e.Fees-.21) > 1e-9 {
		t.Fatalf("accounting: %+v", e)
	}
	b, _ := encode(r)
	r.ID = digest(b)
	if err := s.publish("result", r.ID, b); err != nil {
		t.Fatal(err)
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
