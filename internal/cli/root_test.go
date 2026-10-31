package cli

import (
	"os"
	"path/filepath"
	"testing"
)

func TestRepositoryRootSharesLinkedAuthority(t *testing.T) {
	base := t.TempDir()
	primary := filepath.Join(base, "primary")
	linked := filepath.Join(base, "linked")
	gitdir := filepath.Join(primary, ".git", "worktrees", "task")
	for _, p := range []string{gitdir, linked} {
		if err := os.MkdirAll(p, 0700); err != nil {
			t.Fatal(err)
		}
	}
	for p, v := range map[string]string{filepath.Join(linked, ".git"): "gitdir: " + gitdir + "\n", filepath.Join(gitdir, "commondir"): "../..\n"} {
		if err := os.WriteFile(p, []byte(v), 0600); err != nil {
			t.Fatal(err)
		}
	}
	want, err := filepath.EvalSymlinks(primary)
	if err != nil {
		t.Fatal(err)
	}
	got, err := RepositoryRoot(linked)
	if err != nil || got != want {
		t.Fatalf("%s %v; want %s", got, err, want)
	}
	got, err = RepositoryRoot(primary)
	if err != nil || got != primary {
		t.Fatalf("%s %v", got, err)
	}
	if err := os.Remove(filepath.Join(gitdir, "commondir")); err != nil {
		t.Fatal(err)
	}
	if _, err := RepositoryRoot(linked); err == nil {
		t.Fatal("broken metadata must fail closed")
	}
}
