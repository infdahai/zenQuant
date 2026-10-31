// Package cot downloads and normalizes CFTC financial futures reports.
package cot

import (
	"archive/zip"
	"bytes"
	"encoding/csv"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/parquet-go/parquet-go"
)

const DefaultBaseURL = "https://www.cftc.gov/files/dea/history"
const limit = 64 << 20

type Row struct {
	ReportDate     int64  `parquet:"report_date_ns"`
	Market         string `parquet:"market_name"`
	AssetLong      int64  `parquet:"asset_mgr_long"`
	AssetShort     int64  `parquet:"asset_mgr_short"`
	AssetNet       int64  `parquet:"asset_mgr_net"`
	LeveragedLong  int64  `parquet:"leveraged_long"`
	LeveragedShort int64  `parquet:"leveraged_short"`
	LeveragedNet   int64  `parquet:"leveraged_net"`
	DealerLong     int64  `parquet:"dealer_long"`
	DealerShort    int64  `parquet:"dealer_short"`
	DealerNet      int64  `parquet:"dealer_net"`
	TotalOI        int64  `parquet:"total_oi"`
}

// Download fails the entire request if any year fails, avoiding partial datasets.
func Download(client *http.Client, base, market string, start, end int) ([]Row, error) {
	u, err := url.Parse(base)
	if err != nil || u.Host == "" || (u.Scheme != "http" && u.Scheme != "https") || u.RawQuery != "" || u.Fragment != "" || start < 1900 || end > 2261 || start > end {
		return nil, errors.New("invalid base URL or year range")
	}
	var rows []Row
	for year := start; year <= end; year++ {
		u.Path = strings.TrimRight(u.Path, "/") + fmt.Sprintf("/fut_fin_txt_%d.zip", year)
		req, err := http.NewRequest(http.MethodGet, u.String(), nil)
		if err != nil {
			return nil, err
		}
		req.Header.Set("User-Agent", "zenquant/1.0")
		resp, err := client.Do(req)
		if err != nil {
			return nil, fmt.Errorf("year %d: %w", year, err)
		}
		b, readErr := io.ReadAll(io.LimitReader(resp.Body, limit+1))
		closeErr := resp.Body.Close()
		if resp.StatusCode != http.StatusOK {
			return nil, fmt.Errorf("year %d: HTTP %s", year, resp.Status)
		}
		if readErr != nil {
			return nil, readErr
		}
		if closeErr != nil {
			return nil, closeErr
		}
		if len(b) > limit {
			return nil, errors.New("archive exceeds limit")
		}
		parsed, err := parseArchive(b, market)
		if err != nil {
			return nil, fmt.Errorf("year %d: %w", year, err)
		}
		for _, row := range parsed {
			if time.Unix(0, row.ReportDate).UTC().Year() != year {
				return nil, fmt.Errorf("year %d: report date belongs to another year", year)
			}
		}
		rows = append(rows, parsed...)
		u.Path = strings.TrimSuffix(u.Path, fmt.Sprintf("/fut_fin_txt_%d.zip", year))
	}
	if len(rows) == 0 {
		return nil, errors.New("no Bitcoin COT data found")
	}
	for _, row := range rows[1:] {
		if row.Market != rows[0].Market {
			return nil, errors.New("multiple Bitcoin markets found; select one with --market")
		}
	}
	sort.SliceStable(rows, func(i, j int) bool { return rows[i].ReportDate < rows[j].ReportDate })
	unique := rows[:0]
	for _, r := range rows {
		if len(unique) == 0 || unique[len(unique)-1].ReportDate != r.ReportDate {
			unique = append(unique, r)
		} else if unique[len(unique)-1] != r {
			return nil, fmt.Errorf("conflicting Bitcoin records for %s", time.Unix(0, r.ReportDate).UTC().Format("2006-01-02"))
		}
	}
	return unique, nil
}

