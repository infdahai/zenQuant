package cli

import (
	"bytes"
	"encoding/json"
	"io"
	"os"
	"testing"
	"time"

	"github.com/infdahai/zenQuant/internal/history"
	"github.com/infdahai/zenQuant/internal/workbench"
)

func TestHistoryCandleObservationsCLI(t *testing.T) {
	f, err := os.Open("../history/testdata/klines.csv")
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	raw, err := history.NormalizePartsWithFunding([]io.Reader{f}, "4h", time.Date(2024, 1, 2, 0, 0, 0, 0, time.UTC), nil)
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
	args := []string{"history-candle-observations", "--store", root, "--snapshot", snap.ID, "--start-ns", "1704081600000000000", "--end-ns", "1704096000000000000"}
	var out, stderr bytes.Buffer
	if err = Run(args, &out, &stderr); err != nil {
		t.Fatal(err)
	}
	var result history.ImportedCandleObservations
	if err = json.Unmarshal(out.Bytes(), &result); err != nil || len(result.Observations) != 2 || result.HistoricalArrival != "unknown" {
		t.Fatalf("invalid JSON: %s %v", out.String(), err)
	}
	for _, bad := range [][]string{{"history-candle-observations"}, append(append([]string{}, args...), "extra"), {"history-candle-observations", "--store", root, "--snapshot", snap.ID, "--start-ns", "bad"}} {
		out.Reset()
		if err = Run(bad, &out, &stderr); err == nil || out.Len() != 0 {
			t.Fatalf("accepted invalid args %v", bad)
		}
	}
}
