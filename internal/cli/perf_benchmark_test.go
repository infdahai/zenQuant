package cli

import (
	"bytes"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestBenchmarkReviewCommand(t *testing.T) {
	dir := t.TempDir()
	input := filepath.Join(dir, "status.json")
	if err := os.WriteFile(input, []byte(`{"status":"completed","stage2_prepare_variant_cache_hits":2}`), 0600); err != nil {
		t.Fatal(err)
	}
	var out bytes.Buffer
	args := []string{"review-stage2-benchmark", "--failed-status", input, "--baseline-status", input, "--rerun-status", input, "--output-dir", dir}
	if err := Run(args, &out, &out); err != nil {
		t.Fatal(err)
	}
	var result struct {
		Review         map[string]string `json:"review"`
		Classification string            `json:"classification"`
	}
	if err := json.Unmarshal(out.Bytes(), &result); err != nil {
		t.Fatal(err)
	}
	if result.Classification != "success" {
		t.Fatal(result.Classification)
	}
	for _, path := range result.Review {
		if _, err := os.Stat(path); err != nil {
			t.Fatal(err)
		}
	}
	md, err := os.ReadFile(result.Review["md_path"])
	if err != nil || !strings.Contains(string(md), "Metric comparison") {
		t.Fatalf("markdown: %s %v", md, err)
	}
	out.Reset()
	if err := Run(args, &out, &out); err != nil {
		t.Fatal(err)
	}
	var second struct {
		Review map[string]string `json:"review"`
	}
	if err := json.Unmarshal(out.Bytes(), &second); err != nil {
		t.Fatal(err)
	}
	if second.Review["json_path"] == result.Review["json_path"] {
		t.Fatal("overwrote previous review")
	}
	if err := Run([]string{"review-stage2-benchmark"}, &out, &out); err == nil {
		t.Fatal("missing arguments accepted")
	}
}
