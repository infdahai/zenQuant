package cli

import (
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"time"

	"github.com/infdahai/zenQuant/internal/research"
)

func runBenchmarkReview(args []string, out, stderr io.Writer) error {
	flags := flag.NewFlagSet("review-stage2-benchmark", flag.ContinueOnError)
	flags.SetOutput(stderr)
	failed := flags.String("failed-status", "", "failed campaign status JSON")
	baseline := flags.String("baseline-status", "", "baseline campaign status JSON")
	rerun := flags.String("rerun-status", "", "rerun campaign status JSON")
	report := flags.String("rerun-report", "", "optional rerun report JSON")
	output := flags.String("output-dir", "", "directory for isolated output")
	if err := flags.Parse(args); err != nil {
		if errors.Is(err, flag.ErrHelp) {
			return nil
		}
		return err
	}
	if flags.NArg() != 0 || *failed == "" || *baseline == "" || *rerun == "" || *output == "" {
		return errors.New("failed-status, baseline-status, rerun-status and output-dir are required")
	}
	read := func(path string) (map[string]any, error) {
		b, err := os.ReadFile(path)
		if err != nil {
			return nil, err
		}
		var v map[string]any
		if err = json.Unmarshal(b, &v); err != nil {
			return nil, fmt.Errorf("%s: %w", path, err)
		}
		if v == nil {
			return nil, fmt.Errorf("%s: expected JSON object", path)
		}
		return v, nil
	}
	f, err := read(*failed)
	if err != nil {
		return err
	}
	b, err := read(*baseline)
	if err != nil {
		return err
	}
	r, err := read(*rerun)
	if err != nil {
		return err
	}
	if *report != "" {
		if _, err = read(*report); err != nil {
			return err
		}
	}
	review, err := research.BuildBenchmarkReview(f, b, r, *report != "", research.BenchmarkSources{Failed: *failed, Baseline: *baseline, Rerun: *rerun, RerunReport: *report}, time.Now())
	if err != nil {
		return err
	}
	written, err := research.WriteBenchmarkReview(review, *output)
	if err != nil {
		return err
	}
	enc := json.NewEncoder(out)
	enc.SetIndent("", "  ")
	if err = enc.Encode(map[string]any{"review": written, "classification": review.Classification}); err != nil {
		return fmt.Errorf("review written at %s; stdout failed: %w", filepath.Dir(written["json_path"]), err)
	}
	return nil
}
