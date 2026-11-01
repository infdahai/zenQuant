package options

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestVixCodeIdentityIncludesLocalFileSources(t *testing.T) {
	// Independently reconstruct the manifest from checkout bytes, including all
	// production helper files. A new unembedded helper file must fail this test.
	pairs := [][2]string{}
	for _, group := range []struct{ dir, prefix string }{{".", ""}, {"../localfile", "internal/localfile/"}} {
		entries, err := os.ReadDir(group.dir)
		if err != nil {
			t.Fatal(err)
		}
		for _, entry := range entries {
			name := entry.Name()
			if (!strings.HasSuffix(name, ".go") || strings.HasSuffix(name, "_test.go")) && !(group.dir == "." && name == "vix_demo.json") {
				continue
			}
			data, err := os.ReadFile(filepath.Join(group.dir, name))
			if err != nil {
				t.Fatal(err)
			}
			pairs = append(pairs, [2]string{group.prefix + name, sha(data)})
		}
	}
	want := hash(pairs)
	if want != embeddedSourceHash {
		t.Fatalf("code identity omits or differs from checkout sources: want %s, got %s", want, embeddedSourceHash)
	}
	b := newSnapshotRegressionBundle(t)
	out, err := b.run()
	if err != nil {
		t.Fatal(err)
	}
	var admission struct {
		Observation struct {
			Code string `json:"code_sha256"`
		} `json:"observation"`
	}
	if err := json.Unmarshal(out, &admission); err != nil {
		t.Fatal(err)
	}
	if admission.Observation.Code != want {
		t.Fatal("snapshot admission did not publish its complete code identity")
	}
	for i, pair := range pairs {
		if pair[0] == "internal/localfile/read.go" {
			pairs[i][1] = sha([]byte("changed file admission implementation"))
			if hash(pairs) == want {
				t.Fatal("changing file admission bytes did not change identity")
			}
			return
		}
	}
	t.Fatal("file admission source missing from identity")
}
