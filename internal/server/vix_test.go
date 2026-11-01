package server

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"io"
	"net"
	"net/http"
	"os"
	"testing"
	"time"

	"github.com/infdahai/zenQuant/internal/options"
)

func TestVixHTTPNativeRoundtrip(t *testing.T) {
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	service := &Server{Options: options.Evaluate}
	done := make(chan error, 1)
	go func() { done <- service.Serve(listener) }()
	t.Cleanup(func() {
		listener.Close()
		if err := <-done; err != nil {
			t.Error(err)
		}
	})
	base := "http://" + listener.Addr().String()
	client := &http.Client{Timeout: 5 * time.Second}
	post := func(t *testing.T, route string, body []byte, status int) []byte {
		t.Helper()
		req, err := http.NewRequest(http.MethodPost, base+route, bytes.NewReader(body))
		if err != nil {
			t.Fatal(err)
		}
		req.Header.Set("Origin", base)
		req.Header.Set("Content-Type", "application/json")
		req.Header.Set("X-ZenQuant-Request", "1")
		resp, err := client.Do(req)
		if err != nil {
			t.Fatal(err)
		}
		defer resp.Body.Close()
		out, err := io.ReadAll(resp.Body)
		if err != nil {
			t.Fatal(err)
		}
		if resp.StatusCode != status || resp.Header.Get("Cache-Control") != "no-store" {
			t.Fatalf("HTTP %d, want %d: %s", resp.StatusCode, status, out)
		}
		return out
	}
	type envelope struct {
		Report         json.RawMessage `json:"report"`
		Request        string          `json:"request_json"`
		Transport      string          `json:"transport_sha256"`
		Code           string          `json:"code_sha256"`
		Cargo          json.RawMessage `json:"cargo_lock_sha256"`
		Implementation string          `json:"implementation"`
	}
	digest := func(b []byte) string { h := sha256.Sum256(b); return hex.EncodeToString(h[:]) }
	decode := func(t *testing.T, body, transport []byte) envelope {
		t.Helper()
		var got envelope
		if err := json.Unmarshal(body, &got); err != nil {
			t.Fatal(err)
		}
		code, err := hex.DecodeString(got.Code)
		if err != nil || len(code) != sha256.Size || got.Transport != digest(transport) || got.Implementation != "vix-observation-native.go.v1" || string(got.Cargo) != "null" {
			t.Fatalf("native identity/transport mismatch: %s", body)
		}
		return got
	}
	for _, scenario := range []string{"BACKWARDATION", "CONTANGO", "FLAT", "MISSING", "STALE", "ASYNCHRONOUS", "ROLL"} {
		t.Run(scenario, func(t *testing.T) {
			body := []byte(`{ "scenario": "` + scenario + `" }`)
			first := decode(t, post(t, "/api/vix/demo", body, 200), body)
			request := []byte(first.Request)
			second := decode(t, post(t, "/api/vix/evaluate", request, 200), request)
			if !bytes.Equal(first.Report, second.Report) || first.Code != second.Code || first.Transport == second.Transport {
				t.Fatal("demo export/evaluate changed canonical report or code identity")
			}
			var report struct {
				Hash    string `json:"input_sha256"`
				Futures struct {
					Status  string          `json:"status"`
					Metrics json.RawMessage `json:"metrics"`
				} `json:"futures"`
			}
			if err := json.Unmarshal(first.Report, &report); err != nil {
				t.Fatal(err)
			}
			status := map[string]string{"MISSING": "MISSING", "STALE": "STALE", "ASYNCHRONOUS": "INVALID"}[scenario]
			if status == "" {
				status = "READY"
			}
			metrics := len(report.Futures.Metrics) > 0 && string(report.Futures.Metrics) != "null"
			if report.Futures.Status != status || metrics != (status == "READY") {
				t.Fatalf("incorrect panel: %s", first.Report)
			}
			if scenario == "BACKWARDATION" && report.Hash != "372edb85ecadf97d4b2b0b8bd64e0ac86414890687dcb3a5e8b1f61b05dd73d3" {
				t.Fatal("canonical fixture hash changed")
			}
		})
	}
	t.Run("exact evaluate transport and canonical hash", func(t *testing.T) {
		fixture, err := os.ReadFile("../options/vix_demo.json")
		if err != nil {
			t.Fatal(err)
		}
		body := append([]byte(" \n\t"), fixture...)
		first := decode(t, post(t, "/api/vix/evaluate", body, 200), body)
		second := decode(t, post(t, "/api/vix/evaluate", []byte(first.Request), 200), []byte(first.Request))
		if !bytes.Equal(first.Report, second.Report) || !bytes.Contains(first.Report, []byte(`"input_sha256":"372edb85ecadf97d4b2b0b8bd64e0ac86414890687dcb3a5e8b1f61b05dd73d3"`)) {
			t.Fatal("canonical identity changed with whitespace")
		}
	})
	for _, tc := range []struct {
		route, body string
		status      int
		code        string
	}{
		{"/api/vix/evaluate", `{"bad":true}`, 422, "INVALID_INPUT"},
		{"/api/vix/demo", `{"scenario":"LIVE"}`, 422, "INVALID_INPUT"},
	} {
		t.Run(tc.route+tc.code, func(t *testing.T) {
			out := post(t, tc.route, []byte(tc.body), tc.status)
			var fields map[string]json.RawMessage
			if err := json.Unmarshal(out, &fields); err != nil {
				t.Fatal(err)
			}
			if len(fields) != 1 || string(fields["error"]) != `"`+tc.code+`"` {
				t.Fatalf("failure leaked a report: %s", out)
			}
		})
	}
}
