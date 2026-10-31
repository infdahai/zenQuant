package cli

import (
	"archive/zip"
	"bytes"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/infdahai/zenQuant/internal/cot"
	"github.com/parquet-go/parquet-go"
)

func cotArchive(t *testing.T, content string) []byte {
	t.Helper()
	var b bytes.Buffer
	z := zip.NewWriter(&b)
	f, err := z.Create("report.txt")
	if err != nil {
		t.Fatal(err)
	}
	if _, err = f.Write([]byte(content)); err != nil {
		t.Fatal(err)
	}
	if err = z.Close(); err != nil {
		t.Fatal(err)
	}
	return b.Bytes()
}

func TestDownloadCOTCLI(t *testing.T) {
	year := time.Now().UTC().Year()
	archive := cotArchive(t, fmt.Sprintf("Market_and_Exchange_Names,Report_Date_as_YYYY-MM-DD,Asset_Mgr_Positions_Long_All,Asset_Mgr_Positions_Short_All,Lev_Money_Positions_Long_All,Lev_Money_Positions_Short_All,Dealer_Positions_Long_All,Dealer_Positions_Short_All,Open_Interest_All\nBitcoin CME,%d-01-03,100,40,20,30,5,7,200\nBitcoin CME,%d-01-03,100,40,20,30,5,7,200\nETH,%d-01-04,1,0,0,0,0,0,0\n", year, year, year))
	priorArchive := cotArchive(t, fmt.Sprintf("Market_and_Exchange_Names,Report_Date_as_YYYY-MM-DD,Asset_Mgr_Positions_Long_All,Asset_Mgr_Positions_Short_All,Lev_Money_Positions_Long_All,Lev_Money_Positions_Short_All,Dealer_Positions_Long_All,Dealer_Positions_Short_All,Open_Interest_All\nBitcoin CME,%d-01-03,100,40,20,30,5,7,200\n", year-1))
	var failCurrent atomic.Bool
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == fmt.Sprintf("/reports/fut_fin_txt_%d.zip", year-1) && failCurrent.Load() {
			w.Write(priorArchive)
			return
		}
		if r.URL.Path != fmt.Sprintf("/reports/fut_fin_txt_%d.zip", year) || failCurrent.Load() {
			http.NotFound(w, r)
			return
		}
		w.Write(archive)
	}))
	defer server.Close()
	root := t.TempDir()
	var out, stderr bytes.Buffer
	if err := Run([]string{"download-cot", "--years", "1", "--base-url", server.URL + "/reports", "--output-root", root}, &out, &stderr); err != nil {
		t.Fatal(err)
	}
	matches, err := filepath.Glob(filepath.Join(root, "cot-run-*", "cot_btc.parquet"))
	if err != nil || len(matches) != 1 {
		t.Fatalf("files %v error %v", matches, err)
	}
	rows, err := parquet.ReadFile[cot.Row](matches[0])
	if err != nil {
		t.Fatal(err)
	}
	if len(rows) != 1 || rows[0].AssetNet != 60 || rows[0].LeveragedNet != -10 || rows[0].DealerNet != -2 || rows[0].TotalOI != 200 {
		t.Fatalf("rows: %+v", rows)
	}
	if !strings.Contains(out.String(), "rows=1") || !strings.Contains(out.String(), matches[0]) {
		t.Fatal(out.String())
	}
	// A failed subsequent year must not publish a partial dataset or alter prior output.
	before, err := os.ReadFile(matches[0])
	if err != nil {
		t.Fatal(err)
	}
	failCurrent.Store(true)
	if err = Run([]string{"download-cot", "--years", "2", "--base-url", server.URL + "/reports", "--output-root", root}, &out, &stderr); err == nil {
		t.Fatal("expected failed year")
	}
	after, err := os.ReadFile(matches[0])
	if err != nil || !bytes.Equal(before, after) {
		t.Fatal("existing output changed")
	}
	matches, _ = filepath.Glob(filepath.Join(root, "cot-run-*"))
	if len(matches) != 1 {
		t.Fatal(matches)
	}
}

