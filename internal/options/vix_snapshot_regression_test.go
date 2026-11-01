package options

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

type snapshotRegressionBundle struct {
	dir      string
	request  VixRequest
	manifest vixManifest
}

func newSnapshotRegressionBundle(t *testing.T) *snapshotRegressionBundle {
	t.Helper()
	dir, err := filepath.EvalSymlinks(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	raw := []byte("synthetic quote and calendar evidence")
	permission := []byte("synthetic permission evidence")
	for name, data := range map[string][]byte{"raw.txt": raw, "permission.txt": permission} {
		if err := os.WriteFile(filepath.Join(dir, name), data, 0600); err != nil {
			t.Fatal(err)
		}
	}
	r := vixFixture(t)
	h := sha(raw)
	r.Futures.Calendar.Source.SHA256 = h
	for i := range r.Futures.Quotes {
		r.Futures.Quotes[i].Source.SHA256 = h
	}
	r.Spot.Vix9d.Source.SHA256 = h
	r.Spot.Vix.Source.SHA256 = h
	return &snapshotRegressionBundle{dir, r, vixManifest{
		Schema: "vix-snapshot-manifest.rust.v1", Basis: "SOURCE_EFFECTIVE_TIMESTAMP",
		Rights: vixRights{Status: "SYNTHETIC_TEST_ONLY", Reference: "synthetic only", Evidence: sha(permission), Compute: true, Display: true, Retain: true},
		Files:  []vixPin{{Filename: "raw.txt", SHA: h}, {Filename: "permission.txt", SHA: sha(permission)}},
	}}
}

func (b *snapshotRegressionBundle) run() ([]byte, error) {
	return AdmitVixSnapshot(rawJSON(b.request), rawJSON(b.manifest), b.dir)
}

func (b *snapshotRegressionBundle) expectError(t *testing.T, code string) {
	t.Helper()
	out, err := b.run()
	if err == nil || err.Error() != code || len(out) != 0 {
		t.Fatalf("got output %q, error %v; want no output and %s", out, err, code)
	}
}

func TestVixSnapshotRegressionEvidence(t *testing.T) {
	t.Run("admitted evidence retains trust boundaries", func(t *testing.T) {
		b := newSnapshotRegressionBundle(t)
		out, err := b.run()
		if err != nil {
			t.Fatal(err)
		}
		var got struct {
			Integrity      string `json:"raw_integrity"`
			Projection     string `json:"projection_status"`
			Authorization  string `json:"authorization_status"`
			Redistribution string `json:"redistribution_authority"`
		}
		if err := json.Unmarshal(out, &got); err != nil {
			t.Fatal(err)
		}
		if got.Integrity != "LOCAL_PINNED_BYTES_MATCH" || got.Projection != "SUPPLIED_PROJECTION_UNVERIFIED" || got.Authorization != "SUPPLIER_DECLARATION_NOT_INDEPENDENTLY_VERIFIED" || got.Redistribution != "NONE_GRANTED_BY_ADAPTER" {
			t.Fatalf("unexpected admission: %+v", got)
		}
	})
	t.Run("corrupt and missing", func(t *testing.T) {
		b := newSnapshotRegressionBundle(t)
		path := filepath.Join(b.dir, "raw.txt")
		if err := os.WriteFile(path, []byte("tampered"), 0600); err != nil {
			t.Fatal(err)
		}
		b.expectError(t, "CONFLICTING_EVIDENCE")
		if err := os.Remove(path); err != nil {
			t.Fatal(err)
		}
		b.expectError(t, "MISSING_EVIDENCE")
	})
	for _, tc := range []struct {
		name   string
		change func(*snapshotRegressionBundle)
	}{
		{"calendar", func(b *snapshotRegressionBundle) { b.request.Futures.Calendar.Source.SHA256 = strings.Repeat("a", 64) }},
		{"quote", func(b *snapshotRegressionBundle) { b.request.Futures.Quotes[0].Source.SHA256 = strings.Repeat("a", 64) }},
		{"vix9d", func(b *snapshotRegressionBundle) { b.request.Spot.Vix9d.Source.SHA256 = strings.Repeat("a", 64) }},
		{"vix", func(b *snapshotRegressionBundle) { b.request.Spot.Vix.Source.SHA256 = strings.Repeat("a", 64) }},
		{"permission", func(b *snapshotRegressionBundle) { b.manifest.Rights.Evidence = strings.Repeat("a", 64) }},
	} {
		t.Run("unpinned "+tc.name, func(t *testing.T) {
			b := newSnapshotRegressionBundle(t)
			tc.change(b)
			b.expectError(t, "MISSING_EVIDENCE")
		})
	}
}

func TestVixSnapshotRegressionRightsAndModes(t *testing.T) {
	for _, tc := range []struct {
		name string
		deny func(*vixRights)
	}{
		{"compute", func(r *vixRights) { r.Compute = false }},
		{"display", func(r *vixRights) { r.Display = false }},
		{"retain", func(r *vixRights) { r.Retain = false }},
	} {
		t.Run(tc.name, func(t *testing.T) {
			b := newSnapshotRegressionBundle(t)
			tc.deny(&b.manifest.Rights)
			b.expectError(t, "MISSING_EVIDENCE")
		})
	}
	t.Run("mode declarations", func(t *testing.T) {
		b := newSnapshotRegressionBundle(t)
		b.request.Mode = "SUPPLIED_SNAPSHOT_UNVERIFIED"
		b.expectError(t, "MISSING_EVIDENCE")
		b.manifest.Rights.Status = "DECLARED_AUTHORIZED_LOCAL_ONLY"
		if _, err := b.run(); err != nil {
			t.Fatal(err)
		}
		b.request.Mode = "DEMONSTRATION"
		b.expectError(t, "MISSING_EVIDENCE")
	})
}

func TestVixSnapshotRegressionPaths(t *testing.T) {
	for _, name := range []string{"../raw.txt", "/raw.txt", "a/raw.txt", ".", "..", "https://example.com"} {
		t.Run(name, func(t *testing.T) {
			b := newSnapshotRegressionBundle(t)
			b.manifest.Files[0].Filename = name
			b.expectError(t, "INVALID_INPUT")
		})
	}
	for _, component := range []string{"unused", "alias"} {
		t.Run("lexical parent after "+component, func(t *testing.T) {
			b := newSnapshotRegressionBundle(t)
			if component == "alias" {
				if err := os.Symlink(b.dir, filepath.Join(b.dir, component)); err != nil {
					t.Fatal(err)
				}
			} else if err := os.Mkdir(filepath.Join(b.dir, component), 0700); err != nil {
				t.Fatal(err)
			}
			// Do not use Join: the unnormalized spelling is the attack.
			b.dir += "/" + component + "/.."
			b.expectError(t, "MISSING_EVIDENCE")
		})
	}
	t.Run("file symlink", func(t *testing.T) {
		b := newSnapshotRegressionBundle(t)
		path := filepath.Join(b.dir, "raw.txt")
		if err := os.Rename(path, filepath.Join(b.dir, "other.txt")); err != nil {
			t.Fatal(err)
		}
		if err := os.Symlink("other.txt", path); err != nil {
			t.Fatal(err)
		}
		b.expectError(t, "MISSING_EVIDENCE")
	})
	t.Run("intermediate directory symlink", func(t *testing.T) {
		b := newSnapshotRegressionBundle(t)
		parent, err := filepath.EvalSymlinks(t.TempDir())
		if err != nil {
			t.Fatal(err)
		}
		if err := os.Symlink(filepath.Dir(b.dir), filepath.Join(parent, "alias")); err != nil {
			t.Fatal(err)
		}
		b.dir = filepath.Join(parent, "alias", filepath.Base(b.dir))
		b.expectError(t, "MISSING_EVIDENCE")
	})
}
