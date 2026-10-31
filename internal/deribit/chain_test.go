package deribit

import (
	"bufio"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"sync"
	"testing"
)

var testRow = Row{SnapshotTS: 1700000000000000000, Currency: "BTC"}

// Used by the Python integration test to overlap its real writer with Go publication.
func TestPythonConcurrentSaveHelper(t *testing.T) {
	root := os.Getenv("DERIBIT_CONCURRENT_ROOT")
	if root == "" {
		return
	}
	_, _, err := save([]Row{testRow}, root, func(point string) error {
		if point == "staged" {
			fmt.Println("STAGED")
			_, err := bufio.NewReader(os.Stdin).ReadString('\n')
			return err
		}
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
}

func snapshots(t *testing.T, root string) (int, int) {
	t.Helper()
	var jsonCount, parquetCount int
	err := filepath.WalkDir(filepath.Join(root, "BTC"), func(path string, d os.DirEntry, err error) error {
		if os.IsNotExist(err) {
			return nil
		}
		if err != nil {
			return err
		}
		if !d.IsDir() {
			switch filepath.Ext(path) {
			case ".json":
				jsonCount++
			case ".parquet":
				parquetCount++
			}
		}
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	return jsonCount, parquetCount
}

func TestConcurrentSave(t *testing.T) {
	root := t.TempDir()
	dir := filepath.Join(root, "BTC", "2023-11")
	if err := os.MkdirAll(dir, 0755); err != nil {
		t.Fatal(err)
	}
	existing := filepath.Join(dir, "options_chain_20231114T221320Z.json")
	if err := os.WriteFile(existing, []byte("python"), 0644); err != nil {
		t.Fatal(err)
	}
	const n = 32
	var wg sync.WaitGroup
	errs := make(chan error, n)
	for i := 0; i < n; i++ {
		wg.Add(1)
		go func() { defer wg.Done(); _, _, err := Save([]Row{testRow}, root); errs <- err }()
	}
	wg.Wait()
	close(errs)
	for err := range errs {
		if err != nil {
			t.Fatal(err)
		}
	}
	j, p := snapshots(t, root)
	if j != n+1 || p != n {
		t.Fatalf("got %d JSON, %d parquet", j, p)
	}
	b, err := os.ReadFile(existing)
	if err != nil || string(b) != "python" {
		t.Fatalf("Python file changed: %q %v", b, err)
	}
}

func TestPublicationFailuresAndCrashes(t *testing.T) {
	if root := os.Getenv("DERIBIT_CRASH_ROOT"); root != "" {
		_, _, _ = save([]Row{testRow}, root, func(point string) error {
			if point == os.Getenv("DERIBIT_CRASH_POINT") {
				os.Exit(23)
			}
			return nil
		})
		os.Exit(0)
	}
	for _, point := range []string{"created", "staged", "published"} {
		for _, crash := range []bool{false, true} {
			t.Run(point+map[bool]string{false: "-error", true: "-crash"}[crash], func(t *testing.T) {
				root := t.TempDir()
				if crash {
					cmd := exec.Command(os.Args[0], "-test.run=^TestPublicationFailuresAndCrashes$")
					cmd.Env = append(os.Environ(), "DERIBIT_CRASH_ROOT="+root, "DERIBIT_CRASH_POINT="+point)
					if err := cmd.Run(); err == nil {
						t.Fatal("expected child crash")
					}
				} else {
					_, _, err := save([]Row{testRow}, root, func(got string) error {
						if got == point {
							return errors.New("injected")
						}
						return nil
					})
					if err == nil {
						t.Fatal("expected injected error")
					}
				}
				j, p := snapshots(t, root)
				want := 0
				if point == "published" {
					want = 1
				}
				if j != want || p != want {
					t.Fatalf("got %d JSON and %d parquet, want %d each", j, p, want)
				}
			})
		}
	}
}
