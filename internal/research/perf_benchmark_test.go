package research

import (
	"encoding/json"
	"math"
	"os"
	"reflect"
	"testing"
	"time"
)

func TestBenchmarkPythonGolden(t *testing.T) {
	data, err := os.ReadFile("testdata/perf_benchmark_golden.json")
	if err != nil {
		t.Fatal(err)
	}
	var cases []struct {
		Failed, Baseline, Rerun map[string]any
		ReportPresent           bool `json:"report_present"`
		Expected                map[string]any
	}
	if err := json.Unmarshal(data, &cases); err != nil {
		t.Fatal(err)
	}
	for i, c := range cases {
		got, err := BuildBenchmarkReview(c.Failed, c.Baseline, c.Rerun, c.ReportPresent, BenchmarkSources{Baseline: c.Expected["baseline_source"].(string), Rerun: c.Expected["rerun_source"].(string)}, time.Unix(0, 0))
		if err != nil {
			t.Fatalf("case %d: %v", i, err)
		}
		b, err := json.Marshal(got)
		if err != nil {
			t.Fatal(err)
		}
		var actual map[string]any
		if err := json.Unmarshal(b, &actual); err != nil {
			t.Fatal(err)
		}
		delete(actual, "generated_at")
		delete(actual, "failed_source")
		if !reflect.DeepEqual(actual, c.Expected) {
			t.Errorf("case %d mismatch\ngot %s\nwant %v", i, b, c.Expected)
		}
	}
}

func TestBenchmarkDirectNumericInputs(t *testing.T) {
	values := []any{int(2), int8(2), int16(2), int32(2), int64(2), uint(2), uint8(2), uint16(2), uint32(2), uint64(2), float32(2), float64(2), json.Number("2")}
	for _, value := range values {
		review, err := BuildBenchmarkReview(nil, map[string]any{"stage2_prepare_variant_cache_hits": 1}, map[string]any{"status": "completed", "stage2_prepare_variant_cache_hits": value}, false, BenchmarkSources{}, time.Time{})
		if err != nil {
			t.Fatalf("%T: %v", value, err)
		}
		delta := review.MetricComparison["stage2_prepare_variant_cache_hits"].Delta
		if review.Classification != "success" || delta == nil || *delta != 1 {
			t.Errorf("%T: classification=%s delta=%v", value, review.Classification, delta)
		}
	}
}

func TestBenchmarkRejectsInvalidNumbers(t *testing.T) {
	for _, v := range []any{"1", math.Inf(1), float32(math.Inf(-1)), json.Number("invalid"), true} {
		if _, err := BuildBenchmarkReview(nil, nil, map[string]any{"stage1_runtime_s": v}, false, BenchmarkSources{}, time.Time{}); err == nil {
			t.Errorf("accepted %v", v)
		}
	}
}
