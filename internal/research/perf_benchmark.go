package research

import (
	"encoding/json"
	"fmt"
	"math"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"
)

type BenchmarkComparison struct {
	Baseline any      `json:"baseline"`
	Rerun    any      `json:"rerun"`
	Delta    *float64 `json:"delta,omitempty"`
	noDelta  bool
}

type BenchmarkReview struct {
	GeneratedAt        string                         `json:"generated_at"`
	BenchmarkScope     string                         `json:"benchmark_scope"`
	FailedRootCause    string                         `json:"failed_benchmark_root_cause"`
	FailedEvidence     map[string]any                 `json:"failed_benchmark_evidence"`
	FailedSource       string                         `json:"failed_source"`
	BaselineSource     string                         `json:"baseline_source"`
	RerunSource        string                         `json:"rerun_source"`
	RerunReportSource  string                         `json:"rerun_report_source,omitempty"`
	RerunTerminalState map[string]any                 `json:"rerun_terminal_state"`
	MetricComparison   map[string]BenchmarkComparison `json:"metric_comparison"`
	Classification     string                         `json:"classification"`
	Interpretation     map[string]any                 `json:"interpretation"`
}

var benchmarkMetrics = []struct {
	name   string
	digits int
}{
	{"stage1_runtime_s", 2}, {"stage1_avg_trial_runtime_s", 4},
	{"stage1_avg_replay_run_s", 4}, {"stage1_avg_indicator_step_s", 4},
	{"trial_replay_cache_hit_rate", 4}, {"replay_context_cache_hit_rate", 4},
	{"validation_cache_hit_rate", 4}, {"window_snapshot_cache_hit_rate", 4},
	{"stage2_prepare_variant_cache_hits", 0}, {"stage2_prepare_variant_replay_reused", 0},
}

// BenchmarkSources identifies the supplied artifacts. Empty fields mean no path was supplied.
type BenchmarkSources struct {
	Failed, Baseline, Rerun, RerunReport string
}

// BuildBenchmarkReview compares offline status objects; it does not run a benchmark.
// Numeric metrics accept Go integer and floating-point types and json.Number; calculations use float64.
func BuildBenchmarkReview(failed, baseline, rerun map[string]any, reportPresent bool, sources BenchmarkSources, now time.Time) (BenchmarkReview, error) {
	r := BenchmarkReview{
		GeneratedAt:        now.UTC().Format("2006-01-02T15:04:05.000000+00:00"),
		BenchmarkScope:     "cpu_hot_path_stage2_reuse",
		FailedRootCause:    "stage1 benchmark run ended without authoritative terminal state",
		FailedEvidence:     map[string]any{"phase": failed["phase"], "status": failed["status"], "optuna_trials_completed": failed["optuna_trials_completed"], "optuna_trials_requested": failed["optuna_trials_requested"], "stage2_progress_completed": failed["stage2_progress_completed"], "framework_report_present": false},
		FailedSource:       sources.Failed,
		BaselineSource:     sources.Baseline,
		RerunSource:        sources.Rerun,
		RerunReportSource:  sources.RerunReport,
		RerunTerminalState: map[string]any{"status": rerun["status"], "phase": rerun["phase"], "best_variant": rerun["best_variant"], "framework_report_present": reportPresent},
		MetricComparison:   make(map[string]BenchmarkComparison),
	}
	for _, metric := range benchmarkMetrics {
		b, err := benchmarkNumber(baseline[metric.name])
		if err != nil {
			return r, fmt.Errorf("baseline %s: %w", metric.name, err)
		}
		v, err := benchmarkNumber(rerun[metric.name])
		if err != nil {
			return r, fmt.Errorf("rerun %s: %w", metric.name, err)
		}
		c := BenchmarkComparison{Baseline: baseline[metric.name], Rerun: rerun[metric.name]}
		if b != nil && v != nil {
			d := *v - *b
			if math.IsInf(d, 0) || math.IsNaN(d) {
				return r, fmt.Errorf("%s: non-finite delta", metric.name)
			}
			d, err = strconv.ParseFloat(strconv.FormatFloat(d, 'f', metric.digits, 64), 64)
			if err != nil {
				return r, err
			}
			c.Delta = &d
		}
		r.MetricComparison[metric.name] = c
	}
	// The reference includes null deltas for absent numeric values.
	r.MetricComparison["stage2_prepare_variant_miss_reasons"] = BenchmarkComparison{Baseline: baseline["stage2_prepare_variant_miss_reasons"], Rerun: rerun["stage2_prepare_variant_miss_reasons"], noDelta: true}
	indicator := r.MetricComparison["stage1_avg_indicator_step_s"].Delta
	hits, _ := benchmarkNumber(rerun["stage2_prepare_variant_cache_hits"])
	reused, _ := benchmarkNumber(rerun["stage2_prepare_variant_replay_reused"])
	visible := (hits != nil && *hits > 0) || (reused != nil && *reused > 0)
	improved := indicator != nil && *indicator < 0
	r.Classification = "partial_success"
	status, _ := rerun["status"].(string)
	if status != "completed" {
		r.Classification = "failed"
	} else if (indicator != nil && *indicator < -0.5) || visible {
		r.Classification = "success"
	}
	takeaway := "diagnostics are wired correctly, but measured runtime improvement remains limited"
	if indicator != nil && *indicator >= 0 && visible {
		takeaway = "stage2 reuse is now observable and the rerun completed cleanly, but indicator hot path did not improve yet"
	} else if r.Classification == "success" {
		takeaway = "rerun completed cleanly with observable cache reuse and measurable runtime improvement"
	}
	r.Interpretation = map[string]any{"indicator_hot_path_improved": improved, "stage2_reuse_visible": visible, "main_takeaway": takeaway}
	return r, nil
}