func TestDownloadCOTRejectsConflictingOrWrongYear(t *testing.T) {
	year := time.Now().UTC().Year()
	header := "Market_and_Exchange_Names,Report_Date_as_YYYY-MM-DD,Asset_Mgr_Positions_Long_All,Asset_Mgr_Positions_Short_All,Lev_Money_Positions_Long_All,Lev_Money_Positions_Short_All,Dealer_Positions_Long_All,Dealer_Positions_Short_All,Open_Interest_All\n"
	for name, body := range map[string]string{
		"different contracts": fmt.Sprintf("BITCOIN,%d-01-03,1,0,0,0,0,0,1\nMICRO BITCOIN,%d-01-03,2,0,0,0,0,0,2\n", year, year),
		"different dates":     fmt.Sprintf("BITCOIN,%d-01-03,1,0,0,0,0,0,1\nMICRO BITCOIN,%d-01-10,2,0,0,0,0,0,2\n", year, year),
		"wrong year":          fmt.Sprintf("BITCOIN,%d-01-03,1,0,0,0,0,0,1\n", year-1),
	} {
		t.Run(name, func(t *testing.T) {
			archive := cotArchive(t, header+body)
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { w.Write(archive) }))
			defer server.Close()
			root := t.TempDir()
			if err := Run([]string{"download-cot", "--years", "1", "--base-url", server.URL, "--output-root", root}, &bytes.Buffer{}, &bytes.Buffer{}); err == nil {
				t.Fatal("expected error")
			}
			entries, err := os.ReadDir(root)
			if err != nil || len(entries) != 0 {
				t.Fatalf("entries %v error %v", entries, err)
			}
		})
	}
}

func TestDownloadCOTMarketSelection(t *testing.T) {
	year := time.Now().UTC().Year()
	header := "Market_and_Exchange_Names,Report_Date_as_YYYY-MM-DD,Asset_Mgr_Positions_Long_All,Asset_Mgr_Positions_Short_All,Lev_Money_Positions_Long_All,Lev_Money_Positions_Short_All,Dealer_Positions_Long_All,Dealer_Positions_Short_All,Open_Interest_All\n"
	archive := cotArchive(t, header+fmt.Sprintf("BITCOIN CME,%d-01-03,1,0,0,0,0,0,1\nMICRO BITCOIN,%d-01-03,2,0,0,0,0,0,2\n", year, year))
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { w.Write(archive) }))
	defer server.Close()
	root := t.TempDir()
	if err := Run([]string{"download-cot", "--years", "1", "--base-url", server.URL, "--output-root", root, "--market", "bitcoin cme"}, &bytes.Buffer{}, &bytes.Buffer{}); err != nil {
		t.Fatal(err)
	}
	paths, err := filepath.Glob(filepath.Join(root, "cot-run-*", "cot_btc.parquet"))
	if err != nil || len(paths) != 1 {
		t.Fatalf("paths %v: %v", paths, err)
	}
	rows, err := parquet.ReadFile[cot.Row](paths[0])
	if err != nil || len(rows) != 1 || rows[0].Market != "BITCOIN CME" || rows[0].AssetLong != 1 {
		t.Fatalf("rows %+v: %v", rows, err)
	}
}

func TestDownloadCOTRejectsBadArchive(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { fmt.Fprint(w, "bad zip") }))
	defer server.Close()
	root := t.TempDir()
	if err := Run([]string{"download-cot", "--years", "1", "--base-url", server.URL, "--output-root", root}, &bytes.Buffer{}, &bytes.Buffer{}); err == nil {
		t.Fatal("expected malformed archive error")
	}
	entries, err := os.ReadDir(root)
	if err != nil || len(entries) != 0 {
		t.Fatalf("entries %v error %v", entries, err)
	}
}
