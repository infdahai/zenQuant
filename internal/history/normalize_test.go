package history

import (
	"bytes"
	"encoding/json"
	"os"
	"reflect"
	"strings"
	"testing"
	"time"

	"github.com/infdahai/zenQuant/internal/research"
	"github.com/infdahai/zenQuant/internal/workbench"
)

func TestPythonParityAndReplay(t *testing.T) {
	csv, err := os.ReadFile("testdata/klines.csv")
	if err != nil {
		t.Fatal(err)
	}
	cutoff := time.Date(2024, 1, 1, 8, 0, 0, 0, time.UTC)
	b, err := Normalize(bytes.NewReader(csv), "4h", cutoff)
	if err != nil {
		t.Fatal(err)
	}
	again, err := Normalize(bytes.NewReader(csv), "4h", cutoff)
	if err != nil || !bytes.Equal(b, again) {
		t.Fatal("nondeterministic output", err)
	}
	var d workbench.Dataset
	if err = json.Unmarshal(b, &d); err != nil {
		t.Fatal(err)
	}
	fixture, err := os.ReadFile("testdata/python_klines.json")
	if err != nil {
		t.Fatal(err)
	}
	var expected []research.ReplayCandle
	if err = json.Unmarshal(fixture, &expected); err != nil {
		t.Fatal(err)
	}
	for i := range expected {
		if expected[i].Symbol != "BTCUSDT" {
			t.Fatal("fixture symbol")
		}
		expected[i].Symbol = "BTC"
	}
	if !reflect.DeepEqual(d.Candles, expected) {
		t.Fatalf("Python normalization mismatch: %#v %#v", d.Candles, expected)
	}
	store, err := workbench.Open(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	snap, err := store.Import(b)
	if err != nil {
		t.Fatal(err)
	}
	if snap.Quality.Count != 2 || snap.Quality.Gaps != 0 {
		t.Fatalf("quality: %+v", snap.Quality)
	}
	before := d.Candles[0].CloseTimeNS - 1
	events, err := research.PrepareReplayEvents(map[string][]research.ReplayCandle{"4h": d.Candles}, []string{"4h"}, "4h", nil, &before)
	if err != nil || len(events) != 0 {
		t.Fatalf("premature visibility: %v %v", events, err)
	}
	at := before + 1
	events, err = research.PrepareReplayEvents(map[string][]research.ReplayCandle{"4h": d.Candles}, []string{"4h"}, "4h", nil, &at)
	if err != nil || len(events) != 1 {
		t.Fatalf("close visibility: %v %v", events, err)
	}
}
func TestRejectInvalid(t *testing.T) {
	row := "1704067200000,100.5,105,99,103,12.25,1704081599999,1250,17,5,510,0\n"
	cases := map[string]string{"empty": "", "header": "open,high\n", "columns": strings.TrimSpace(row) + ",1\n", "duplicate": row + row, "unordered": strings.Replace(row, "1704067200000", "1704081600000", 1) + row, "close": strings.Replace(row, "1704081599999", "1704081599998", 1), "misaligned": strings.Replace(row, "1704067200000", "1704067200001", 1), "nan": strings.Replace(row, "100.5", "NaN", 1), "ohlc": strings.Replace(row, "105", "98", 1), "trades": strings.Replace(row, ",17,", ",-1,", 1), "oversize": strings.Repeat("x", MaxCSV+1)}
	for name, input := range cases {
		t.Run(name, func(t *testing.T) {
			if _, err := Normalize(strings.NewReader(input), "4h", time.Date(2024, 1, 2, 0, 0, 0, 0, time.UTC)); err == nil {
				t.Fatal("accepted invalid input")
			}
		})
	}
	if _, err := Normalize(strings.NewReader(row), "4h", time.Date(2024, 1, 1, 3, 59, 59, 999999999, time.UTC)); err == nil {
		t.Fatal("accepted future close")
	}
}
