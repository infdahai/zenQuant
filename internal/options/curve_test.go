package options

import (
	"bytes"
	"encoding/json"
	"os"
	"strings"
	"testing"
)

const controls = `{"option_type":"C","strike":"100","quantity":".01","ask":"2","bid":"1.9","fee_rate":".0003","terminal_fee":".001","first":"50","step":".1","count":1001,"signal_hypothesis":null,"signal_known_ns":"10","signal_valid_until_ns":"1000"}`

func baseline() Request { var p syntheticParameters; decode([]byte(controls), &p); return synthetic(p) }
func evaluate(t *testing.T, r Request) Report {
	t.Helper()
	b, e := Evaluate("/api/curve", rawJSON(r))
	if e != nil {
		t.Fatal(e)
	}
	var x Response
	if e = json.Unmarshal(b, &x); e != nil {
		t.Fatal(e)
	}
	return x.Report
}
func checkDec(t *testing.T, d *Dec, want string) {
	t.Helper()
	if d == nil || d.String() != want {
		t.Fatalf("decimal got %v want %s", d, want)
	}
}
func errorCode(t *testing.T, path string, b []byte, want string) {
	t.Helper()
	_, e := Evaluate(path, b)
	if e == nil || e.(*Error).Code != want {
		t.Fatalf("error got %v want %s", e, want)
	}
}
func rebind(r *Request) {
	c := r.Candidate
	c.Leg.Instrument = descriptor(c.Spec)
	r.Grid.SpecHash = hash(c.Spec)
	for i := range c.Quotes {
		c.Quotes[i].Book.Spec = c.Spec
	}
}

func TestSourceHashCachedReportMatchesEmbeddedSources(t *testing.T) {
	want := sourceHash()
	for i := 0; i < 2; i++ {
		b, err := Evaluate("/api/synthetic", []byte(controls))
		if err != nil {
			t.Fatal(err)
		}
		var r Response
		if err := json.Unmarshal(b, &r); err != nil {
			t.Fatal(err)
		}
		if r.CodeSHA256 != want {
			t.Fatalf("source hash %q, want %q", r.CodeSHA256, want)
		}
	}
}