func parseArchive(b []byte, market string) ([]Row, error) {
	z, err := zip.NewReader(bytes.NewReader(b), int64(len(b)))
	if err != nil {
		return nil, err
	}
	for _, f := range z.File {
		if strings.HasSuffix(f.Name, ".txt") {
			if f.UncompressedSize64 > limit {
				return nil, errors.New("report exceeds limit")
			}
			r, err := f.Open()
			if err != nil {
				return nil, err
			}
			data, err := io.ReadAll(io.LimitReader(r, limit+1))
			closeErr := r.Close()
			if err != nil {
				return nil, err
			}
			if closeErr != nil {
				return nil, closeErr
			}
			if len(data) > limit {
				return nil, errors.New("report exceeds limit")
			}
			return parseCSV(data, market)
		}
	}
	return nil, errors.New("archive contains no text report")
}
func parseCSV(b []byte, selectedMarket string) ([]Row, error) {
	reader := csv.NewReader(bytes.NewReader(b))
	reader.FieldsPerRecord = -1
	header, err := reader.Read()
	if err != nil {
		return nil, err
	}
	indices := map[string]int{}
	for i, h := range header {
		if _, exists := indices[h]; exists {
			return nil, fmt.Errorf("duplicate column %q", h)
		}
		indices[h] = i
	}
	numericColumns := []string{"Asset_Mgr_Positions_Long_All", "Asset_Mgr_Positions_Short_All", "Lev_Money_Positions_Long_All", "Lev_Money_Positions_Short_All", "Dealer_Positions_Long_All", "Dealer_Positions_Short_All", "Open_Interest_All"}
	for _, column := range append([]string{"Market_and_Exchange_Names"}, numericColumns...) {
		if _, ok := indices[column]; !ok {
			return nil, fmt.Errorf("missing required column %q", column)
		}
	}
	_, hasDate := indices["Report_Date_as_YYYY-MM-DD"]
	_, hasLegacyDate := indices["As_of_Date_In_Form_YYMMDD"]
	if !hasDate && !hasLegacyDate {
		return nil, errors.New("missing report date column")
	}
	get := func(record []string, key string) string {
		i, ok := indices[key]
		if !ok || i >= len(record) {
			return ""
		}
		return record[i]
	}
	var rows []Row
	for {
		record, e := reader.Read()
		if e == io.EOF {
			break
		}
		if e != nil {
			return nil, e
		}
		market := strings.ToUpper(strings.TrimSpace(get(record, "Market_and_Exchange_Names")))
		if !strings.Contains(market, "BITCOIN") || (selectedMarket != "" && market != strings.ToUpper(strings.TrimSpace(selectedMarket))) {
			continue
		}
		if len(record) != len(header) {
			return nil, errors.New("Bitcoin row has incorrect column count")
		}
		date := get(record, "Report_Date_as_YYYY-MM-DD")
		if date == "" {
			date = get(record, "As_of_Date_In_Form_YYMMDD")
		}
		layout := "2006-01-02"
		if len(date) == 6 {
			layout = "060102"
		} else {
			date = strings.TrimSpace(date)
		}
		t, e := time.Parse(layout, date)
		if e != nil || t.Year() < 1678 || t.Year() > 2261 {
			return nil, fmt.Errorf("invalid Bitcoin report date %q", date)
		}
		values := make([]int64, len(numericColumns))
		for i, column := range numericColumns {
			raw := strings.ReplaceAll(strings.TrimSpace(get(record, column)), ",", "")
			value, err := strconv.ParseInt(raw, 10, 64)
			if err != nil {
				return nil, fmt.Errorf("invalid Bitcoin %s value %q: %w", column, raw, err)
			}
			values[i] = value
		}
		a, b := values[0], values[1]
		l, s := values[2], values[3]
		d, ds := values[4], values[5]
		// Reject arithmetic overflow rather than silently publish incorrect net positions.
		sub := func(x, y int64) (int64, bool) { z := x - y; return z, (y >= 0 && z <= x) || (y < 0 && z > x) }
		an, ok1 := sub(a, b)
		ln, ok2 := sub(l, s)
		dn, ok3 := sub(d, ds)
		if !ok1 || !ok2 || !ok3 {
			return nil, errors.New("net position overflow")
		}
		rows = append(rows, Row{t.UnixNano(), strings.TrimSpace(market), a, b, an, l, s, ln, d, ds, dn, values[6]})
	}
	return rows, nil
}

// Save creates a new private run directory; it never overwrites a prior dataset.
func Save(rows []Row, root string) (string, error) {
	if len(rows) == 0 {
		return "", errors.New("no rows")
	}
	if err := os.MkdirAll(root, 0755); err != nil {
		return "", err
	}
	dir, err := os.MkdirTemp(root, "cot-run-")
	if err != nil {
		return "", err
	}
	path := filepath.Join(dir, "cot_btc.parquet")
	staging := filepath.Join(dir, ".cot_btc.parquet.tmp")
	f, err := os.OpenFile(staging, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0644)
	if err != nil {
		os.Remove(dir)
		return "", err
	}
	w := parquet.NewGenericWriter[Row](f)
	_, err = w.Write(rows)
	if err == nil {
		err = w.Close()
	}
	closeErr := f.Close()
	if err == nil {
		err = closeErr
	}
	if err == nil {
		err = os.Rename(staging, path)
	}
	if err != nil {
		os.Remove(staging)
		os.Remove(dir)
		return "", err
	}
	return path, nil
}
