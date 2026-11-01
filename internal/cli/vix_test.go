package cli

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/infdahai/zenQuant/internal/server"
	"golang.org/x/sys/unix"
)

// Run the real CLI entry point in a child so a FIFO regression is killable.
func TestVixCLIProcess(t *testing.T) {
	if os.Getenv("ZENQUANT_VIX_CLI_TEST") != "1" {
		return
	}
	for i, arg := range os.Args {
		if arg == "--" {
			if err := Run(os.Args[i+1:], os.Stdout, os.Stderr); err != nil {
				fmt.Fprintln(os.Stderr, err)
				os.Exit(1)
			}
			os.Exit(0)
		}
	}
	os.Exit(2)
}

func runVixCLI(t *testing.T, input string, args ...string) ([]byte, string, error) {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	cmd := exec.CommandContext(ctx, os.Args[0], append([]string{"-test.run=^TestVixCLIProcess$", "--"}, args...)...)
	cmd.Env = append(os.Environ(), "ZENQUANT_VIX_CLI_TEST=1", "GORACE=atexit_sleep_ms=0")
	cmd.Stdin = strings.NewReader(input)
	var stdout, stderr bytes.Buffer
	cmd.Stdout, cmd.Stderr = &stdout, &stderr
	err := cmd.Run()
	if ctx.Err() != nil {
		t.Fatalf("CLI blocked instead of returning promptly: %v", ctx.Err())
	}
	return stdout.Bytes(), stderr.String(), err
}

func vixDigest(b []byte) string {
	h := sha256.Sum256(b)
	return hex.EncodeToString(h[:])
}

