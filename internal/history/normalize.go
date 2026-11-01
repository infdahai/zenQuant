// Package history normalizes supplied Binance futures kline CSV for the local workbench.
package history

import (
	"archive/zip"
	"bytes"
	"crypto/sha256"
	"encoding/csv"
	"encoding/json"
	"fmt"
	"hash/crc32"
	"io"
	"math"
	"path"
	"strconv"
	"strings"
	"time"

	"github.com/infdahai/zenQuant/internal/research"
	"github.com/infdahai/zenQuant/internal/workbench"
)

const MaxCSV = 4 << 20

// ZIPCSV returns the exact CSV member bytes, which are the source identity.
// The ZIP container itself is not recorded as provenance.
func ZIPCSV(raw []byte) ([]byte, error) {
	if len(raw) > MaxCSV {
		return nil, fmt.Errorf("ZIP exceeds 4 MiB")
	}
	archive, err := zip.NewReader(bytes.NewReader(raw), int64(len(raw)))
	if err != nil {
		return nil, fmt.Errorf("invalid ZIP: %w", err)
	}
	if len(archive.File) != 1 {
		return nil, fmt.Errorf("ZIP must contain exactly one CSV file")
	}
	file := archive.File[0]
	name := file.Name
	if name == "" || name == "." || name == ".." || strings.ContainsAny(name, "/\\\x00") || path.Ext(name) != ".csv" || !file.Mode().IsRegular() {
		return nil, fmt.Errorf("ZIP member must be a regular CSV file with a plain name")
	}
	if file.UncompressedSize64 > MaxCSV || file.CompressedSize64 > MaxCSV {
		return nil, fmt.Errorf("ZIP member exceeds 4 MiB")
	}
	r, err := file.Open()
	if err != nil {
		return nil, fmt.Errorf("open ZIP member: %w", err)
	}
	data, readErr := io.ReadAll(io.LimitReader(r, MaxCSV+1))
	closeErr := r.Close()
	if readErr != nil {
		return nil, fmt.Errorf("read ZIP member: %w", readErr)
	}
	if closeErr != nil {
		return nil, fmt.Errorf("close ZIP member: %w", closeErr)
	}
	if len(data) > MaxCSV {
		return nil, fmt.Errorf("ZIP member exceeds 4 MiB")
	}
	if crc32.ChecksumIEEE(data) != file.CRC32 {
		return nil, fmt.Errorf("ZIP member checksum: %w", zip.ErrChecksum)
	}
	return data, nil
}

// Normalize converts headerless Binance futures millisecond klines. asOf is an
// explicit observation cutoff, not evidence of the exchange's arrival time.
func Normalize(input io.Reader, timeframe string, asOf time.Time) ([]byte, error) {
	return NormalizeWithFunding(input, timeframe, asOf, nil)
}

