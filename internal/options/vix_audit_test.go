package options

import (
	"bytes"
	"encoding/json"
	"os"
	"reflect"
	"testing"
)

// Frozen synthetic PR23 audit expectations. No Rust executable, downloaded
// market data, clock, or network is used by this regression suite.
func TestVixArchivedAuditCorpus(t *testing.T) {
	data, err := os.ReadFile("testdata/vix/audit.json")
	if err != nil {
		t.Fatal(err)
	}
	var corpus struct {
		Provenance struct {
			Commit    string `json:"rust_commit"`
			Synthetic bool   `json:"synthetic_only"`
		} `json:"provenance"`
		Cases []struct {
			Name      string          `json:"name"`
			Request   json.RawMessage `json:"request"`
			Rust      json.RawMessage `json:"rust"`
			GoError   string          `json:"go_error"`
			GoReport  json.RawMessage `json:"go_report"`
			Allowance string          `json:"allowance"`
		} `json:"cases"`
	}
	if err := json.Unmarshal(data, &corpus); err != nil {
		t.Fatal(err)
	}
	if len(corpus.Cases) != 123 || corpus.Provenance.Commit != "c9c35a708b0286effc530fdc0919823d8caa9d8f" || !corpus.Provenance.Synthetic {
		t.Fatal("unexpected corpus provenance/inventory")
	}
	allowances := 0
	for _, tc := range corpus.Cases {
		if tc.Allowance != "" {
			allowances++
			if tc.Name != "weekly plus prefix" || len(tc.GoReport) == 0 {
				t.Fatal("unexpected semantic allowance")
			}
		} else if len(tc.GoReport) != 0 {
			t.Fatal("Go override without documented allowance")
		}
		t.Run(tc.Name, func(t *testing.T) {
			out, err := Evaluate("/api/vix/evaluate", tc.Request)
			if tc.GoError != "" {
				var rust struct {
					Error string `json:"error"`
				}
				if e := json.Unmarshal(tc.Rust, &rust); e != nil {
					t.Fatal(e)
				}
				mapping := map[string]string{"InvalidInput": "INVALID_INPUT", "InvalidSchema": "INVALID_SCHEMA", "InvalidTimestamp": "INVALID_TIMESTAMP", "LimitExceeded": "LIMIT_EXCEEDED", "ArithmeticOverflow": "ARITHMETIC_OVERFLOW"}
				if mapping[rust.Error] != tc.GoError {
					t.Fatalf("unapproved error mapping: %s -> %s", rust.Error, tc.GoError)
				}
				if err == nil || err.Error() != tc.GoError || len(out) != 0 {
					t.Fatalf("want %s and no output, got %v / %s", tc.GoError, err, out)
				}
				return
			}
			if err != nil {
				t.Fatal(err)
			}
			var envelope struct {
				Report         json.RawMessage `json:"report"`
				Request        string          `json:"request_json"`
				Transport      string          `json:"transport_sha256"`
				Code           string          `json:"code_sha256"`
				Cargo          json.RawMessage `json:"cargo_lock_sha256"`
				Implementation string          `json:"implementation"`
			}
			if err := json.Unmarshal(out, &envelope); err != nil {
				t.Fatal(err)
			}
			want := tc.Rust
			if tc.Allowance != "" {
				want = tc.GoReport
			}
			var actual, expected any
			if err := json.Unmarshal(want, &expected); err != nil {
				t.Fatal(err)
			}
			if err := json.Unmarshal(envelope.Report, &actual); err != nil {
				t.Fatal(err)
			}
			if !reflect.DeepEqual(actual, expected) {
				t.Fatalf("report differs (no fields ignored)\nwant %s\ngot %s", want, envelope.Report)
			}
			if envelope.Transport != sha(tc.Request) || envelope.Code != embeddedSourceHash || !validHash(envelope.Code) || string(envelope.Cargo) != "null" || envelope.Implementation != "vix-observation-native.go.v1" {
				t.Fatalf("incorrect native identity: %s", out)
			}
			type panelOutput struct {
				Status  string          `json:"status"`
				Metrics json.RawMessage `json:"metrics"`
			}
			var report struct {
				Futures panelOutput `json:"futures"`
				Spot    panelOutput `json:"spot"`
			}
			if err := json.Unmarshal(envelope.Report, &report); err != nil {
				t.Fatal(err)
			}
			for _, panel := range []panelOutput{report.Futures, report.Spot} {
				present := len(panel.Metrics) != 0 && string(panel.Metrics) != "null"
				if (panel.Status == "READY") != present {
					t.Fatalf("metrics must be present exactly when READY: %+v", panel)
				}
			}
			roundtrip, err := Evaluate("/api/vix/evaluate", []byte(envelope.Request))
			if err != nil {
				t.Fatal(err)
			}
			var again struct {
				Report json.RawMessage `json:"report"`
			}
			if err := json.Unmarshal(roundtrip, &again); err != nil {
				t.Fatal(err)
			}
			if !bytes.Equal(again.Report, envelope.Report) {
				t.Fatal("exported request changed report or canonical hash")
			}
		})
	}
	if allowances != 1 {
		t.Fatalf("want one documented grammar correction; got %d", allowances)
	}
}
