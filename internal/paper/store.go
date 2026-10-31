package paper

import (
	"bytes"
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
	if !i.Mode().IsRegular() || !ok || st.Nlink != 1 {
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
func (s *Store) read() (object, bool, error) {
	p, e := safePath(s.path)
	if e != nil {
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
	j := emptyJournal()
	pending := false
	var l *locked
	if _, e = os.Lstat(p); os.IsNotExist(e) {
		if _, _, e = confirm(j, c, clock()); e != nil {
			return nil, false, false, e
		}
		parent, e := os.OpenRoot(filepath.Dir(p))
		if e != nil {
			return nil, false, false, e
		}
		defer parent.Close()
		if e = parent.Mkdir(filepath.Base(p), 0700); e != nil {
			return nil, false, false, e
		}
		f, e := parent.Open(".")
		if e != nil {
			return nil, false, false, e
		}
		e = f.Sync()
		f.Close()
		if e != nil {
			return nil, false, false, failure("DurabilityUnconfirmed")
		}
		l, e = lock(p)
		if e != nil {
			return nil, false, false, e
		}
		for _, name := range []string{"snapshot.json", "pending.json"} {
			f, e := l.open(name)
			if f != nil {
				f.Close()
			}
			if !os.IsNotExist(e) {
				l.close()
				return nil, false, false, failure("InvalidStore")
			}
		}
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
