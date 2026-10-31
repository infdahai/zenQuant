package paper

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"io"
	"os"
	"path/filepath"
	"strings"
	"syscall"
	"time"
)

const maxStoreBytes = 16 * 1024 * 1024

// Store's path is trusted application configuration, never an HTTP request field.
// NewEngineeringStore is for isolated engineering stores. The CLI resolves the
// shared main checkout authority and supplies its fixed .manual-paper path.
type Store struct {
	path        string
	engineering bool
}

func NewStore(path string) *Store            { return &Store{path: path} }
func NewEngineeringStore(path string) *Store { return &Store{path: path, engineering: true} }
func safePath(path string) (string, error) {
	for _, p := range strings.Split(filepath.ToSlash(path), "/") {
		if p == ".." {
			return "", failure("InvalidStore")
		}
	}
	p, e := filepath.Abs(path)
	if e != nil {
		return "", e
	}
	for cur := p; ; cur = filepath.Dir(cur) {
		info, e := os.Lstat(cur)
		if e != nil && !os.IsNotExist(e) {
			return "", e
		}
		if e == nil && info.Mode()&os.ModeSymlink != 0 {
			return "", failure("InvalidStore")
		}
		if filepath.Dir(cur) == cur {
			break
		}
	}
	return p, nil
}

type locked struct {
	root *os.Root
	dir  *os.File
	path string
}

func (l *locked) close() {
	syscall.Flock(int(l.dir.Fd()), syscall.LOCK_UN)
	l.dir.Close()
	l.root.Close()
}
func (l *locked) same() error {
	a, e := l.dir.Stat()
	if e != nil {
		return e
	}
	p, e := safePath(l.path)
	if e != nil {
		return e
	}
	b, e := os.Stat(p)
	if e != nil || !os.SameFile(a, b) {
		return failure("DurabilityUnconfirmed")
	}
	return nil
}
func lock(path string) (*locked, error) {
	r, e := os.OpenRoot(path)
	if e != nil {
		return nil, e
	}
	d, e := r.Open(".")
	if e != nil {
		r.Close()
		return nil, e
	}
	info, e := d.Stat()
	if e != nil {
		d.Close()
		r.Close()
		return nil, e
	}
	st, ok := info.Sys().(*syscall.Stat_t)
	if !ok || st.Uid != uint32(os.Geteuid()) || info.Mode().Perm()&0022 != 0 {
		d.Close()
		r.Close()
		return nil, failure("InvalidStore")
	}
	l := &locked{r, d, path}
	if e = syscall.Flock(int(d.Fd()), syscall.LOCK_EX|syscall.LOCK_NB); e != nil {
		d.Close()
		r.Close()
		return nil, failure("Busy")
	}
	if e = l.same(); e != nil {
		l.close()
		return nil, e
	}
	return l, nil
}
func (l *locked) open(name string) (*os.File, error) {
	f, e := l.root.OpenFile(name, os.O_RDONLY|syscall.O_NOFOLLOW|syscall.O_NONBLOCK, 0)
	if e != nil {
		return nil, e
	}
	i, e := f.Stat()
	if e != nil {
		f.Close()
		return nil, e
	}
	st, ok := i.Sys().(*syscall.Stat_t)
	if !i.Mode().IsRegular() || !ok || st.Nlink != 1 || st.Uid != uint32(os.Geteuid()) || i.Mode().Perm()&0022 != 0 {
		f.Close()
		return nil, failure("CorruptStore")
	}
	return f, nil
}
func (l *locked) sync() error {
	if e := l.same(); e != nil {
		return e
	}
	if e := l.dir.Sync(); e != nil {
		return failure("DurabilityUnconfirmed")
	}
	parent, e := os.Open(filepath.Dir(l.path))
	if e != nil {
		return failure("DurabilityUnconfirmed")
	}
	defer parent.Close()
	if e = parent.Sync(); e != nil {
		return failure("DurabilityUnconfirmed")
	}
	return l.same()
}
func load(l *locked) (object, bool, error) {
	f, e := l.open("snapshot.json")
	if e != nil {
		return nil, false, failure("CORRUPT_OR_MISSING_PAPER_JOURNAL")
	}
	defer f.Close()
	b, e := io.ReadAll(io.LimitReader(f, maxStoreBytes+1))
	if e != nil {
		return nil, false, e
	}
	env, e := parse(b, maxStoreBytes, "envelope")
	if e != nil {
		return nil, false, e
	}
	j := obj(env["journal"])
	if digest(j) != env["sha256"] || !bytes.Equal(canonical(env), b) {
		return nil, false, failure("CORRUPT_PAPER_JOURNAL")
	}
	if e = validateJournal(j); e != nil {
		return nil, false, e
	}
	if e = f.Sync(); e != nil {
		return nil, false, failure("PAPER_DURABILITY_UNCONFIRMED")
	}
	p, e := l.open("pending.json")
	if os.IsNotExist(e) {
		return j, false, nil
	}
	if e != nil {
		return nil, false, e
	}
	p.Close()
	return j, true, nil
}

