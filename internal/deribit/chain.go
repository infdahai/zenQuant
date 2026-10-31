// Package deribit downloads one bounded options-chain snapshot.
package deribit

import (
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"

	"github.com/parquet-go/parquet-go"
)

const DefaultEndpoint = "https://www.deribit.com/api/v2/public"
const maxResponse = 16 << 20

type Row struct {
	SnapshotTS       int64    `json:"snapshot_ts_ns" parquet:"snapshot_ts_ns"`
	Currency         string   `json:"currency" parquet:"currency"`
	Instrument       string   `json:"instrument_name" parquet:"instrument_name"`
	Expiry           int64    `json:"expiry_ts_ms" parquet:"expiry_ts_ms"`
	ExpiryDate       string   `json:"expiry_date" parquet:"expiry_date"`
	Strike           float64  `json:"strike" parquet:"strike"`
	OptionType       string   `json:"option_type" parquet:"option_type"`
	UnderlyingPrice  float64  `json:"underlying_price" parquet:"underlying_price"`
	OpenInterest     float64  `json:"open_interest" parquet:"open_interest"`
	VolumeUSD        float64  `json:"volume_usd" parquet:"volume_usd"`
	MarkIV           float64  `json:"mark_iv" parquet:"mark_iv"`
	Bid              *float64 `json:"bid_price" parquet:"bid_price,optional"`
	Ask              *float64 `json:"ask_price" parquet:"ask_price,optional"`
	Mid              *float64 `json:"mid_price" parquet:"mid_price,optional"`
	UnderlyingIndex  string   `json:"underlying_index" parquet:"underlying_index"`
	SettlementPeriod string   `json:"settlement_period" parquet:"settlement_period"`
	MarkIVUnit       string   `json:"mark_iv_unit" parquet:"mark_iv_unit"`
}
type instrument struct {
	Name       string  `json:"instrument_name"`
	Expiry     int64   `json:"expiration_timestamp"`
	Strike     float64 `json:"strike"`
	Type       string  `json:"option_type"`
	Base       string  `json:"base_currency"`
	Settlement string  `json:"settlement_period"`
}
type summary struct {
	Name       string   `json:"instrument_name"`
	Underlying float64  `json:"underlying_price"`
	OI         float64  `json:"open_interest"`
	Volume     float64  `json:"volume_usd"`
	IV         float64  `json:"mark_iv"`
	Bid        *float64 `json:"bid_price"`
	Ask        *float64 `json:"ask_price"`
}

func fetch(client *http.Client, base, method string, query url.Values, result any) error {
	u, err := url.Parse(base)
	if err != nil || u.Host == "" || (u.Scheme != "http" && u.Scheme != "https") || u.RawQuery != "" || u.Fragment != "" {
		return errors.New("invalid endpoint")
	}
	u.Path = strings.TrimRight(u.Path, "/") + "/" + method
	u.RawQuery = query.Encode()
	req, err := http.NewRequest(http.MethodGet, u.String(), nil)
	if err != nil {
		return err
	}
	req.Header.Set("User-Agent", "zenquant/1.0")
	resp, err := client.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("%s: HTTP %s", method, resp.Status)
	}
	b, err := io.ReadAll(io.LimitReader(resp.Body, maxResponse+1))
	if err != nil {
		return err
	}
	if len(b) > maxResponse {
		return fmt.Errorf("%s: response exceeds limit", method)
	}
	var envelope struct {
		Result json.RawMessage `json:"result"`
		Error  json.RawMessage `json:"error"`
	}
	if err = json.Unmarshal(b, &envelope); err != nil {
		return fmt.Errorf("%s: %w", method, err)
	}
	if len(envelope.Error) > 0 && string(envelope.Error) != "null" {
		return fmt.Errorf("%s: API error: %s", method, envelope.Error)
	}
	if len(envelope.Result) == 0 || string(envelope.Result) == "null" {
		return fmt.Errorf("%s: missing result", method)
	}
	if err = json.Unmarshal(envelope.Result, result); err != nil {
		return fmt.Errorf("%s: invalid result: %w", method, err)
	}
	return nil
}

