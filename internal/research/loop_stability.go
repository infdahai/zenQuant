package research

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"time"
)

type LoopReviewItem struct {
	LoopID                     string `json:"loop_id"`
	LastCompletedRound         int    `json:"last_completed_round"`
	TerminalClassification     string `json:"terminal_classification"`
	FailureCluster             string `json:"failure_cluster"`
	StaleStateDetected         bool   `json:"stale_state_detected"`
	BootstrapStatusPresent     bool   `json:"bootstrap_status_present"`
	Stage1RLProgressSeen       bool   `json:"stage1_rl_progress_seen"`
	Stage2TerminalStateWritten bool   `json:"stage2_terminal_state_written"`
}

type LoopReview struct {
	GeneratedAt string           `json:"generated_at"`
	ResultsRoot string           `json:"results_root"`
	LoopCount   int              `json:"loop_count"`
	Loops       []LoopReviewItem `json:"loops"`
}

func readLoopStatus(path string) (map[string]any, error) {
	b, err := os.ReadFile(path)
	if os.IsNotExist(err) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	var value map[string]any
	if err := json.Unmarshal(b, &value); err != nil {
		return nil, fmt.Errorf("%s: %w", path, err)
	}
	return value, nil
}

func loopString(m map[string]any, key string) string {
	v, _ := m[key].(string)
	return v
}

func literalGlobPath(path string) string {
	return strings.NewReplacer("\\", "\\\\", "*", "\\*", "?", "\\?", "[", "\\[").Replace(path)
}

// ReviewLoopStability reads local campaign artifacts; it does not run a campaign.
func ReviewLoopStability(root string, now time.Time) (LoopReview, error) {
	root = filepath.Clean(root)
	review := LoopReview{GeneratedAt: now.UTC().Format("2006-01-02T15:04:05.000000+00:00"), ResultsRoot: root, Loops: []LoopReviewItem{}}
	dirs, err := filepath.Glob(filepath.Join(literalGlobPath(root), "framework_optimization_loop_v*"))
	if err != nil {
		return review, err
	}
	sort.Strings(dirs)
	for _, dir := range dirs {
		info, err := os.Stat(dir)
		if err != nil {
			return review, err
		}
		if !info.IsDir() {
			continue
		}
		loop, err := readLoopStatus(filepath.Join(dir, "loop_status.json"))
		if err != nil {
			return review, err
		}
		var campaign map[string]any
		report := false
		if len(loop) > 0 {
			run := loopString(loop, "current_run_dir")
			if run == "" {
				run = "."
			}
			campaign, err = readLoopStatus(filepath.Join(run, "campaign_status.json"))
			if err != nil {
				return review, err
			}
			_, err = os.Stat(filepath.Join(run, "framework_optimization.json"))
			if err == nil {
				report = true
			} else if !os.IsNotExist(err) {
				return review, err
			}
		}
		item := LoopReviewItem{LoopID: filepath.Base(dir), BootstrapStatusPresent: campaign != nil}
		paths, err := filepath.Glob(filepath.Join(literalGlobPath(dir), "runs", "round_*", "framework_optimization.json"))
		if err != nil {
			return review, err
		}
		for _, path := range paths {
			name := filepath.Base(filepath.Dir(path))
			parts := strings.Split(name, "_")
			n, err := strconv.Atoi(parts[len(parts)-1])
			if err != nil {
				return review, fmt.Errorf("invalid round %q: %w", name, err)
			}
			if n > item.LastCompletedRound {
				item.LastCompletedRound = n
			}
		}
		state, status, phase := loopString(loop, "loop_state"), loopString(campaign, "status"), loopString(campaign, "phase")
		item.StaleStateDetected = len(loop) > 0 && len(campaign) > 0 && (state == "campaign_running" || state == "failed") && status == "running"
		switch {
		case report && status == "completed":
			item.TerminalClassification = "completed"
		case item.StaleStateDetected:
			item.TerminalClassification = "stale_running_state"
		case campaign == nil:
			item.TerminalClassification = "bootstrap_failed_before_status"
		case status == "failed":
			item.TerminalClassification = "failed_terminal_state"
		case !report && state == "failed":
			item.TerminalClassification = "failed_missing_final_report"
		case state == "stopped":
			item.TerminalClassification = "stopped"
		default:
			item.TerminalClassification = "incomplete_or_unknown"
		}
		switch {
		case strings.Contains(loopString(loop, "stop_reason"), "bootstrap") || campaign == nil:
			item.FailureCluster = "bootstrap_durability"
		case phase == "stage1_rl" || loopString(campaign, "rl_failure_driver") != "":
			item.FailureCluster = "stage1_rl_startup_fragility"
		case phase == "stage2_full_verdict":
			item.FailureCluster = "stage2_durability"
		default:
			item.FailureCluster = "unknown_or_completed"
		}
		episodes, _ := campaign["rl_episodes_completed"].(float64)
		item.Stage1RLProgressSeen = episodes > 0
		item.Stage2TerminalStateWritten = len(campaign) > 0 && (status == "completed" || status == "failed" || phase == "stage3_complete")
		review.Loops = append(review.Loops, item)
	}
	review.LoopCount = len(review.Loops)
	return review, nil
}

