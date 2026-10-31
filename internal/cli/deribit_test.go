package cli

import (
	"bytes"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestDeribitCLI(t *testing.T) {
	mode := "ok"
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Query().Get("currency") != "BTC" || r.URL.Query().Get("kind") != "option" {
			t.Errorf("bad query: %s", r.URL.RawQuery)
		}
		if mode == "http" {
			http.Error(w, "bad", 502)
			return
		}
		if mode == "api" {
			fmt.Fprint(w, `{"error":{"code":42}}`)
			return
		}
		if mode == "shape" {
			fmt.Fprint(w, `{"result":{}}`)
			return
		}
		if mode == "empty" {
			fmt.Fprint(w, `{"result":[]}`)
			return
		}
		switch r.URL.Path {
		case "/get_instruments":
			if r.URL.Query().Get("expired") != "false" {
				t.Error("missing expired")
			}
			fmt.Fprint(w, `{"result":[{"instrument_name":"BTC-X-C","expiration_timestamp":1999999999999,"strike":100,"option_type":"CALL","base_currency":"BTC"}]}`)
		case "/get_book_summary_by_currency":
			fmt.Fprint(w, `{"result":[{"instrument_name":"BTC-X-C","underlying_price":100,"open_interest":3,"mark_iv":50,"bid_price":0,"ask_price":2}]}`)
		default:
			t.Errorf("unexpected path %s", r.URL.Path)
			http.NotFound(w, r)
		}
	}))
	defer server.Close()
	root := t.TempDir()
	var out bytes.Buffer
	args := []string{"download-deribit-options", "--endpoint", server.URL, "--currency", "btc", "--output-root", root}
	if err := Run(args, &out, io.Discard); err != nil {
		t.Fatal(err)
	}
	files, err := filepath.Glob(filepath.Join(root, "BTC", "*", "*"))
	if err != nil || len(files) != 2 {
		t.Fatalf("files=%v err=%v", files, err)
	}
	if !strings.Contains(out.String(), "rows=1") {
		t.Fatal(out.String())
	}
	for _, m := range []string{"http", "api", "shape", "empty"} {
		t.Run(m, func(t *testing.T) {
			mode = m
			dir := t.TempDir()
			if err := Run([]string{"download-deribit-options", "--endpoint", server.URL, "--output-root", dir}, io.Discard, io.Discard); err == nil {
				t.Fatal("expected error")
			}
			entries, err := os.ReadDir(dir)
			if err != nil || len(entries) != 0 {
				t.Fatalf("unexpected output %v %v", entries, err)
			}
		})
	}
}