type hooks struct {
	failAt  string
	observe func(string)
}

func (h hooks) checkpoint(s string) error {
	if h.observe != nil {
		h.observe(s)
	}
	if s == h.failAt {
		return failure("WriteFailed")
	}
	return nil
}
func persist(l *locked, j object, h hooks) error {
	b := canonical(object{"journal": j, "sha256": digest(j)})
	if len(b) > maxStoreBytes {
		return failure("Limit")
	}
	if e := l.same(); e != nil {
		return e
	}
	p, e := l.open("pending.json")
	if e == nil {
		p.Close()
		if e = l.root.Remove("pending.json"); e != nil {
			return e
		}
	} else if !os.IsNotExist(e) {
		return e
	}
	if e = h.checkpoint("before_create"); e != nil {
		return e
	}
	f, e := l.root.OpenFile("pending.json", os.O_WRONLY|os.O_CREATE|os.O_EXCL|syscall.O_NOFOLLOW, 0600)
	if e != nil {
		return e
	}
	defer f.Close()
	if e = h.checkpoint("temp_created"); e != nil {
		return e
	}
	if _, e = f.Write(b); e != nil {
		return e
	}
	if e = h.checkpoint("before_file_sync"); e != nil {
		return e
	}
	if e = f.Sync(); e != nil {
		return e
	}
	if e = h.checkpoint("file_synced"); e != nil {
		return e
	}
	if e = l.same(); e != nil {
		return e
	}
	if e = l.root.Rename("pending.json", "snapshot.json"); e != nil {
		return e
	}
	if e = h.checkpoint("renamed"); e != nil {
		return failure("DurabilityUnconfirmed")
	}
	if e = l.sync(); e != nil {
		return e
	}
	if e = h.checkpoint("directory_synced"); e != nil {
		return failure("DurabilityUnconfirmed")
	}
	return nil
}

// First publication requires an owner-controlled parent. Same-UID processes are
// trusted: they can already modify the journal and its directory directly.
func trustedParent(path string) error {
	for cur := path; ; cur = filepath.Dir(cur) {
		i, e := os.Lstat(cur)
		if e != nil {
			return e
		}
		st, ok := i.Sys().(*syscall.Stat_t)
		if !ok || !i.IsDir() || (st.Uid != uint32(os.Geteuid()) && st.Uid != 0) || (i.Mode().Perm()&0022 != 0 && (i.Mode()&os.ModeSticky == 0 || st.Uid != 0)) {
			return failure("InvalidStore")
		}
		if cur == path && (st.Uid != uint32(os.Geteuid()) || i.Mode().Perm()&0022 != 0) {
			return failure("InvalidStore")
		}
		if filepath.Dir(cur) == cur {
			return nil
		}
	}
}

// A marker binds a stage to one destination. Unmarked and unexpected data are
// never reclaimed: they may belong to an older writer or another application.
func stageMarker(p string) []byte {
	sum := sha256.Sum256([]byte(filepath.Base(p)))
	return []byte("zenquant-paper-stage-v1:" + hex.EncodeToString(sum[:]))
}

