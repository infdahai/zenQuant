package history

import (
	"bytes"
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
	"time"

	"github.com/infdahai/zenQuant/internal/workbench"
)

func TestObserveImportedCandles(t *testing.T) {
	const bar int64 = int64(4 * time.Hour)
	const start int64 = 1704081600000000000
	var csv bytes.Buffer
	for i := 0; i < 25; i++ {
		openMS := (start + int64(i)*bar - bar) / 1e6
		fmt.Fprintf(&csv, "%d,100,110,90,%d,10,%d,1000,1,5,500,0\n", openMS, 100+i, openMS+bar/1e6-1)
	}
	// Keep prices inside the OHLC range.
	rawLines := bytes.Split(bytes.TrimSpace(csv.Bytes()), []byte("\n"))
	// Use a larger high for the synthetic rising closes.
	for i := range rawLines {
		rawLines[i] = bytes.Replace(rawLines[i], []byte(",110,90,"), []byte(",200,90,"), 1)
	}
	raw, err := NormalizePartsWithFunding([]io.Reader{bytes.NewReader(bytes.Join(rawLines[:12], []byte("\n"))), bytes.NewReader(bytes.Join(rawLines[12:], []byte("\n")))}, "4h", time.Unix(0, start+25*bar).UTC(), nil)
	if err != nil {
		t.Fatal(err)
	}
	root := t.TempDir()
	store, err := workbench.Open(root)
	if err != nil {
		t.Fatal(err)
	}
	snap, err := store.Import(raw)
	if err != nil {
		t.Fatal(err)
	}
	result, err := ObserveImportedCandles(store, snap.ID, start+2*bar, start+23*bar)
	if err != nil {
		t.Fatal(err)
	}
	if len(result.Observations) != 22 || result.Observations[0].CutoffTimeNS != start+2*bar || result.Observations[21].CutoffTimeNS != start+23*bar {
		t.Fatal("inclusive bounds", result)
	}
	if result.Observations[0].ATRReady || result.Observations[0].HistoricalArrivalTimeNS != nil || !result.Observations[20].ATRReady || !result.Observations[20].OUKappaReady || result.HistoricalArrival != "unknown" {
		t.Fatal("cold start or arrival", result.Observations)
	}
	if result.DatasetSHA256 != snap.SourceSHA256 || result.SnapshotID != snap.ID || !reflect.DeepEqual(result.Provenance, *snap.Dataset.Provenance) || len(result.Provenance.SourcePartsSHA256) != 2 {
		t.Fatal("identity mismatch")
	}
	for _, bounds := range [][2]int64{{start - bar, start}, {start, start + 25*bar}, {start + 1, start + bar}, {start + bar, start}} {
		if _, err := ObserveImportedCandles(store, snap.ID, bounds[0], bounds[1]); err == nil {
			t.Fatalf("accepted bounds %v", bounds)
		}
	}
	var d workbench.Dataset
	if err = json.Unmarshal(raw, &d); err != nil {
		t.Fatal(err)
	}
	d.Candles = append(d.Candles[:1], d.Candles[2:]...)
	gapRaw, _ := json.Marshal(d)
	gap, err := store.Import(gapRaw)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = ObserveImportedCandles(store, gap.ID, start, start+3*bar); err == nil {
		t.Fatal("accepted internal gap")
	}
	if _, err = ObserveImportedCandles(store, gap.ID, start+2*bar, start+3*bar); err != nil {
		t.Fatalf("outside gap: %v", err)
	}
	d.Provenance = nil
	noProvRaw, _ := json.Marshal(d)
	noProv, err := store.Import(noProvRaw)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = ObserveImportedCandles(store, noProv.ID, start+2*bar, start+3*bar); err == nil {
		t.Fatal("accepted missing provenance")
	}
	// A self-consistent artifact ID must still reject altered provenance and cutoff.
	for _, mutate := range []func(*workbench.Snapshot){
		func(s *workbench.Snapshot) {
			s.Dataset.Provenance.SourcePartsSHA256[0] = s.Dataset.Provenance.SourcePartsSHA256[1]
		},
		func(s *workbench.Snapshot) {
			s.Dataset.Provenance.AsOf = time.Unix(0, start).UTC().Format(time.RFC3339Nano)
		},
	} {
		storedBytes, err := os.ReadFile(filepath.Join(root, "snapshot-"+snap.ID+".json"))
		if err != nil {
			t.Fatal(err)
		}
		var altered workbench.Snapshot
		if err := json.Unmarshal(storedBytes, &altered); err != nil {
			t.Fatal(err)
		}
		mutate(&altered)
		storedBytes, err = json.Marshal(altered)
		if err != nil {
			t.Fatal(err)
		}
		id := fmt.Sprintf("%x", sha256.Sum256(storedBytes))
		if err := os.WriteFile(filepath.Join(root, "snapshot-"+id+".json"), storedBytes, 0600); err != nil {
			t.Fatal(err)
		}
		if _, err := ObserveImportedCandles(store, id, start+2*bar, start+3*bar); err == nil {
			t.Fatal("accepted altered provenance")
		}
	}
	storedBytes, err := os.ReadFile(filepath.Join(root, "snapshot-"+snap.ID+".json"))
	if err != nil {
		t.Fatal(err)
	}
	var rewritten workbench.Snapshot
	if err := json.Unmarshal(storedBytes, &rewritten); err != nil {
		t.Fatal(err)
	}
	// Original input bytes are absent: a rehashed snapshot can declare any valid dataset hash.
	rewritten.SourceSHA256 = strings.Repeat("a", 64)
	storedBytes, err = json.Marshal(rewritten)
	if err != nil {
		t.Fatal(err)
	}
	id := fmt.Sprintf("%x", sha256.Sum256(storedBytes))
	if err := os.WriteFile(filepath.Join(root, "snapshot-"+id+".json"), storedBytes, 0600); err != nil {
		t.Fatal(err)
	}
	declared, err := ObserveImportedCandles(store, id, start, start)
	if err != nil || declared.DatasetSHA256 != rewritten.SourceSHA256 || !strings.Contains(declared.HashVerification, "unverified") || !strings.Contains(declared.HashVerification, "snapshot ID verifies") {
		t.Fatalf("incorrect hash verification claim: %+v %v", declared, err)
	}
	if err = os.WriteFile(filepath.Join(root, "snapshot-"+snap.ID+".json"), []byte("{}"), 0600); err != nil {
		t.Fatal(err)
	}
	if _, err = ObserveImportedCandles(store, snap.ID, start, start); err == nil {
		t.Fatal("accepted tampered snapshot")
	}
}