func vixCLIInputs(t *testing.T) (string, []byte, []byte) {
	t.Helper()
	dir, err := filepath.EvalSymlinks(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	raw := []byte("synthetic evidence only")
	request, err := os.ReadFile("../options/vix_demo.json")
	if err != nil {
		t.Fatal(err)
	}
	var r map[string]any
	if err := json.Unmarshal(request, &r); err != nil {
		t.Fatal(err)
	}
	var pin func(any)
	pin = func(value any) {
		switch v := value.(type) {
		case map[string]any:
			if _, ok := v["sha256"]; ok {
				v["sha256"] = vixDigest(raw)
			}
			for _, child := range v {
				pin(child)
			}
		case []any:
			for _, child := range v {
				pin(child)
			}
		}
	}
	pin(r)
	request, err = json.MarshalIndent(r, "", "  ")
	if err != nil {
		t.Fatal(err)
	}
	manifest := []byte(fmt.Sprintf(`{"schema_version":"vix-snapshot-manifest.rust.v1","time_basis":"SOURCE_EFFECTIVE_TIMESTAMP","rights":{"status":"SYNTHETIC_TEST_ONLY","reference":"synthetic only","evidence_sha256":%q,"local_compute":true,"local_display":true,"retain_raw":true},"files":[{"filename":"raw.txt","sha256":%q}]}`, vixDigest(raw), vixDigest(raw)))
	for name, data := range map[string][]byte{"request.json": request, "manifest.json": manifest, "raw.txt": raw} {
		if err := os.WriteFile(filepath.Join(dir, name), data, 0600); err != nil {
			t.Fatal(err)
		}
	}
	return dir, request, manifest
}

func TestVixCLIFileAdmission(t *testing.T) {
	dir, request, manifest := vixCLIInputs(t)
	must := func(err error) {
		t.Helper()
		if err != nil {
			t.Fatal(err)
		}
	}
	must(os.Mkdir(filepath.Join(dir, "unused"), 0700))
	must(os.Symlink(dir, filepath.Join(dir, "alias")))
	must(unix.Mkfifo(filepath.Join(dir, "fifo"), 0600))
	must(os.WriteFile(filepath.Join(dir, "oversize"), bytes.Repeat([]byte(" "), server.BodyLimit+1), 0600))
	for _, name := range []string{"request", "manifest"} {
		must(os.Symlink(name+".json", filepath.Join(dir, name+"-link")))
		valid := map[string][]byte{"request": request, "manifest": manifest}[name]
		must(os.WriteFile(filepath.Join(dir, name+"-limit"), append(bytes.Clone(valid), bytes.Repeat([]byte(" "), server.BodyLimit-len(valid))...), 0600))
		for _, attack := range []struct{ name, path string }{
			{"fifo", dir + "/fifo"}, {"directory", dir}, {"final symlink", dir + "/" + name + "-link"},
			{"intermediate symlink", dir + "/alias/" + name + ".json"},
			{"parent traversal", dir + "/unused/../" + name + ".json"},
			{"erased symlink", dir + "/alias/../" + name + ".json"},
			{"nonexistent parent", dir + "/absent/../" + name + ".json"},
			{"oversized", dir + "/oversize"},
		} {
			t.Run(name+"/"+attack.name, func(t *testing.T) {
				paths := map[string]string{"request": dir + "/request.json", "manifest": dir + "/manifest.json"}
				paths[name] = attack.path
				out, stderr, err := runVixCLI(t, "", "vix-snapshot", "--request", paths["request"], "--manifest", paths["manifest"], "--evidence", dir)
				if err == nil || len(out) != 0 || stderr == "" {
					t.Fatalf("want failure with empty stdout, got %q, %q, %v", out, stderr, err)
				}
			})
		}
	}
	for _, path := range []string{dir + "/unused/../", dir + "/alias/../", dir + "/alias", dir + "/fifo", dir + "/request.json"} {
		t.Run("evidence/"+path, func(t *testing.T) {
			out, stderr, err := runVixCLI(t, "", "vix-snapshot", "--request", dir+"/request.json", "--manifest", dir+"/manifest.json", "--evidence", path)
			if err == nil || len(out) != 0 || !strings.Contains(stderr, "MISSING_EVIDENCE") {
				t.Fatalf("want evidence rejection and empty stdout, got %q, %q, %v", out, stderr, err)
			}
		})
	}
	for _, kind := range []string{"fifo", "directory", "oversized"} {
		t.Run("evidence file/"+kind, func(t *testing.T) {
			root, _, _ := vixCLIInputs(t)
			path := filepath.Join(root, "raw.txt")
			must(os.Remove(path))
			switch kind {
			case "fifo":
				must(unix.Mkfifo(path, 0600))
			case "directory":
				must(os.Mkdir(path, 0700))
			case "oversized":
				f, err := os.Create(path)
				must(err)
				must(f.Truncate(16*1024*1024 + 1))
				must(f.Close())
			}
			out, stderr, err := runVixCLI(t, "", "vix-snapshot", "--request", root+"/request.json", "--manifest", root+"/manifest.json", "--evidence", root)
			if err == nil || len(out) != 0 || !strings.Contains(stderr, "MISSING_EVIDENCE") {
				t.Fatalf("want evidence rejection and empty stdout, got %q, %q, %v", out, stderr, err)
			}
		})
	}
	t.Run("inclusive input limits", func(t *testing.T) {
		out, stderr, err := runVixCLI(t, "", "vix-snapshot", "--request", dir+"/request-limit", "--manifest", dir+"/manifest-limit", "--evidence", dir)
		if err != nil || !json.Valid(out) {
			t.Fatalf("limit-sized inputs rejected: %s %v", stderr, err)
		}
	})
}

func TestVixCLIEvaluateCanonicalIdentity(t *testing.T) {
	fixture, err := os.ReadFile("../options/vix_demo.json")
	if err != nil {
		t.Fatal(err)
	}
	body := " \n\t" + string(fixture)
	out, stderr, err := runVixCLI(t, body, "evaluate", "--route", "/api/vix/evaluate")
	if err != nil {
		t.Fatalf("%s %v", stderr, err)
	}
	var result struct {
		Transport string `json:"transport_sha256"`
		Request   string `json:"request_json"`
		Report    struct {
			Hash string `json:"input_sha256"`
		} `json:"report"`
	}
	if err := json.Unmarshal(out, &result); err != nil {
		t.Fatal(err)
	}
	if result.Transport != vixDigest([]byte(body)) || result.Report.Hash != "372edb85ecadf97d4b2b0b8bd64e0ac86414890687dcb3a5e8b1f61b05dd73d3" {
		t.Fatalf("incorrect transport/canonical hash: %s", out)
	}
	out, stderr, err = runVixCLI(t, `{"bad":true}`, "evaluate", "--route", "/api/vix/evaluate")
	if err == nil || len(out) != 0 || !strings.Contains(stderr, "INVALID_INPUT") {
		t.Fatalf("invalid evaluate leaked output: %s %s %v", out, stderr, err)
	}
}

func TestVixCLISnapshotRoundtrip(t *testing.T) {
	dir, request, manifest := vixCLIInputs(t)
	out, stderr, err := runVixCLI(t, "", "vix-snapshot", "--request", dir+"/request.json", "--manifest", dir+"/manifest.json", "--evidence", dir)
	if err != nil {
		t.Fatalf("%s %v", stderr, err)
	}
	var admission struct {
		ManifestHash string          `json:"manifest_sha256"`
		Integrity    string          `json:"raw_integrity"`
		Observation  json.RawMessage `json:"observation"`
	}
	if err := json.Unmarshal(out, &admission); err != nil {
		t.Fatal(err)
	}
	if admission.ManifestHash != vixDigest(manifest) || admission.Integrity != "LOCAL_PINNED_BYTES_MATCH" {
		t.Fatalf("incorrect admission: %+v", admission)
	}
	var first, second struct {
		Report         json.RawMessage `json:"report"`
		Request        string          `json:"request_json"`
		Transport      string          `json:"transport_sha256"`
		Implementation string          `json:"implementation"`
		Cargo          *string         `json:"cargo_lock_sha256"`
	}
	if err := json.Unmarshal(admission.Observation, &first); err != nil {
		t.Fatal(err)
	}
	if first.Transport != vixDigest(request) || first.Implementation != "vix-observation-native.go.v1" || first.Cargo != nil {
		t.Fatalf("incorrect Go identity: %+v", first)
	}
	out, stderr, err = runVixCLI(t, first.Request, "evaluate", "--route", "/api/vix/evaluate")
	if err != nil {
		t.Fatalf("%s %v", stderr, err)
	}
	if err := json.Unmarshal(out, &second); err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(first.Report, second.Report) || second.Transport != vixDigest([]byte(first.Request)) || first.Transport == second.Transport {
		t.Fatalf("canonical report must survive changed transport: %s", out)
	}
}