func recoverStages(p string, h hooks) error {
	parentPath := filepath.Dir(p)
	parent, e := os.OpenRoot(parentPath)
	if e != nil {
		return e
	}
	defer parent.Close()
	pd, e := parent.Open(".")
	if e != nil {
		return e
	}
	defer pd.Close()
	entries, e := pd.ReadDir(-1)
	if e != nil {
		return e
	}
	for _, entry := range entries {
		if !strings.HasPrefix(entry.Name(), ".paper-init-v1-") || !entry.IsDir() {
			continue
		}
		dir, e := parent.OpenFile(entry.Name(), os.O_RDONLY|syscall.O_NOFOLLOW|syscall.O_DIRECTORY, 0)
		if e != nil {
			continue
		}
		stage, e := parent.OpenRoot(entry.Name())
		if e != nil {
			dir.Close()
			continue
		}
		rootDir, e := stage.Open(".")
		if e != nil {
			dir.Close()
			stage.Close()
			continue
		}
		a, ea := dir.Stat()
		b, eb := rootDir.Stat()
		rootDir.Close()
		if ea != nil || eb != nil || !os.SameFile(a, b) {
			dir.Close()
			stage.Close()
			continue
		}
		if syscall.Flock(int(dir.Fd()), syscall.LOCK_EX|syscall.LOCK_NB) != nil {
			dir.Close()
			stage.Close()
			continue
		}
		e = recoverStage(parent, pd, stage, dir, entry.Name(), stageMarker(p), h)
		syscall.Flock(int(dir.Fd()), syscall.LOCK_UN)
		dir.Close()
		stage.Close()
		if e != nil {
			return e
		}
	}
	return nil
}

func recoverStage(parent *os.Root, pd *os.File, stage *os.Root, dir *os.File, stageName string, marker []byte, h hooks) error {
	opened, e := dir.Stat()
	if e != nil {
		return e
	}
	current, e := parent.Lstat(stageName)
	if e != nil || !current.IsDir() || !os.SameFile(opened, current) {
		return nil
	}
	st, ok := opened.Sys().(*syscall.Stat_t)
	if !ok || st.Uid != uint32(os.Geteuid()) || opened.Mode().Perm()&0077 != 0 {
		return nil
	}
	validFile := func(root *os.Root, name string) bool {
		i, e := root.Lstat(name)
		if e != nil || !i.Mode().IsRegular() {
			return false
		}
		s, ok := i.Sys().(*syscall.Stat_t)
		return ok && s.Nlink == 1 && s.Uid == uint32(os.Geteuid()) && i.Mode().Perm()&0077 == 0
	}
	if !validFile(stage, "owner") {
		return nil
	}
	f, e := stage.OpenFile("owner", os.O_RDONLY|syscall.O_NOFOLLOW|syscall.O_NONBLOCK, 0)
	if e != nil {
		return nil
	}
	b, e := io.ReadAll(io.LimitReader(f, 256))
	f.Close()
	if e != nil || !bytes.Equal(b, marker) {
		return nil
	}
	entries, e := dir.ReadDir(-1)
	if e != nil {
		return e
	}
	var journal *os.Root
	for _, entry := range entries {
		switch entry.Name() {
		case "owner":
		case "journal":
			i, err := stage.Lstat("journal")
			if err != nil || !i.IsDir() {
				return nil
			}
			s, ok := i.Sys().(*syscall.Stat_t)
			if !ok || s.Uid != uint32(os.Geteuid()) || i.Mode().Perm()&0077 != 0 {
				return nil
			}
			journal, err = stage.OpenRoot("journal")
			if err != nil {
				return nil
			}
			defer journal.Close()
			jd, err := journal.Open(".")
			if err != nil {
				return nil
			}
			opened, err := jd.Stat()
			jd.Close()
			if err != nil || !os.SameFile(i, opened) {
				return nil
			}
		default:
			return nil
		}
	}
	if journal != nil {
		jd, err := journal.Open(".")
		if err != nil {
			return err
		}
		children, err := jd.ReadDir(-1)
		jd.Close()
		if err != nil {
			return err
		}
		for _, child := range children {
			if (child.Name() != "pending.json" && child.Name() != "snapshot.json") || !validFile(journal, child.Name()) {
				return nil
			}
		}
		current, err := parent.Lstat(stageName)
		if err != nil || !os.SameFile(opened, current) {
			return nil
		}
		for _, child := range children {
			if err = journal.Remove(child.Name()); err != nil {
				return err
			}
		}
		if err = h.checkpoint("cleanup_files"); err != nil {
			return err
		}
		if err = stage.Remove("journal"); err != nil {
			return err
		}
	}
	if e = h.checkpoint("cleanup_journal"); e != nil {
		return e
	}
	current, e = parent.Lstat(stageName)
	if e != nil || !os.SameFile(opened, current) {
		return nil
	}
	if e = stage.Remove("owner"); e != nil {
		return e
	}
	if e = parent.Remove(stageName); e != nil {
		return e
	}
	if e = pd.Sync(); e != nil {
		return failure("DurabilityUnconfirmed")
	}
	return nil
}

