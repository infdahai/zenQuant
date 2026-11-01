package history

import (
	"bytes"
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"os"
	"reflect"
	"strings"
	"testing"
	"time"

	"github.com/infdahai/zenQuant/internal/research"
	"github.com/infdahai/zenQuant/internal/workbench"
)

func TestSyntheticPythonCandleNormalizationAndCloseVisibility(t *testing.T) {
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
	if snap.Quality.Count != 2 || snap.Quality.Gaps != 0 || snap.Quality.CandleCoverage != "unknown; no requested coverage verified" {
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
func TestTruncatedCoverageAndProvenanceValidation(t *testing.T) {
	row := "1704067200000,100.5,105,99,103,12.25,1704081599999,1250,17,5,510,0\n"
	b, err := Normalize(strings.NewReader(row), "4h", time.Date(2024, 1, 2, 0, 0, 0, 0, time.UTC))
	if err != nil {
		t.Fatal(err)
	}
	store, err := workbench.Open(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	snap, err := store.Import(b)
	if err != nil {
		t.Fatal(err)
	}
	if snap.Quality.Gaps != 0 || snap.Quality.CandleCoverage != "unknown; no requested coverage verified" {
		t.Fatalf("truncated coverage: %+v", snap.Quality)
	}
	var d workbench.Dataset
	if err = json.Unmarshal(b, &d); err != nil {
		t.Fatal(err)
	}
	for _, mutate := range []func(*workbench.Dataset){
		func(d *workbench.Dataset) { d.Provenance.AsOf = "2024-01-01T03:59:59Z" },
		func(d *workbench.Dataset) { d.Provenance.SourceSHA256 = "invalid" },
		func(d *workbench.Dataset) { d.Provenance.SourceSymbol = "ETHUSDT" },
	} {
		var copy workbench.Dataset
		if err = json.Unmarshal(b, &copy); err != nil {
			t.Fatal(err)
		}
		mutate(&copy)
		bad, err := json.Marshal(copy)
		if err != nil {
			t.Fatal(err)
		}
		if _, err = store.Import(bad); err == nil {
			t.Fatal("invalid provenance accepted")
		}
	}
}

func TestRejectUnrepresentableUTCCutoff(t *testing.T) {
	for _, input := range []string{"9999-12-31T23:00:00-02:00", "0000-01-01T00:00:00+01:00"} {
		cutoff, err := time.Parse(time.RFC3339, input)
		if err != nil {
			t.Fatal(err)
		}
		if _, err := Normalize(strings.NewReader(""), "4h", cutoff); err == nil || !strings.Contains(err.Error(), "UTC RFC3339") {
			t.Fatalf("cutoff %s: expected UTC representation error, got %v", input, err)
		}
	}
}

func TestFundingNormalization(t *testing.T) {
	csv, err := os.ReadFile("testdata/klines.csv")
	if err != nil {
		t.Fatal(err)
	}
	funding, err := os.ReadFile("testdata/funding.json")
	if err != nil {
		t.Fatal(err)
	}
	cutoff := time.Date(2024, 1, 1, 8, 0, 0, 0, time.UTC)
	normalize := func(raw []byte) ([]byte, error) { return NormalizeWithFunding(bytes.NewReader(csv), "4h", cutoff, raw) }
	b, err := normalize(funding)
	if err != nil {
		t.Fatal(err)
	}
	var d workbench.Dataset
	if err := json.Unmarshal(b, &d); err != nil {
		t.Fatal(err)
	}
	reference, err := os.ReadFile("testdata/python_funding.json")
	if err != nil {
		t.Fatal(err)
	}
	var python []struct {
		Symbol string  `json:"symbol"`
		Time   int64   `json:"funding_time_ns"`
		Rate   float64 `json:"funding_rate"`
	}
	if err := json.Unmarshal(reference, &python); err != nil {
		t.Fatal(err)
	}
	if len(python) != len(d.Funding) {
		t.Fatal("funding count mismatch")
	}
	for i, row := range python {
		if row.Symbol != "BTCUSDT" || d.Funding[i] != (workbench.Funding{TimeNS: row.Time, Rate: row.Rate}) {
			t.Fatalf("Python mismatch: %+v %+v", row, d.Funding[i])
		}
	}
	sum := sha256.Sum256(funding)
	if d.Provenance.FundingSourceSHA256 != fmt.Sprintf("%x", sum) {
		t.Fatal("incorrect funding digest")
	}
	store, err := workbench.Open(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	snap, err := store.Import(b)
	if err != nil {
		t.Fatal(err)
	}
	variant, err := normalize(append(append([]byte{}, funding...), '\n'))
	if err != nil {
		t.Fatal(err)
	}
	other, err := store.Import(variant)
	if err != nil || other.ID == snap.ID {
		t.Fatalf("funding identity: %v", err)
	}
	bad := d
	bad.Provenance = &workbench.DatasetProvenance{}
	*bad.Provenance = *d.Provenance
	bad.Provenance.FundingSourceSHA256 = "bad"
	invalid, _ := json.Marshal(bad)
	if _, err := store.Import(invalid); err == nil {
		t.Fatal("accepted invalid funding digest")
	}
	bad.Provenance.FundingSourceSHA256 = d.Provenance.FundingSourceSHA256
	bad.Funding = append([]workbench.Funding{}, d.Funding...)
	bad.Funding[1].TimeNS++
	invalid, _ = json.Marshal(bad)
	if _, err := store.Import(invalid); err == nil {
		t.Fatal("accepted future funding")
	}
	for name, input := range map[string]string{
		"empty": "", "null": "null", "object": `{}`, "symbol": `[{"symbol":"ETHUSDT","fundingTime":1,"fundingRate":"0"}]`,
		"zero": `[{"symbol":"BTCUSDT","fundingTime":0,"fundingRate":"0"}]`, "overflow": `[{"symbol":"BTCUSDT","fundingTime":9223372036855,"fundingRate":"0"}]`,
		"future": `[{"symbol":"BTCUSDT","fundingTime":1704096000001,"fundingRate":"0"}]`, "nan": `[{"symbol":"BTCUSDT","fundingTime":1,"fundingRate":"NaN"}]`,
		"missing": `[{"symbol":"BTCUSDT","fundingTime":1}]`, "duplicate": `[{"symbol":"BTCUSDT","fundingTime":1,"fundingRate":"0"},{"symbol":"BTCUSDT","fundingTime":1,"fundingRate":"0"}]`,
		"unordered": `[{"symbol":"BTCUSDT","fundingTime":2,"fundingRate":"0"},{"symbol":"BTCUSDT","fundingTime":1,"fundingRate":"0"}]`,
	} {
		t.Run(name, func(t *testing.T) {
			if _, err := normalize([]byte(input)); err == nil {
				t.Fatal("accepted invalid funding")
			}
		})
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
