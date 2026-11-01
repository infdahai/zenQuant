package cli

import (
	"bytes"
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"reflect"
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
	fixturePath, err := filepath.Abs("../history/testdata/klines.csv")
	if err != nil {
		t.Fatal(err)
	}
	args := []string{"history-import", "--input", fixturePath, "--symbol", "BTCUSDT", "--timeframe", "4h", "--as-of", "2024-01-01T08:00:00Z"}
	overflow := append([]string{}, args...)
	overflow[len(overflow)-1] = "9999-12-31T23:00:00-02:00"
	if b, err := exec.Command(binary, overflow...).Output(); err == nil || len(b) != 0 {
		t.Fatalf("unrepresentable UTC cutoff accepted: output %q, error %v", b, err)
	}
	boundary := append([]string{}, args...)
	boundary[len(boundary)-1] = "9999-12-31T22:00:00-01:00"
	boundaryOutput, err := exec.Command(binary, boundary...).Output()
	if err != nil {
		t.Fatalf("valid UTC boundary: %v", err)
	}
	boundaryStore, err := workbench.Open(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	if _, err = boundaryStore.Import(boundaryOutput); err != nil {
		t.Fatalf("import valid UTC boundary: %v", err)
	}
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
			Count          int    `json:"count"`
			CandleCoverage string `json:"candle_coverage"`
		} `json:"Quality"`
		Dataset struct {
			Provenance workbench.DatasetProvenance `json:"provenance"`
		} `json:"dataset"`
	}
	if err = json.Unmarshal(result, &snap); err != nil || snap.ID == "" || snap.Quality.Count != 2 || snap.Quality.CandleCoverage != "unknown; no requested coverage verified" {
		t.Fatalf("snapshot: %s %v", result, err)
	}
	csv, err := os.ReadFile(fixturePath)
	if err != nil {
		t.Fatal(err)
	}
	sum := sha256.Sum256(csv)
	if snap.Dataset.Provenance.SourceSHA256 != fmt.Sprintf("%x", sum) || snap.Dataset.Provenance.AsOf != "2024-01-01T08:00:00Z" {
		t.Fatalf("provenance: %+v", snap.Dataset.Provenance)
	}
	importVariant := func(a []string) string {
		t.Helper()
		b, e := exec.Command(binary, a...).Output()
		if e != nil {
			t.Fatal(e)
		}
		var original, variant struct {
			Candles []json.RawMessage `json:"candles"`
		}
		if json.Unmarshal(output, &original) != nil || json.Unmarshal(b, &variant) != nil || !reflect.DeepEqual(original.Candles, variant.Candles) {
			t.Fatal("variant changed normalized candles")
		}
		c := exec.Command(binary, "workbench", "--store", storePath, "import")
		c.Stdin = bytes.NewReader(b)
		b, e = c.CombinedOutput()
		if e != nil {
			t.Fatalf("variant import: %s %v", b, e)
		}
		var v struct {
			ID string `json:"id"`
		}
		if e = json.Unmarshal(b, &v); e != nil || v.ID == "" {
			t.Fatalf("variant: %s %v", b, e)
		}
		return v.ID
	}
	later := append([]string{}, args...)
	later[len(later)-1] = "2024-01-01T09:00:00Z"
	if id := importVariant(later); id == snap.ID {
		t.Fatal("cutoff did not change snapshot identity")
	}
	temp, err := filepath.EvalSymlinks(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	variantPath := filepath.Join(temp, "variant.csv")
	if err = os.WriteFile(variantPath, append(csv, '\n'), 0600); err != nil {
		t.Fatal(err)
	}
	changed := append([]string{}, args...)
	changed[2] = variantPath
	if id := importVariant(changed); id == snap.ID {
		t.Fatal("source bytes did not change snapshot identity")
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

	for _, kind := range []string{"symlink", "parent-symlink", "parent-component", "fifo", "directory"} {
		err = nil
		path := filepath.Join(temp, kind)
		switch kind {
		case "symlink":
			err = os.Symlink(fixturePath, path)
		case "parent-symlink":
			err = os.Symlink(temp, path)
			path += "/variant.csv"
		case "parent-component":
			path = temp + "/../" + filepath.Base(temp) + "/variant.csv"
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
