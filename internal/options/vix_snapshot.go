package options

import (
	"io"
	"os"
	"path/filepath"
	"strings"

	"golang.org/x/sys/unix"
)

type vixPin struct {
	Filename string `json:"filename"`
	SHA      string `json:"sha256"`
}
type vixRights struct {
	Status    string `json:"status"`
	Reference string `json:"reference"`
	Evidence  string `json:"evidence_sha256"`
	Compute   bool   `json:"local_compute"`
	Display   bool   `json:"local_display"`
	Retain    bool   `json:"retain_raw"`
}
type vixManifest struct {
	Schema string    `json:"schema_version"`
	Basis  string    `json:"time_basis"`
	Rights vixRights `json:"rights"`
	Files  []vixPin  `json:"files"`
}

// AdmitVixSnapshot validates local pinned bytes and declared rights without asserting authenticity.
func AdmitVixSnapshot(request, manifest []byte, dir string) (out []byte, err error) {
	defer catch(&err)
	var m vixManifest
	decode(manifest, &m)
	r := decodeVix(request)
	require(m.Schema == "vix-snapshot-manifest.rust.v1", "INVALID_SCHEMA")
	require(oneOf(m.Basis, "SOURCE_EFFECTIVE_TIMESTAMP", "SESSION_DATE_ONLY"), "INVALID_INPUT")
	require(m.Basis == "SOURCE_EFFECTIVE_TIMESTAMP", "UNSUPPORTED")
	rights := m.Rights
	require(oneOf(rights.Status, "SYNTHETIC_TEST_ONLY", "DECLARED_AUTHORIZED_LOCAL_ONLY"), "INVALID_INPUT")
	require((r.Mode == "DEMONSTRATION" && rights.Status == "SYNTHETIC_TEST_ONLY" || r.Mode == "SUPPLIED_SNAPSHOT_UNVERIFIED" && rights.Status == "DECLARED_AUTHORIZED_LOCAL_ONLY") && strings.TrimSpace(rights.Reference) != "" && len(rights.Reference) <= 512 && rights.Compute && rights.Display && rights.Retain && validHash(rights.Evidence), "MISSING_EVIDENCE")
	require(len(m.Files) > 0 && len(m.Files) <= 32, "LIMIT_EXCEEDED")
	names := map[string]bool{}
	hashes := map[string]bool{}
	total := 0
	root, e := openVixEvidenceDir(dir)
	require(e == nil, "MISSING_EVIDENCE")
	defer unix.Close(root)
	for _, p := range m.Files {
		good := len(p.Filename) > 0 && len(p.Filename) <= 128 && p.Filename != "." && p.Filename != ".."
		for _, c := range p.Filename {
			if !(c >= 'a' && c <= 'z' || c >= 'A' && c <= 'Z' || c >= '0' && c <= '9' || strings.ContainsRune("._-", c)) {
				good = false
			}
		}
		require(good && !names[p.Filename] && validHash(p.SHA), "INVALID_INPUT")
		names[p.Filename] = true
		fd, e := unix.Openat(root, p.Filename, unix.O_RDONLY|unix.O_NOFOLLOW|unix.O_NONBLOCK|unix.O_CLOEXEC, 0)
		require(e == nil, "MISSING_EVIDENCE")
		f := os.NewFile(uintptr(fd), p.Filename)
		info, e := f.Stat()
		if e != nil || !info.Mode().IsRegular() {
			f.Close()
			fail("MISSING_EVIDENCE")
		}
		data, e := io.ReadAll(io.LimitReader(f, 16*1024*1024+1))
		ce := f.Close()
		require(e == nil && ce == nil, "MISSING_EVIDENCE")
		require(len(data) <= 16*1024*1024, "MISSING_EVIDENCE")
		total += len(data)
		require(total <= 64*1024*1024, "LIMIT_EXCEEDED")
		require(sha(data) == p.SHA, "CONFLICTING_EVIDENCE")
		hashes[p.SHA] = true
	}
	require(hashes[rights.Evidence], "MISSING_EVIDENCE")
	pinned := func(s VixSource) { require(hashes[s.SHA256], "MISSING_EVIDENCE") }
	if r.Futures != nil {
		pinned(r.Futures.Calendar.Source)
		for _, q := range r.Futures.Quotes {
			pinned(q.Source)
		}
	}
	if r.Spot != nil {
		if r.Spot.Vix9d != nil {
			pinned(r.Spot.Vix9d.Source)
		}
		if r.Spot.Vix != nil {
			pinned(r.Spot.Vix.Source)
		}
	}
	response := vixResponse(request)
	out = rawJSON(struct {
		Schema         string      `json:"schema_version"`
		Integrity      string      `json:"raw_integrity"`
		Projection     string      `json:"projection_status"`
		Authorization  string      `json:"authorization_status"`
		Redistribution string      `json:"redistribution_authority"`
		Hash           string      `json:"manifest_sha256"`
		Manifest       vixManifest `json:"manifest"`
		Observation    jsonRaw     `json:"observation"`
	}{"vix-snapshot-admission.rust.v1", "LOCAL_PINNED_BYTES_MATCH", "SUPPLIED_PROJECTION_UNVERIFIED", "SUPPLIER_DECLARATION_NOT_INDEPENDENTLY_VERIFIED", "NONE_GRANTED_BY_ADAPTER", sha(manifest), m, jsonRaw(response)})
	return
}

func openVixEvidenceDir(path string) (int, error) {
	abs, err := filepath.Abs(path)
	if err != nil {
		return -1, err
	}
	fd, err := unix.Open(string(filepath.Separator), unix.O_RDONLY|unix.O_DIRECTORY|unix.O_CLOEXEC, 0)
	if err != nil {
		return -1, err
	}
	for _, part := range strings.Split(strings.TrimPrefix(abs, string(filepath.Separator)), string(filepath.Separator)) {
		if part == "" {
			continue
		}
		next, e := unix.Openat(fd, part, unix.O_RDONLY|unix.O_DIRECTORY|unix.O_NOFOLLOW|unix.O_CLOEXEC, 0)
		unix.Close(fd)
		if e != nil {
			return -1, e
		}
		fd = next
	}
	return fd, nil
}

type jsonRaw []byte

func (j jsonRaw) MarshalJSON() ([]byte, error) { return j, nil }
