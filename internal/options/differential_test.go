package options

import (
	"bytes"
	"encoding/json"
	"io"
	"net/http"
	"os"
	"reflect"
	"testing"
	"time"
)

// Optional live reference check; ordinary native tests never require Rust or a subprocess.
// Start the existing Rust options-ui separately and set OPTIONS_RUST_ORACLE_URL.
func TestRustDifferential(t *testing.T) {
	base := os.Getenv("OPTIONS_RUST_ORACLE_URL")
	if base == "" {
		t.Skip("set OPTIONS_RUST_ORACLE_URL to compare the existing Rust HTTP API")
	}
	client := http.Client{Timeout: 10 * time.Second}
	compare := func(t *testing.T, path string, body []byte) {
		t.Helper()
		req, e := http.NewRequest("POST", base+path, bytes.NewReader(body))
		if e != nil {
			t.Fatal(e)
		}
		req.Header.Set("Origin", base)
		req.Header.Set("X-ZenQuant-Request", "1")
		req.Header.Set("Content-Type", "application/json")
		resp, e := client.Do(req)
		if e != nil {
			t.Fatal(e)
		}
		defer resp.Body.Close()
		rust, e := io.ReadAll(resp.Body)
		if e != nil {
			t.Fatal(e)
		}
		native, err := Evaluate(path, body)
		if resp.StatusCode != 200 {
			if err == nil {
				t.Fatalf("Rust rejected %s native admitted", rust)
			}
			var envelope struct {
				Error string `json:"error"`
			}
			_ = json.Unmarshal(rust, &envelope)
			if envelope.Error != err.(*Error).Code {
				t.Fatalf("Rust %s Go %v", rust, err)
			}
			return
		}
		if err != nil {
			t.Fatal(err)
		}
		var a, b map[string]any
		_ = json.Unmarshal(rust, &a)
		_ = json.Unmarshal(native, &b)
		if !reflect.DeepEqual(a["report"], b["report"]) {
			t.Fatalf("reports differ\nRust %s\nGo %s", rawJSON(a["report"]), rawJSON(b["report"]))
		}
		var ar, br any
		da := json.NewDecoder(bytes.NewBufferString(a["request_json"].(string)))
		da.UseNumber()
		db := json.NewDecoder(bytes.NewBufferString(b["request_json"].(string)))
		db.UseNumber()
		_ = da.Decode(&ar)
		_ = db.Decode(&br)
		if !reflect.DeepEqual(ar, br) {
			t.Fatal("normalized requests differ")
		}
	}
	compare(t, "/api/synthetic", []byte(controls))
	changes := []func(*Request){func(r *Request) {}, func(r *Request) { r.Candidate.Spec.OptionType = "P"; rebind(r) }, func(r *Request) { r.Candidate.Quotes = []QuoteInput{} }, func(r *Request) { r.Candidate.TerminalFee = nil }, func(r *Request) { r.CutoffNs = 200 }, func(r *Request) { r.Candidate.Leg.Side = "SELL" }, func(r *Request) { r.Grid.AtNs = 999 }, func(r *Request) { r.Candidate.Quotes[0].Book.Asks[0].Price = decimal("2.001") }, func(r *Request) {
		r.Candidate.Spec.QuantityUnit = "contracts"
		r.Candidate.Spec.Multiplier = decimal("10")
		rebind(r)
	}, func(r *Request) { r.Candidate.Quotes[0].FeePolicy = nil; r.Candidate.Quotes[0].FeeKnownNs = nil }, func(r *Request) { r.Candidate = nil; r.Grid = nil; r.CutoffNs = 1900000000000000001 }, func(r *Request) {
		r.Signal = &ManualSignal{"manual-scenario-signal.rust.v1", "manual", sha([]byte("test")), 8, 9, 11, "test <&> literal \\u2028"}
	}, func(r *Request) { r.Grid.Step = decimal("0.0000000000000000000000000001"); r.Grid.Count = 4096 }, func(r *Request) { r.Candidate.Quotes[0].Book.Bids[0].Price = decimal("3") }}
	for i, change := range changes {
		t.Run(string(rune('A'+i)), func(t *testing.T) {
			r := baseline()
			r.Grid.Count = 65
			change(&r)
			compare(t, "/api/curve", rawJSON(r))
		})
	}
}