func initialize(p string, c object, clock func() uint64, h hooks) (result object, replayed bool, pending bool, resultErr error) {
	if _, _, e := confirm(emptyJournal(), c, clock()); e != nil {
		return nil, false, false, e
	}
	parentPath := filepath.Dir(p)
	if e := trustedParent(parentPath); e != nil {
		return nil, false, false, e
	}
	parent, e := os.Open(parentPath)
	if e != nil {
		return nil, false, false, e
	}
	defer parent.Close()
	stageParent, e := os.MkdirTemp(parentPath, ".paper-init-v1-")
	if e != nil {
		return nil, false, false, e
	}
	stageFD, e := os.Open(stageParent)
	if e != nil {
		return nil, false, false, e
	}
	defer stageFD.Close()
	if e = syscall.Flock(int(stageFD.Fd()), syscall.LOCK_EX|syscall.LOCK_NB); e != nil {
		return nil, false, false, e
	}
	defer syscall.Flock(int(stageFD.Fd()), syscall.LOCK_UN)
	marker, e := os.OpenFile(filepath.Join(stageParent, "owner"), os.O_WRONLY|os.O_CREATE|os.O_EXCL|syscall.O_NOFOLLOW, 0600)
	if e != nil {
		return nil, false, false, e
	}
	_, e = marker.Write(stageMarker(p))
	if e == nil {
		e = marker.Sync()
	}
	if closeErr := marker.Close(); e == nil {
		e = closeErr
	}
	if e != nil {
		return nil, false, false, e
	}
	if e = stageFD.Sync(); e != nil {
		return nil, false, false, e
	}
	defer func() {
		parentRoot, err := os.OpenRoot(parentPath)
		if err == nil {
			defer parentRoot.Close()
			stageRoot, openErr := parentRoot.OpenRoot(filepath.Base(stageParent))
			if openErr == nil {
				defer stageRoot.Close()
				err = recoverStage(parentRoot, parent, stageRoot, stageFD, filepath.Base(stageParent), stageMarker(p), hooks{})
			} else {
				err = openErr
			}
		}
		if err != nil && resultErr == nil {
			resultErr = failure("DurabilityUnconfirmed")
		}
	}()
	stage := filepath.Join(stageParent, "journal")
	if e = os.Mkdir(stage, 0700); e != nil {
		return nil, false, false, e
	}
	l, e := lock(stage)
	if e != nil {
		return nil, false, false, e
	}
	defer l.close()
	next, _, e := confirm(emptyJournal(), c, clock())
	if e != nil {
		return nil, false, false, e
	}
	if e = persist(l, next, h); e != nil {
		return nil, false, false, e
	}
	if e = h.checkpoint("before_publish"); e != nil {
		return nil, false, false, e
	}
	if e = trustedParent(parentPath); e != nil {
		return nil, false, false, e
	}
	parentInfo, e := parent.Stat()
	if e != nil {
		return nil, false, false, e
	}
	currentInfo, e := os.Stat(parentPath)
	if e != nil || !os.SameFile(parentInfo, currentInfo) {
		return nil, false, false, failure("InvalidStore")
	}
	if e = l.same(); e != nil {
		return nil, false, false, e
	}
	if e = renameExclusive(int(stageFD.Fd()), "journal", int(parent.Fd()), filepath.Base(p)); e != nil {
		if os.IsExist(e) {
			// The winner keeps the directory lock until its parent fsync finishes.
			for i := 0; i < 100; i++ {
				l, err := lock(p)
				if err == nil {
					defer l.close()
					j, pending, err := load(l)
					if err != nil {
						return nil, false, false, err
					}
					next, replay, err := confirm(j, c, clock())
					if err != nil {
						return nil, false, false, err
					}
					if replay {
						err = l.sync()
					} else {
						err = persist(l, next, h)
					}
					return next, replay, pending && replay, err
				}
				if err.Error() != "Busy" {
					return nil, false, false, err
				}
				time.Sleep(10 * time.Millisecond)
			}
			return nil, false, false, failure("Busy")
		}
		return nil, false, false, e
	}
	l.path = p
	if e = h.checkpoint("published"); e != nil {
		return nil, false, false, failure("DurabilityUnconfirmed")
	}
	if e = l.sync(); e != nil {
		return nil, false, false, e
	}
	if e = h.checkpoint("parent_synced"); e != nil {
		return nil, false, false, failure("DurabilityUnconfirmed")
	}
	return next, false, false, nil
}
func (s *Store) read() (object, bool, error) {
	p, e := safePath(s.path)
	if e != nil {
		return nil, false, e
	}
	if e = trustedParent(filepath.Dir(p)); e != nil {
		return nil, false, e
	}
	if _, e = os.Lstat(p); os.IsNotExist(e) {
		return emptyJournal(), false, nil
	} else if e != nil {
		return nil, false, e
	}
	l, e := lock(p)
	if e != nil {
		return nil, false, e
	}
	defer l.close()
	j, pending, e := load(l)
	if e != nil {
		return nil, false, e
	}
	e = l.sync()
	return j, pending, e
}
func (s *Store) commit(c object, clock func() uint64, h hooks) (object, bool, bool, error) {
	p, e := safePath(s.path)
	if e != nil {
		return nil, false, false, e
	}
	if e = trustedParent(filepath.Dir(p)); e != nil {
		return nil, false, false, e
	}
	if e = recoverStages(p, h); e != nil {
		return nil, false, false, e
	}
	j := emptyJournal()
	pending := false
	var l *locked
	if _, e = os.Lstat(p); os.IsNotExist(e) {
		return initialize(p, c, clock, h)
	} else {
		if e != nil {
			return nil, false, false, e
		}
		l, e = lock(p)
		if e != nil {
			return nil, false, false, e
		}
		j, pending, e = load(l)
		if e != nil {
			l.close()
			return nil, false, false, e
		}
	}
	defer l.close()
	next, replay, e := confirm(j, c, clock())
	if e != nil {
		return nil, false, false, e
	}
	if replay {
		e = l.sync()
	} else {
		e = persist(l, next, h)
	}
	return next, replay, pending && replay, e
}