func TestSyntheticRustHandOracle(t *testing.T) {
	b, e := Evaluate("/api/synthetic", []byte(controls))
	if e != nil {
		t.Fatal(e)
	}
	var x Response
	if e = json.Unmarshal(b, &x); e != nil {
		t.Fatal(e)
	}
	if len(x.Report.Points) != 1001 {
		t.Fatal("point count")
	}
	checkDec(t, x.Report.Points[0].GrossPayoff, "0")
	checkDec(t, x.Report.Points[500].ConditionalModelPnl, "-0.0213")
	checkDec(t, x.Report.Points[1000].ConditionalModelPnl, "0.4787")
	if x.Report.Diagnostic.Support.ExecutionEvidence.Status != "UNVERIFIED" || x.Report.Diagnostic.Support.ModeledFeasibility.Reason != "FUNDING_NOT_ASSESSED" {
		t.Fatal("authority changed")
	}
	if len(x.Report.Diagnostic.Outcomes) != 0 || x.TransportSHA256 != sha([]byte(controls)) || !validHash(x.CodeSHA256) || x.CargoLockSHA256 != nil {
		t.Fatal("envelope")
	}
	var r Request
	decode([]byte(x.RequestJSON), &r)
	if hash(r) != x.Report.InputHash {
		t.Fatal("request hash")
	}
	if evaluate(t, r).InputHash != x.Report.InputHash {
		t.Fatal("replay")
	}
}
func TestExistingRustFixtures(t *testing.T) {
	for _, name := range []string{"terminal-eth-call", "terminal-eth-straddle", "terminal-vertical"} {
		t.Run(name, func(t *testing.T) {
			b, e := os.ReadFile("../../testdata/legacy-options/" + name + ".json")
			if e != nil {
				t.Fatal(e)
			}
			var f struct {
				Quotes     []QuoteInput `json:"quotes"`
				Candidates []struct {
					Legs []CandidateLeg `json:"legs"`
					Fee  *Dec           `json:"terminal_fee_native"`
				} `json:"candidates"`
			}
			if e = json.Unmarshal(b, &f); e != nil {
				t.Fatal(e)
			}
			for i, q := range f.Quotes {
				l := f.Candidates[0].Legs[i]
				if hash(q.Book.Spec) != l.Instrument.SpecHash {
					t.Fatal("canonical identity differs from Rust fixture")
				}
				r := baseline()
				r.Candidate.Spec = q.Book.Spec
				r.Candidate.Leg = l
				r.Candidate.Quotes = []QuoteInput{q}
				r.Candidate.TerminalFee.AmountReport = decimal("0")
				r.Grid.First = decimal("90")
				r.Grid.Step = decimal("10")
				r.Grid.Count = 3
				rebind(&r)
				out := evaluate(t, r)
				if l.Side == "SELL" {
					if out.Diagnostic.Support.ModelSupport.Reason != "SINGLE_LONG_LEG_REQUIRED" {
						t.Fatal("short leg admitted")
					}
					continue
				}
				checkDec(t, out.Diagnostic.Cost.PremiumNative, q.Book.Asks[0].Price.mul(l.QuantityNative).String())
				for j, p := range out.Points {
					price := decimal([]string{"90", "100", "110"}[j])
					gross := price.sub(q.Book.Spec.Strike)
					if q.Book.Spec.OptionType == "P" {
						gross = q.Book.Spec.Strike.sub(price)
					}
					if gross.cmp(Dec{}) < 0 {
						gross = Dec{}
					}
					gross = gross.mul(l.QuantityNative)
					checkDec(t, p.GrossPayoff, gross.String())
					checkDec(t, p.ConditionalModelPnl, gross.sub(*out.Diagnostic.Cost.TotalReportCurrency).String())
				}
			}
		})
	}
}
func TestEvidenceAblations(t *testing.T) {
	cases := []struct {
		name, reason string
		change       func(*Request)
	}{
		{"quotes", "MISSING_VISIBLE_EXACT_SPEC_QUOTE", func(r *Request) { r.Candidate.Quotes = []QuoteInput{} }},
		{"terminal fee", "MISSING_TERMINAL_FEE_ASSUMPTION", func(r *Request) { r.Candidate.TerminalFee = nil }},
		{"future terminal fee", "TERMINAL_FEE_NOT_YET_KNOWN", func(r *Request) { r.Candidate.TerminalFee.KnownNs = 11 }},
		{"fees", "FEE_KNOWLEDGE_UNAVAILABLE", func(r *Request) { r.Candidate.Quotes[0].FeePolicy = nil; r.Candidate.Quotes[0].FeeKnownNs = nil }},
		{"fx", "MISSING_EXPLICIT_FX", func(r *Request) { r.Candidate.Quotes[0].QuoteToReport = nil; r.Candidate.Quotes[0].FxPair = nil }},
		{"stale", "BOOK_SOURCE_AGE_OUTSIDE_POLICY", func(r *Request) { r.CutoffNs = 200 }},
		{"depth", "INSUFFICIENT_DISPLAYED_DEPTH", func(r *Request) { r.Candidate.Quotes[0].Book.Asks[0].QuantityNative = decimal(".001") }},
		{"tick", "ILLEGAL_NATIVE_PRICE_TICK", func(r *Request) { r.Candidate.Quotes[0].Book.Asks[0].Price = decimal("2.001") }},
		{"locked book", "UNORDERED_OR_CROSSED_BOOK", func(r *Request) { r.Candidate.Quotes[0].Book.Bids[0].Price = decimal("2") }},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			r := baseline()
			r.Grid.Count = 2
			tc.change(&r)
			o := evaluate(t, r)
			if o.Diagnostic.NetStatus.Reason != tc.reason {
				t.Fatal(o.Diagnostic.NetStatus)
			}
			for _, p := range o.Points {
				if p.GrossPayoff == nil || p.ConditionalModelPnl != nil {
					t.Fatal("evidence ablation")
				}
			}
		})
	}
}
func TestUnsupportedAndPointInTime(t *testing.T) {
	for _, change := range []func(*Request){func(r *Request) { r.Candidate.Leg.Side = "SELL" }, func(r *Request) { r.Grid.AtNs = 999 }, func(r *Request) { r.Candidate.Spec.Product = "inverse_option" }, func(r *Request) { r.Candidate.Spec.ExerciseStyle = "AMERICAN" }, func(r *Request) { r.Candidate.Spec.MarginCurrency = "BTC" }, func(r *Request) { r.Candidate.MetadataKnownNs = ptr(uint64(11)) }, func(r *Request) { r.Candidate.Spec.SettlementIndex = "UNKNOWN" }, func(r *Request) { r.Candidate.Spec.ValidUntilNs = ptr(uint64(1000)) }, func(r *Request) { r.Candidate.Leg.QuantityNative = decimal(".015") }} {
		r := baseline()
		r.Grid.Count = 2
		change(&r)
		rebind(&r)
		o := evaluate(t, r)
		if o.Points[0].GrossPayoff != nil || o.Points[0].ConditionalModelPnl != nil {
			t.Fatal("unsupported admitted")
		}
	}
	r := baseline()
	r.Grid.Count = 2
	q := r.Candidate.Quotes[0]
	q.QuoteId = "later"
	q.Book.SourceNs = 11
	q.Book.ReceivedNs = 11
	q.Book.AvailableNs = 12
	q.FeePolicy = nil
	q.FeeKnownNs = nil
	r.Candidate.Quotes = append(r.Candidate.Quotes, q)
	a := evaluate(t, r)
	checkDec(t, a.Points[0].ConditionalModelPnl, "-0.0213")
	r.CutoffNs = 12
	b := evaluate(t, r)
	if b.Points[0].ConditionalModelPnl != nil || b.Diagnostic.NetStatus.Reason != "FEE_KNOWLEDGE_UNAVAILABLE" {
		t.Fatal("borrowed fees")
	}
}
func TestExactGridAndTinyPayoff(t *testing.T) {
	r := baseline()
	r.Grid.First = decimal("0")
	r.Grid.Step = decimal("0.0000000000000000000000000001")
	r.Grid.Count = 4096
	o := evaluate(t, r)
	checkDec(t, &o.Points[4095].DeliveryPrice, "0.0000000000000000000000004095")
	r = baseline()
	tiny := decimal("0.0000000000000000000000000001")
	r.Grid.First = decimal("100.0000000000000000000000000001")
	r.Grid.Step = tiny
	r.Grid.Count = 2
	r.Candidate.Spec.MinQuantityNative = tiny
	r.Candidate.Spec.QuantityStepNative = tiny
	r.Candidate.Leg.QuantityNative = tiny
	r.Candidate.Quotes = []QuoteInput{}
	rebind(&r)
	// Output values can exceed input precision; read raw strings rather than treating reports as requests.
	b, e := Evaluate("/api/curve", rawJSON(r))
	if e != nil {
		t.Fatal(e)
	}
	if !bytes.Contains(b, []byte(`"gross_payoff":"0.`+strings.Repeat("0", 55)+`1"`)) {
		t.Fatal("tiny payoff lost")
	}
}
func TestStrictBoundary(t *testing.T) {
	for _, tc := range []struct{ body, code string }{
		{`{"x":1,"x":2}`, "DUPLICATE_KEY"}, {`{"x":1.0}`, "INVALID_JSON"}, {`{} {}`, "INVALID_JSON"}, {`{`, "INVALID_JSON"}, {`{}`, "INVALID_INPUT"},
		{strings.Replace(controls, `"count":1001`, `"count":true`, 1), "INVALID_INPUT"},
		{strings.Replace(controls, `"count":1001`, `"count":null`, 1), "INVALID_INPUT"},
		{strings.Replace(controls, `"count":1001`, `"count":4097`, 1), "LIMIT_EXCEEDED"},
		{strings.Replace(controls, `"strike":"100"`, `"strike":"`+strings.Repeat("9", 65)+`"`, 1), "INVALID_INPUT"},
		{strings.Replace(controls, `"strike":"100"`, `"strike":"1e2"`, 1), "INVALID_INPUT"},
		{strings.Replace(controls, `"step":".1"`, `"step":"0"`, 1), "INVALID_INPUT"},
		{strings.Replace(controls, `"bid":"1.9"`, `"bid":"3"`, 1), "CONFLICTING_EVIDENCE"},
		{strings.Replace(controls, `"strike":"100"`, `"strike":"0.00000000000000000000000000001"`, 1), "INVALID_INPUT"},
		{strings.Replace(controls, `"count":1001`, `"count":1001,"authority":true`, 1), "INVALID_INPUT"},
		{strings.Repeat(" ", 1048577), "LIMIT_EXCEEDED"},
	} {
		errorCode(t, "/api/synthetic", []byte(tc.body), tc.code)
	}
	r := baseline()
	r.Grid.Count = 2
	r.Candidate.Quotes[0].Book.AvailableNs = 9
	errorCode(t, "/api/curve", rawJSON(r), "INVALID_TIMESTAMP")
	r = baseline()
	r.Grid.SpecHash = strings.Repeat("f", 64)
	errorCode(t, "/api/curve", rawJSON(r), "INVALID_IDENTITY")
	r = baseline()
	b := rawJSON(r)
	b = bytes.Replace(b, []byte(`"price":"2"`), []byte(`"price":"2","authority":true`), 1)
	errorCode(t, "/api/curve", b, "INVALID_INPUT")
	errorCode(t, "/etc/passwd", []byte(controls), "INVALID_INPUT")
}
func TestMissingAndManualSignal(t *testing.T) {
	r := baseline()
	r.Grid.Count = 2
	r.Signal = &ManualSignal{"manual-scenario-signal.rust.v1", "manual", strings.Repeat("a", 64), 8, 9, 11, "hypothesis"}
	a := evaluate(t, r)
	if a.SignalStatus.Status != "UNVERIFIED" {
		t.Fatal(a.SignalStatus)
	}
	r.Signal.KnownNs = 11
	r.Signal.ValidUntilNs = 12
	if evaluate(t, r).SignalStatus.Reason != "SIGNAL_NOT_YET_KNOWN" {
		t.Fatal("future signal")
	}
	r.Signal.KnownNs = 9
	r.Signal.ValidUntilNs = 10
	if evaluate(t, r).SignalStatus.Reason != "SIGNAL_EXPIRED" {
		t.Fatal("expired signal")
	}
	r.Candidate = nil
	r.Grid = nil
	r.CutoffNs = 1900000000000000001
	o := evaluate(t, r)
	if o.CutoffNs != "1900000000000000001" || len(o.Missing) != 2 || len(o.Points) != 0 {
		t.Fatal("missing contract or time")
	}
}
