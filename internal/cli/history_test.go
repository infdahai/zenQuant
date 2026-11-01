package cli

import (
	"bytes"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"time"

	"github.com/infdahai/zenQuant/internal/workbench"
	"golang.org/x/sys/unix"
	"path/filepath"
	"testing"
)

func TestHistoryCommandEndToEnd(t *testing.T) {
	binary := filepath.Join(t.TempDir(), "zenquant")
	build := exec.Command("go", "build", "-o", binary, "../../cmd/zenquant")
	if b, err := build.CombinedOutput(); err != nil {
		t.Fatalf("build: %v %s", err, b)
	}
	args := []string{"history-import", "--input", "../history/testdata/klines.csv", "--symbol", "BTCUSDT", "--timeframe", "4h", "--as-of", "2024-01-01T08:00:00Z"}
	output, err := exec.Command(binary, args...).Output()
	if err != nil {
		t.Fatal(err)
	}
	storePath := filepath.Join(t.TempDir(), "store")
	cmd := exec.Command(binary, "workbench", "--store", storePath, "import")
	cmd.Stdin = bytes.NewReader(output)
	result, err := cmd.CombinedOutput()
	if err != nil {
		t.Fatalf("import: %v %s", err, result)
	}
	var snap struct {
		ID      string `json:"ID"`
		Quality struct {
			Count int `json:"count"`
		} `json:"Quality"`
	}
	if err = json.Unmarshal(result, &snap); err != nil || snap.ID == "" || snap.Quality.Count != 2 {
		t.Fatalf("snapshot: %s %v", result, err)
	}
	cmd = exec.Command(binary, "workbench", "--store", storePath, "submit")
	cmd.Stdin = bytes.NewBufferString(fmt.Sprintf(`{"snapshot_id":%q,"fee_rate":0}`, snap.ID))
	jobJSON, err := cmd.CombinedOutput()
	if err != nil {
		t.Fatalf("submit: %v %s", err, jobJSON)
	}
	var job workbench.Job
	if err := json.Unmarshal(jobJSON, &job); err != nil || job.Status != "queued" {
		t.Fatalf("job: %s %v", jobJSON, err)
	}
	store, err := workbench.Open(storePath)
	if err != nil {
		t.Fatal(err)
	}
	worker, err := workbench.StartJobs(store)
	if err != nil {
		t.Fatal(err)
	}
	defer worker.Close()
	deadline := time.Now().Add(5 * time.Second)
	for time.Now().Before(deadline) {
		jobs, e := store.JobsList()
		if e != nil {
			t.Fatal(e)
		}
		if len(jobs) == 1 {
			job = jobs[0]
			if job.Status == "succeeded" || job.Status == "failed" {
				break
			}
		}
		time.Sleep(10 * time.Millisecond)
	}
	if job.Status != "succeeded" || job.ResultID == "" {
		t.Fatalf("replay job: %+v", job)
	}
	cmd = exec.Command(binary, "workbench", "--store", storePath, "result")
	cmd.Stdin = bytes.NewBufferString(fmt.Sprintf(`{"id":%q}`, job.ResultID))
	result, err = cmd.CombinedOutput()
	if err != nil {
		t.Fatalf("result: %v %s", err, result)
	}
	var replay struct {
		SnapshotID string            `json:"snapshot_id"`
		Reports    []json.RawMessage `json:"reports"`
	}
	if err = json.Unmarshal(result, &replay); err != nil || replay.SnapshotID != snap.ID || len(replay.Reports) != 2 {
		t.Fatalf("replay result: %s %v", result, err)
	}

	for _, kind := range []string{"symlink", "fifo", "directory"} {
		path := filepath.Join(t.TempDir(), kind)
		switch kind {
		case "symlink":
			err = os.Symlink("../history/testdata/klines.csv", path)
		case "fifo":
			err = unix.Mkfifo(path, 0600)
		case "directory":
			err = os.Mkdir(path, 0700)
		}
		if err != nil {
			t.Fatal(err)
		}
		invalid := append([]string{}, args...)
		invalid[2] = path
		if _, err = exec.Command(binary, invalid...).Output(); err == nil {
			t.Fatalf("accepted %s", kind)
		}
	}
	bad := append([]string{}, args...)
	bad[len(bad)-1] = "2024-01-01T03:00:00Z"
	cmd = exec.Command(binary, bad...)
	out, err := cmd.Output()
	if err == nil || len(out) != 0 {
		t.Fatalf("invalid command: %q %v", out, err)
	}
}