// MarshalJSON keeps numeric missing deltas explicit, while the miss-reasons pair has no delta.
func (c BenchmarkComparison) MarshalJSON() ([]byte, error) {
	if c.noDelta {
		return json.Marshal(struct {
			Baseline any `json:"baseline"`
			Rerun    any `json:"rerun"`
		}{c.Baseline, c.Rerun})
	}
	return json.Marshal(struct {
		Baseline any      `json:"baseline"`
		Rerun    any      `json:"rerun"`
		Delta    *float64 `json:"delta"`
	}{c.Baseline, c.Rerun, c.Delta})
}

func benchmarkNumber(v any) (*float64, error) {
	if v == nil {
		return nil, nil
	}
	var n float64
	switch value := v.(type) {
	case float64:
		n = value
	case float32:
		n = float64(value)
	case int:
		n = float64(value)
	case int8:
		n = float64(value)
	case int16:
		n = float64(value)
	case int32:
		n = float64(value)
	case int64:
		n = float64(value)
	case uint:
		n = float64(value)
	case uint8:
		n = float64(value)
	case uint16:
		n = float64(value)
	case uint32:
		n = float64(value)
	case uint64:
		n = float64(value)
	case json.Number:
		var err error
		n, err = value.Float64()
		if err != nil {
			return nil, fmt.Errorf("expected finite number or null")
		}
	default:
		return nil, fmt.Errorf("expected finite number or null")
	}
	if math.IsNaN(n) || math.IsInf(n, 0) {
		return nil, fmt.Errorf("expected finite number or null")
	}
	return &n, nil
}

// WriteBenchmarkReview publishes a JSON and Markdown review in an isolated directory.
func WriteBenchmarkReview(r BenchmarkReview, root string) (map[string]string, error) {
	if err := os.MkdirAll(root, 0700); err != nil {
		return nil, err
	}
	dir, err := os.MkdirTemp(root, "benchmark-review-")
	if err != nil {
		return nil, err
	}
	ok := false
	defer func() {
		if !ok {
			_ = os.RemoveAll(dir)
		}
	}()
	b, err := json.MarshalIndent(r, "", "  ")
	if err != nil {
		return nil, err
	}
	jp, mp := filepath.Join(dir, "perf_stage2_benchmark_review.json"), filepath.Join(dir, "perf_stage2_benchmark_review.md")
	if err = os.WriteFile(jp, b, 0600); err != nil {
		return nil, err
	}
	metrics, err := json.MarshalIndent(r.MetricComparison, "", "  ")
	if err != nil {
		return nil, err
	}
	md := fmt.Sprintf("# Stage2 Benchmark Review\n\n- classification: `%s`\n- failed_benchmark_root_cause: `%s`\n- main_takeaway: `%s`\n- indicator_hot_path_improved: `%t`\n- stage2_reuse_visible: `%t`\n\n## Metric comparison\n\n```json\n%s\n```\n", r.Classification, r.FailedRootCause, r.Interpretation["main_takeaway"], r.Interpretation["indicator_hot_path_improved"], r.Interpretation["stage2_reuse_visible"], strings.TrimSpace(string(metrics)))
	if err = os.WriteFile(mp, []byte(md), 0600); err != nil {
		return nil, err
	}
	ok = true
	return map[string]string{"json_path": jp, "md_path": mp}, nil
}
