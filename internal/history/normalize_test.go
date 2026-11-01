package history

import (
	"archive/zip"
	"bytes"
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"hash/crc32"
	"os"
	"reflect"
	"strings"
	"testing"
	"time"

	"github.com/infdahai/zenQuant/internal/research"
	"github.com/infdahai/zenQuant/internal/workbench"
)

func TestZIPCSV(t *testing.T) {
	makeZIP := func(headers []zip.FileHeader, content []byte) []byte {
		t.Helper()
		var buf bytes.Buffer
		w := zip.NewWriter(&buf)
		for _, h := range headers {
			f, err := w.CreateHeader(&h)
			if err != nil {
				t.Fatal(err)
			}
			if _, err := f.Write(content); err != nil {
				t.Fatal(err)
			}
		}
		if err := w.Close(); err != nil {
			t.Fatal(err)
		}
		return buf.Bytes()
	}
	csv, err := os.ReadFile("testdata/klines.csv")
	if err != nil {
		t.Fatal(err)
	}
	header := zip.FileHeader{Name: "klines.csv", Method: zip.Store}
	valid := makeZIP([]zip.FileHeader{header}, csv)
	got, err := ZIPCSV(valid)
	if err != nil || !bytes.Equal(got, csv) {
		t.Fatalf("valid ZIP: %v", err)
	}
	corrupt := bytes.Clone(valid)
	index := bytes.Index(corrupt, csv)
	if index < 0 {
		t.Fatal("missing stored bytes")
	}
	corrupt[index] ^= 1
	symlink := zip.FileHeader{Name: "link.csv"}
	symlink.SetMode(os.ModeSymlink | 0777)
	directory := zip.FileHeader{Name: "dir.csv"}
	directory.SetMode(os.ModeDir | 0755)
	cases := map[string][]byte{
		"corrupt": corrupt, "truncated": valid[:len(valid)-10], "empty": makeZIP(nil, nil),
		"multiple":        makeZIP([]zip.FileHeader{header, header}, csv),
		"traversal":       makeZIP([]zip.FileHeader{{Name: "../file.csv"}}, csv),
		"nested":          makeZIP([]zip.FileHeader{{Name: "dir/file.csv"}}, csv),
		"backslash":       makeZIP([]zip.FileHeader{{Name: `dir\file.csv`}}, csv),
		"wrong extension": makeZIP([]zip.FileHeader{{Name: "file.txt"}}, csv),
		"symlink":         makeZIP([]zip.FileHeader{symlink}, csv), "directory": makeZIP([]zip.FileHeader{directory}, csv),
		"oversize archive":   make([]byte, MaxCSV+1),
		"oversize extracted": makeZIP([]zip.FileHeader{{Name: "file.csv", Method: zip.Deflate}}, bytes.Repeat([]byte("a"), MaxCSV+1)),
	}
	for name, data := range cases {
		t.Run(name, func(t *testing.T) {
			if _, err := ZIPCSV(data); err == nil {
				t.Fatal("accepted invalid ZIP")
			}
		})
	}
}

func TestZIPCSVRejectsZeroCRCWithoutDescriptor(t *testing.T) {
	content := []byte("corrupted CSV payload")
	var buf bytes.Buffer
	w := zip.NewWriter(&buf)
	member, err := w.CreateRaw(&zip.FileHeader{
		Name: "klines.csv", Method: zip.Store,
		CRC32: 0, CompressedSize64: uint64(len(content)), UncompressedSize64: uint64(len(content)),
	})
	if err != nil {
		t.Fatal(err)
	}
	if _, err = member.Write(content); err != nil {
		t.Fatal(err)
	}
	if err = w.Close(); err != nil {
		t.Fatal(err)
	}
	if crc32.ChecksumIEEE(content) == 0 {
		t.Fatal("fixture must have nonzero CRC")
	}
	archive, err := zip.NewReader(bytes.NewReader(buf.Bytes()), int64(buf.Len()))
	if err != nil {
		t.Fatal(err)
	}
	if archive.File[0].Flags&8 != 0 || archive.File[0].CRC32 != 0 {
		t.Fatal("fixture requires no descriptor and zero CRC")
	}
	if _, err = ZIPCSV(buf.Bytes()); err == nil {
		t.Fatal("accepted mismatched zero CRC")
	}
}

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