func Download(client *http.Client, endpoint, currency string, now func() time.Time) ([]Row, error) {
	currency = strings.ToUpper(currency)
	if currency == "" {
		return nil, errors.New("currency is required")
	}
	for _, c := range currency {
		if c < 'A' || c > 'Z' {
			return nil, errors.New("currency must contain only letters")
		}
	}
	var instruments []instrument
	var summaries []summary
	q := url.Values{"currency": {currency}, "kind": {"option"}, "expired": {"false"}}
	if err := fetch(client, endpoint, "get_instruments", q, &instruments); err != nil {
		return nil, err
	}
	q.Del("expired")
	if err := fetch(client, endpoint, "get_book_summary_by_currency", q, &summaries); err != nil {
		return nil, err
	}
	ts := now().UTC().UnixNano()
	byName := make(map[string]instrument, len(instruments))
	for _, i := range instruments {
		if i.Name != "" {
			byName[i.Name] = i
		}
	}
	rows := make([]Row, 0, len(summaries))
	for _, s := range summaries {
		i, ok := byName[s.Name]
		if !ok || i.Expiry <= 0 {
			continue
		}
		bid, ask := s.Bid, s.Ask
		if bid != nil && *bid <= 0 {
			bid = nil
		}
		if ask != nil && *ask <= 0 {
			ask = nil
		}
		var mid *float64
		if bid != nil && ask != nil {
			v := (*bid + *ask) / 2
			mid = &v
		}
		base := i.Base
		if base == "" {
			base = currency
		}
		rows = append(rows, Row{ts, currency, i.Name, i.Expiry, time.UnixMilli(i.Expiry).UTC().Format("2006-01-02"), i.Strike, strings.ToLower(i.Type), s.Underlying, s.OI, s.Volume, s.IV, bid, ask, mid, base, i.Settlement, "percent"})
	}
	sort.SliceStable(rows, func(a, b int) bool {
		x, y := rows[a], rows[b]
		if x.Expiry != y.Expiry {
			return x.Expiry < y.Expiry
		}
		if x.Strike != y.Strike {
			return x.Strike < y.Strike
		}
		return x.OptionType < y.OptionType
	})
	if len(rows) == 0 {
		return nil, errors.New("no Deribit option rows to save")
	}
	return rows, nil
}

// Save publishes a complete snapshot with a single directory rename.
func Save(rows []Row, root string) (string, string, error) {
	return save(rows, root, nil)
}

func save(rows []Row, root string, checkpoint func(string) error) (string, string, error) {
	if len(rows) == 0 {
		return "", "", errors.New("no rows")
	}
	t := time.Unix(0, rows[0].SnapshotTS).UTC()
	dir := filepath.Join(root, rows[0].Currency, t.Format("2006-01"))
	if err := os.MkdirAll(dir, 0755); err != nil {
		return "", "", err
	}
	stem := "options_chain_" + t.Format("20060102T150405Z")
	stagingRoot := filepath.Join(root, ".options-chain-staging")
	if err := os.MkdirAll(stagingRoot, 0755); err != nil {
		return "", "", err
	}
	stage, err := os.MkdirTemp(stagingRoot, "run-")
	if err != nil {
		return "", "", err
	}
	defer os.RemoveAll(stage)
	run, err := os.MkdirTemp(dir, "go-run-")
	if err != nil {
		return "", "", err
	}
	// Remove only our empty parent on failure; never remove published data.
	defer os.Remove(run)
	if checkpoint != nil {
		if err := checkpoint("created"); err != nil {
			return "", "", err
		}
	}
	b, err := json.MarshalIndent(rows, "", "  ")
	if err != nil {
		return "", "", err
	}
	if err = os.WriteFile(filepath.Join(stage, stem+".json"), b, 0644); err != nil {
		return "", "", err
	}
	f, err := os.Create(filepath.Join(stage, stem+".parquet"))
	if err != nil {
		return "", "", err
	}
	w := parquet.NewGenericWriter[Row](f)
	_, err = w.Write(rows)
	if err == nil {
		err = w.Close()
	}
	closeErr := f.Close()
	if err != nil {
		return "", "", err
	}
	if closeErr != nil {
		return "", "", closeErr
	}
	if checkpoint != nil {
		if err := checkpoint("staged"); err != nil {
			return "", "", err
		}
	}
	published := filepath.Join(run, "snapshot")
	if err := os.Rename(stage, published); err != nil {
		return "", "", err
	}
	if checkpoint != nil {
		if err := checkpoint("published"); err != nil {
			return "", "", err
		}
	}
	return filepath.Join(published, stem+".json"), filepath.Join(published, stem+".parquet"), nil
}