func WriteLoopReview(review LoopReview, output string) (map[string]string, error) {
	return writeLoopReview(review, output, writeLoopReportFile)
}

func writeLoopReportFile(path string, data []byte) error {
	f, err := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
	if err != nil {
		return err
	}
	_, err = f.Write(data)
	if err == nil {
		err = f.Sync()
	}
	if closeErr := f.Close(); err == nil {
		err = closeErr
	}
	return err
}

func writeLoopReview(review LoopReview, output string, writeFile func(string, []byte) error) (map[string]string, error) {
	b, err := json.MarshalIndent(review, "", "  ")
	if err != nil {
		return nil, err
	}
	var md strings.Builder
	fmt.Fprintf(&md, "# Framework Optimization Loop Stability Review\n\n- generated_at: `%s`\n- loop_count: `%d`\n\n| loop_id | last_completed_round | terminal_classification | failure_cluster | stale_state_detected | bootstrap_status_present | stage1_rl_progress_seen | stage2_terminal_state_written |\n|---|---:|---|---|---|---|---|---|\n", review.GeneratedAt, review.LoopCount)
	pyBool := func(v bool) string {
		if v {
			return "True"
		}
		return "False"
	}
	for _, item := range review.Loops {
		fmt.Fprintf(&md, "| `%s` | %d | `%s` | `%s` | `%s` | `%s` | `%s` | `%s` |\n", item.LoopID, item.LastCompletedRound, item.TerminalClassification, item.FailureCluster, pyBool(item.StaleStateDetected), pyBool(item.BootstrapStatusPresent), pyBool(item.Stage1RLProgressSeen), pyBool(item.Stage2TerminalStateWritten))
	}
	if err := os.MkdirAll(output, 0755); err != nil {
		return nil, err
	}
	run, err := os.MkdirTemp(output, "loop-stability-v2-")
	if err != nil {
		return nil, err
	}
	published := false
	defer func() {
		if !published {
			_ = os.RemoveAll(run)
		}
	}()
	stage := filepath.Join(run, ".staging")
	if err := os.Mkdir(stage, 0700); err != nil {
		return nil, err
	}
	const jsonName = "framework_optimization_loop_stability_review.json"
	const mdName = "framework_optimization_loop_stability_review.md"
	if err := writeFile(filepath.Join(stage, jsonName), b); err != nil {
		return nil, err
	}
	if err := writeFile(filepath.Join(stage, mdName), []byte(md.String())); err != nil {
		return nil, err
	}
	stageDir, err := os.Open(stage)
	if err != nil {
		return nil, err
	}
	err = stageDir.Sync()
	if closeErr := stageDir.Close(); err == nil {
		err = closeErr
	}
	if err != nil {
		return nil, err
	}
	final := filepath.Join(run, "report-v2")
	if err := os.Rename(stage, final); err != nil {
		return nil, err
	}
	published = true
	runDir, err := os.Open(run)
	if err != nil {
		return nil, fmt.Errorf("report published at %s; durability unconfirmed: %w", final, err)
	}
	err = runDir.Sync()
	if closeErr := runDir.Close(); err == nil {
		err = closeErr
	}
	if err != nil {
		return nil, fmt.Errorf("report published at %s; durability unconfirmed: %w", final, err)
	}
	parent, err := os.Open(output)
	if err != nil {
		return nil, fmt.Errorf("report published at %s; durability unconfirmed: %w", final, err)
	}
	err = parent.Sync()
	if closeErr := parent.Close(); err == nil {
		err = closeErr
	}
	if err != nil {
		return nil, fmt.Errorf("report published at %s; durability unconfirmed: %w", final, err)
	}
	return map[string]string{"json_path": filepath.Join(final, jsonName), "md_path": filepath.Join(final, mdName)}, nil
}
