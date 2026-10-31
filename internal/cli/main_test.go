package cli

import (
	"context"
	"fmt"
	"io"
	"net"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"testing"
	"time"
)

func TestServeFromLinkedCheckoutSubdirectory(t *testing.T) {
	if os.Getenv("ZQ_CLI_CHILD") == "1" {
		if err := Run([]string{"serve", "--engineering", "--port", os.Getenv("ZQ_CLI_PORT")}, io.Discard, os.Stderr); err != nil {
			t.Fatal(err)
		}
		return
	}
	base := t.TempDir()
	primary := filepath.Join(base, "primary")
	linked := filepath.Join(base, "linked")
	nested := filepath.Join(linked, "nested")
	gitdir := filepath.Join(primary, ".git", "worktrees", "linked")
	for _, dir := range []string{gitdir, nested, filepath.Join(linked, "options-ui", "dist")} {
		if err := os.MkdirAll(dir, 0700); err != nil {
			t.Fatal(err)
		}
	}
	for path, content := range map[string]string{filepath.Join(linked, ".git"): "gitdir: " + gitdir + "\n", filepath.Join(gitdir, "commondir"): "../..\n", filepath.Join(linked, "options-ui", "dist", "index.html"): "linked checkout asset"} {
		if err := os.WriteFile(path, []byte(content), 0600); err != nil {
			t.Fatal(err)
		}
	}
	l, err := net.Listen("tcp4", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	port := l.Addr().(*net.TCPAddr).Port
	l.Close()
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	cmd := exec.CommandContext(ctx, os.Args[0], "-test.run=^TestServeFromLinkedCheckoutSubdirectory$")
	cmd.Dir = nested
	cmd.Env = append(os.Environ(), "ZQ_CLI_CHILD=1", fmt.Sprintf("ZQ_CLI_PORT=%d", port))
	cmd.Stderr = os.Stderr
	if err := cmd.Start(); err != nil {
		t.Fatal(err)
	}
	defer func() { _ = cmd.Process.Kill(); _ = cmd.Wait() }()
	url := fmt.Sprintf("http://127.0.0.1:%d/paper", port)
	for ctx.Err() == nil {
		resp, err := http.Get(url)
		if err == nil {
			b, _ := io.ReadAll(resp.Body)
			resp.Body.Close()
			if resp.StatusCode != 200 || string(b) != "linked checkout asset" {
				t.Fatalf("%d %s", resp.StatusCode, b)
			}
			return
		}
		time.Sleep(20 * time.Millisecond)
	}
	t.Fatal("server failed to serve linked checkout assets")
}