// API accepts the existing /api/paper/state, /preview and /confirm JSON contracts.
func (s *Store) API(path string, body []byte) ([]byte, error) {
	return s.api(path, body, func() uint64 { return uint64(time.Now().UnixMilli()) }, hooks{})
}
func (s *Store) api(path string, body []byte, clock func() uint64, h hooks) ([]byte, error) {
	var j, p object
	var pending, replay bool
	var e error
	switch path {
	case "/api/paper/state":
		if _, e = parse(body, 32, "empty"); e != nil {
			return nil, e
		}
		j, pending, e = s.read()
	case "/api/paper/preview":
		var c object
		c, e = parse(body, 1048576, "command")
		if e != nil {
			return nil, e
		}
		j, pending, e = s.read()
		if e == nil {
			p, e = preview(j, c, clock())
		}
	case "/api/paper/confirm":
		var c object
		c, e = parse(body, 1048576, "confirmation")
		if e != nil {
			return nil, e
		}
		j, replay, pending, e = s.commit(c, clock, h)
	default:
		return nil, failure("UNKNOWN_PAPER_ROUTE")
	}
	if e != nil {
		return nil, e
	}
	v, e := materialize(j)
	if e != nil {
		return nil, e
	}
	b := []any{"NO_EXECUTION_AUTHORITY"}
	var pv any
	if p != nil {
		pv = p
		a := obj(obj(p["command"])["action"])
		if a["kind"] == "RECORD_INTENT" {
			b = blockers(obj(a["value"]), clock())
		}
	}
	scope := "REPOSITORY_SCOPED_SIMULATION_DECLARATION"
	if s.engineering {
		scope = "ENGINEERING_TEST_ONLY"
	}
	return canonical(object{"schema_version": "manual-paper-response.rust.v1", "storage_scope": scope, "view": v, "preview": pv, "blockers": b, "replayed": replay, "uncommitted_pending": pending}), nil
}