// NormalizeWithFunding also accepts a supplied Binance fundingRate JSON array.
// A nil funding input preserves the original no-funding dataset.
func NormalizeWithFunding(input io.Reader, timeframe string, asOf time.Time, fundingJSON []byte) ([]byte, error) {
	duration := map[string]int64{"5m": 300000, "1h": 3600000, "4h": 14400000, "1d": 86400000}[timeframe]
	if duration == 0 || asOf.IsZero() {
		return nil, fmt.Errorf("supported timeframe and as-of required")
	}
	asOf = asOf.UTC()
	if _, err := asOf.MarshalText(); err != nil {
		return nil, fmt.Errorf("as-of cannot be represented in UTC RFC3339: %w", err)
	}
	raw, err := io.ReadAll(io.LimitReader(input, MaxCSV+1))
	if err != nil {
		return nil, err
	}
	if len(raw) > MaxCSV {
		return nil, fmt.Errorf("CSV exceeds 4 MiB")
	}
	sum := sha256.Sum256(raw)
	r := csv.NewReader(bytes.NewReader(raw))
	r.FieldsPerRecord = 12
	d := workbench.Dataset{Version: "dataset-v1", Provider: "Binance supplied CSV", Venue: "BINANCE", Instrument: "BTC", Timeframe: timeframe, Candles: []research.ReplayCandle{}, Funding: []workbench.Funding{}, Provenance: &workbench.DatasetProvenance{SourceSymbol: "BTCUSDT", SourceSHA256: fmt.Sprintf("%x", sum), AsOf: asOf.UTC().Format(time.RFC3339Nano)}}
	var previous int64 = -1
	for line := 1; ; line++ {
		fields, e := r.Read()
		if e == io.EOF {
			break
		}
		if e != nil {
			return nil, fmt.Errorf("row %d: %w", line, e)
		}
		integer := func(i int) (int64, error) { return strconv.ParseInt(fields[i], 10, 64) }
		open, e := integer(0)
		if e != nil || open < 0 || open > math.MaxInt64/1000000-duration || open%duration != 0 {
			return nil, fmt.Errorf("row %d: invalid open time", line)
		}
		closeMS, e := integer(6)
		if e != nil || closeMS != open+duration-1 {
			return nil, fmt.Errorf("row %d: invalid close time", line)
		}
		if open <= previous {
			return nil, fmt.Errorf("row %d: duplicate or unordered candle", line)
		}
		previous = open
		endNS := (closeMS + 1) * 1e6
		if time.Unix(0, endNS).After(asOf) {
			return nil, fmt.Errorf("row %d: candle closes after as-of", line)
		}
		vals := make([]float64, 7)
		for j, i := range []int{1, 2, 3, 4, 5, 7, 9} {
			v, e := strconv.ParseFloat(fields[i], 64)
			if e != nil || math.IsNaN(v) || math.IsInf(v, 0) || v < 0 {
				return nil, fmt.Errorf("row %d: invalid numeric field %d", line, i)
			}
			vals[j] = v
		}
		for _, i := range []int{10, 11} {
			v, e := strconv.ParseFloat(fields[i], 64)
			if e != nil || math.IsNaN(v) || math.IsInf(v, 0) || v < 0 {
				return nil, fmt.Errorf("row %d: invalid numeric field %d", line, i)
			}
		}
		trades, e := integer(8)
		if e != nil || trades < 0 {
			return nil, fmt.Errorf("row %d: invalid trade count", line)
		}
		if vals[2] <= 0 || vals[1] < vals[2] || vals[0] < vals[2] || vals[0] > vals[1] || vals[3] < vals[2] || vals[3] > vals[1] {
			return nil, fmt.Errorf("row %d: invalid OHLC", line)
		}
		d.Candles = append(d.Candles, research.ReplayCandle{Symbol: "BTC", Timeframe: timeframe, OpenTimeNS: open * 1e6, CloseTimeNS: endNS, Open: vals[0], High: vals[1], Low: vals[2], Close: vals[3], Volume: vals[4], TradeCount: trades})
		if len(d.Candles) > 20000 {
			return nil, fmt.Errorf("too many candles")
		}
	}
	if len(d.Candles) == 0 {
		return nil, fmt.Errorf("empty CSV")
	}
	if fundingJSON != nil {
		if len(fundingJSON) > MaxCSV {
			return nil, fmt.Errorf("funding JSON exceeds 4 MiB")
		}
		var rows []struct {
			Symbol string  `json:"symbol"`
			Time   *int64  `json:"fundingTime"`
			Rate   *string `json:"fundingRate"`
		}
		if err := json.Unmarshal(fundingJSON, &rows); err != nil || rows == nil {
			return nil, fmt.Errorf("invalid funding JSON array")
		}
		for i, row := range rows {
			if row.Symbol != "BTCUSDT" || row.Time == nil || *row.Time <= 0 || *row.Time > math.MaxInt64/1000000 || row.Rate == nil {
				return nil, fmt.Errorf("funding row %d: invalid symbol, time or rate", i+1)
			}
			ns := *row.Time * 1e6
			rate, err := strconv.ParseFloat(*row.Rate, 64)
			if err != nil || math.IsNaN(rate) || math.IsInf(rate, 0) || time.Unix(0, ns).After(asOf) || (i > 0 && ns <= d.Funding[i-1].TimeNS) {
				return nil, fmt.Errorf("funding row %d: invalid rate, cutoff or order", i+1)
			}
			d.Funding = append(d.Funding, workbench.Funding{TimeNS: ns, Rate: rate})
		}
		sum := sha256.Sum256(fundingJSON)
		d.Provenance.FundingSourceSHA256 = fmt.Sprintf("%x", sum)
	}
	b, err := json.Marshal(d)
	if err != nil {
		return nil, err
	}
	if len(b)+1 > workbench.MaxInput {
		return nil, fmt.Errorf("dataset exceeds workbench input limit")
	}
	return append(b, '\n'), nil
}
