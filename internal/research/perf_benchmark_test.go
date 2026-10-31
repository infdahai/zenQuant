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
		got, err := BuildBenchmarkReview(c.Failed, c.Baseline, c.Rerun, c.ReportPresent, time.Unix(0, 0))
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
		if !reflect.DeepEqual(actual, c.Expected) {
			t.Errorf("case %d mismatch\ngot %s\nwant %v", i, b, c.Expected)
		}
	}
}

func TestBenchmarkRejectsInvalidNumbers(t *testing.T) {
	for _, v := range []any{"1", math.Inf(1), true} {
		if _, err := BuildBenchmarkReview(nil, nil, map[string]any{"stage1_runtime_s": v}, false, time.Time{}); err == nil {
			t.Errorf("accepted %v", v)
		}
	}
}
