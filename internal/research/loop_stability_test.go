package research

import (
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
	"time"
)

func TestLoopReviewArtifacts(t *testing.T) {
	root := filepath.Join(t.TempDir(), "results[1]")
	write := func(path, data string) {
		t.Helper()
		if err := os.MkdirAll(filepath.Dir(path), 0755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(path, []byte(data), 0644); err != nil {
			t.Fatal(err)
		}
	}
	stale := filepath.Join(root, "framework_optimization_loop_v1")
	run := filepath.Join(stale, "runs", "round_002")
	write(filepath.Join(stale, "loop_status.json"), `{"loop_state":"campaign_running","current_run_dir":`+strconvQuote(run)+`}`)
	write(filepath.Join(run, "campaign_status.json"), `{"status":"running","phase":"stage2_full_verdict","rl_episodes_completed":4}`)
	complete := filepath.Join(root, "framework_optimization_loop_v2")
	completedRun := filepath.Join(complete, "runs", "round_003")
	write(filepath.Join(complete, "loop_status.json"), `{"loop_state":"stopped","current_run_dir":`+strconvQuote(completedRun)+`}`)
	write(filepath.Join(completedRun, "campaign_status.json"), `{"status":"completed"}`)
	write(filepath.Join(completedRun, "framework_optimization.json"), `{}`)
	write(filepath.Join(complete, "runs", "round_001", "framework_optimization.json"), `{}`)
	review, err := ReviewLoopStability(root, time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC))
	if err != nil {
		t.Fatal(err)
	}
	if len(review.Loops) != 2 || review.Loops[0].TerminalClassification != "stale_running_state" || review.Loops[0].FailureCluster != "stage2_durability" || !review.Loops[0].Stage1RLProgressSeen || review.Loops[1].TerminalClassification != "completed" || review.Loops[1].LastCompletedRound != 3 {
		t.Fatalf("unexpected review: %+v", review)
	}
	paths, err := WriteLoopReview(review, filepath.Join(root, "output"))
	if err != nil {
		t.Fatal(err)
	}
	md, err := os.ReadFile(paths["md_path"])
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(md), "`stale_running_state` | `stage2_durability` | `True`") {
		t.Fatalf("unexpected markdown: %s", md)
	}
	write(filepath.Join(stale, "loop_status.json"), `{broken`)
	if _, err := ReviewLoopStability(root, time.Now()); err == nil {
		t.Fatal("malformed status accepted")
	}
}

func strconvQuote(s string) string { return strconv.Quote(s) }
