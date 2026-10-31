package options

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func vixFixture(t *testing.T) VixRequest {
	t.Helper()
	b, e := os.ReadFile("vix_demo.json")
	if e != nil {
		t.Fatal(e)
	}
	return decodeVix(b)
}
func TestVixScenarios(t *testing.T) {
	for _, tc := range []struct{ scenario, status, reason, classification string }{{"BACKWARDATION", "READY", "AT_INPUT_CUTOFF_ONLY", "BACKWARDATION"}, {"CONTANGO", "READY", "AT_INPUT_CUTOFF_ONLY", "CONTANGO"}, {"FLAT", "READY", "AT_INPUT_CUTOFF_ONLY", "FLAT"}, {"MISSING", "MISSING", "SELECTED_MONTHLY_QUOTE_REQUIRED", ""}, {"STALE", "STALE", "QUOTE_EXPIRED", ""}, {"ASYNCHRONOUS", "INVALID", "ASYNCHRONOUS_OR_DIFFERENT_SESSION_QUOTES", ""}, {"ROLL", "READY", "AT_INPUT_CUTOFF_ONLY", "CONTANGO"}} {
		t.Run(tc.scenario, func(t *testing.T) {
			b, e := Evaluate("/api/vix/demo", []byte(`{"scenario":"`+tc.scenario+`"}`))
			if e != nil {
				t.Fatal(e)
			}
			var got struct {
				Report vixReport `json:"report"`
			}
			if e = json.Unmarshal(b, &got); e != nil {
				t.Fatal(e)
			}
			p := got.Report.Futures
			if p.Status != tc.status || p.Reason != tc.reason {
				t.Fatalf("%+v", p)
			}
			if p.Metrics != nil && p.Metrics.Classification != tc.classification {
				t.Fatal(p.Metrics.Classification)
			}
			if tc.scenario == "ROLL" && (p.Selected[0].Symbol != "VX/X6" || p.Roll != "CHANGED_FROM_SUPPLIED_PAIR") {
				t.Fatal(p)
			}
		})
	}
}
func TestVixBoundariesAndFraction(t *testing.T) {
	r := vixFixture(t)
	if got := vixAnalyze(r).Hash; got != "372edb85ecadf97d4b2b0b8bd64e0ac86414890687dcb3a5e8b1f61b05dd73d3" {
		t.Fatalf("fixture identity: %s", got)
	}
	r.Futures.Quotes[0].Price.Value = ptr(decimal("1"))
	r.Futures.Quotes[1].Price.Value = ptr(decimal("21"))
	r.MaxAge = 1000
	p := vixAnalyze(r).Futures
	if p.Status != "READY" || p.Metrics.Ratio.Exact != nil || p.Metrics.Ratio.Numerator.String() != "1" {
		t.Fatal(p)
	}
	r.MaxAge = 999
	if p = vixAnalyze(r).Futures; p.Status != "STALE" {
		t.Fatal(p)
	}
	r = vixFixture(t)
	r.Cutoff = r.Futures.Calendar.Contracts[0].Last
	p = vixAnalyze(r).Futures
	if p.Selected[0].Symbol != "VX/X6" || p.Status != "STALE" {
		t.Fatal(p)
	}
	r.Spot = nil
	if vixAnalyze(r).Spot.Status != "MISSING" {
		t.Fatal("spot must be independent")
	}
}
func TestVixStrictInput(t *testing.T) {
	b, e := os.ReadFile("vix_demo.json")
	if e != nil {
		t.Fatal(e)
	}
	for _, body := range []string{strings.Replace(string(b), `"mode": "DEMONSTRATION"`, `"mode": "DEMONSTRATION", "mode": "DEMONSTRATION"`, 1), strings.Replace(string(b), `"kind": "SETTLEMENT"`, `"kind": "SETTLEMENT", "bid": null`, 1), strings.Replace(string(b), `"cutoff_ms": 1792180800000`, `"cutoff_ms": 1.5`, 1)} {
		if _, err := Evaluate("/api/vix/evaluate", []byte(body)); err == nil {
			t.Fatal("invalid request admitted")
		}
	}
}
func TestVixOptionalPriceFields(t *testing.T) {
	b, err := os.ReadFile("vix_demo.json")
	if err != nil {
		t.Fatal(err)
	}
	for _, price := range []string{`{"kind":"SETTLEMENT"}`, `{"kind":"SETTLEMENT","value":null}`, `{"kind":"MIDPOINT"}`, `{"kind":"MIDPOINT","bid":"1"}`, `{"kind":"MIDPOINT","ask":"2"}`} {
		t.Run(price, func(t *testing.T) {
			body := strings.Replace(string(b), `{
          "kind": "SETTLEMENT",
          "value": "24"
        }`, price, 1)
			if body == string(b) {
				t.Fatal("fixture price not found")
			}
			out, err := Evaluate("/api/vix/evaluate", []byte(body))
			if err != nil {
				t.Fatal(err)
			}
			var result struct {
				Report vixReport `json:"report"`
			}
			if err := json.Unmarshal(out, &result); err != nil {
				t.Fatal(err)
			}
			if result.Report.Futures.Status != "MISSING" || result.Report.Futures.Reason != "MISSING_PRICE" {
				t.Fatalf("%+v", result.Report.Futures)
			}
		})
	}
	for _, price := range []string{`{"kind":"SETTLEMENT","extra":1}`, `{"kind":"MIDPOINT","value":null}`, `{"kind":"MIDPOINT","extra":null}`} {
		body := strings.Replace(string(b), `{
          "kind": "SETTLEMENT",
          "value": "24"
        }`, price, 1)
		if body == string(b) {
			t.Fatal("fixture price not found")
		}
		if _, err := Evaluate("/api/vix/evaluate", []byte(body)); err == nil {
			t.Fatalf("accepted %s", price)
		}
	}
}
func TestVixSnapshotPins(t *testing.T) {
	dir, err := filepath.EvalSymlinks(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	data := []byte("synthetic evidence")
	if e := os.WriteFile(filepath.Join(dir, "evidence"), data, 0600); e != nil {
		t.Fatal(e)
	}
	h := sha(data)
	r := vixFixture(t)
	r.Futures.Calendar.Source.SHA256 = h
	for i := range r.Futures.Quotes {
		r.Futures.Quotes[i].Source.SHA256 = h
	}
	r.Spot.Vix9d.Source.SHA256 = h
	r.Spot.Vix.Source.SHA256 = h
	m := vixManifest{"vix-snapshot-manifest.rust.v1", "SOURCE_EFFECTIVE_TIMESTAMP", vixRights{"SYNTHETIC_TEST_ONLY", "synthetic", h, true, true, true}, []vixPin{{"evidence", h}}}
	if _, e := AdmitVixSnapshot(rawJSON(r), rawJSON(m), dir); e != nil {
		t.Fatal(e)
	}
	r.Futures.Quotes[2].Source.SHA256 = strings.Repeat("a", 64)
	if _, e := AdmitVixSnapshot(rawJSON(r), rawJSON(m), dir); e == nil {
		t.Fatal("unselected quote not pinned")
	}
	r.Futures.Quotes[2].Source.SHA256 = h
	m.Basis = "SESSION_DATE_ONLY"
	if _, e := AdmitVixSnapshot(rawJSON(r), rawJSON(m), dir); e == nil {
		t.Fatal("date-only admitted")
	}
	m.Basis = "NOT_A_TIME_BASIS"
	if _, e := AdmitVixSnapshot(rawJSON(r), rawJSON(m), dir); e == nil || e.Error() != "INVALID_INPUT" {
		t.Fatalf("unknown time basis: %v", e)
	}
	m.Basis = "SOURCE_EFFECTIVE_TIMESTAMP"
	alias := filepath.Join(filepath.Dir(dir), "vix-evidence-alias")
	if e := os.Symlink(dir, alias); e != nil {
		t.Fatal(e)
	}
	defer os.Remove(alias)
	if _, e := AdmitVixSnapshot(rawJSON(r), rawJSON(m), alias); e == nil {
		t.Fatal("symlink directory admitted")
	}
	if e := os.Symlink("evidence", filepath.Join(dir, "link")); e != nil {
		t.Fatal(e)
	}
	m.Files[0].Filename = "link"
	if _, e := AdmitVixSnapshot(rawJSON(r), rawJSON(m), dir); e == nil {
		t.Fatal("symlink admitted")
	}
}
