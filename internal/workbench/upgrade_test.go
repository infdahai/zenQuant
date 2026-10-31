package workbench

import (
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/infdahai/zenQuant/internal/research"
)

func TestLegacyUpgradeVisibilityAndComparison(t *testing.T) {
	s, err := Open(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	// The v1/v2 data is serialized with the old engine labels, then stored as
	// immutable old-format bytes. V1 predates jobs; v2 may be a cancelled orphan.
	storeLegacy := func(engine string) string {
		t.Helper()
		r := Result{SnapshotID: strings.Repeat("a", 64), Engine: engine, Assumptions: "synthetic", Episodes: []research.PositionEpisode{{}}, Metrics: &Metrics{}}
		b, e := json.Marshal(r)
		if e != nil {
			t.Fatal(e)
		}
		id := digest(b)
		if e = s.publish("result", id, b); e != nil {
			t.Fatal(e)
		}
		return id
	}
	v1 := storeLegacy("fixed-buy-first-sell-last-v1")
	v2 := storeLegacy("fixed-buy-first-sell-last-v2")
	job := Job{ID: strings.Repeat("b", 64), SnapshotID: strings.Repeat("a", 64), Engine: "fixed-buy-first-sell-last-v2", Status: "cancelled"}
	if err = s.saveJob(job); err != nil {
		t.Fatal(err)
	}
	original := map[string][]byte{}
	for _, id := range []string{v1, v2} {
		original[id], err = os.ReadFile(filepath.Join(s.root, "result-"+id+".json"))
		if err != nil {
			t.Fatal(err)
		}
	}
	check := func(store *Store) {
		t.Helper()
		list, e := store.API("/api/research/list", []byte(`{"kind":"result"}`))
		if e != nil || string(list) != "[]" {
			t.Fatalf("orphan visible: %s %v", list, e)
		}
		for _, id := range []string{v1, v2} {
			if _, e = store.API("/api/research/result", []byte(`{"id":"`+id+`"}`)); e == nil || !strings.Contains(e.Error(), "manual review") {
				t.Fatalf("unlinked result: %v", e)
			}
			b, e := os.ReadFile(filepath.Join(store.root, "result-"+id+".json"))
			if e != nil || string(b) != string(original[id]) {
				t.Fatal("legacy bytes changed")
			}
		}
	}
	check(s)
	s, err = Open(s.root)
	if err != nil {
		t.Fatal(err)
	}
	w, err := StartJobs(s)
	if err != nil {
		t.Fatal(err)
	}
	defer w.Close()
	check(s)
	// A succeeded job can make a historical artifact inspectable, but the label
	// alone cannot establish which implementation produced it.
	linked := Job{ID: strings.Repeat("c", 64), SnapshotID: job.SnapshotID, Engine: job.Engine, Status: "succeeded", ResultID: v2}
	if err = s.saveJob(linked); err != nil {
		t.Fatal(err)
	}
	if _, err = s.API("/api/research/result", []byte(`{"id":"`+v2+`"}`)); err != nil {
		t.Fatal(err)
	}
	if _, err = s.API("/api/research/compare", []byte(`{"a":"`+v2+`","b":"`+v2+`"}`)); err == nil || !strings.Contains(err.Error(), "unverified") {
		t.Fatalf("legacy comparison: %v", err)
	}
}

func TestCompareRequiresMatchingVerifiedIdentity(t *testing.T) {
	engine, err := EngineIdentity()
	if err != nil {
		t.Fatal(err)
	}
	a := Result{Engine: engine, SnapshotID: "same", Episodes: []research.PositionEpisode{{}}, Metrics: &Metrics{}}
	b := a
	if _, err = Compare(a, b); err != nil {
		t.Fatal(err)
	}
	b.Engine = engine[:len(engine)-64] + strings.Repeat("a", 64)
	if b.Engine == engine {
		b.Engine = engine[:len(engine)-64] + strings.Repeat("b", 64)
	}
	if _, err = Compare(a, b); err == nil {
		t.Fatal("different engine identities compared")
	}
	for _, legacy := range []string{"fixed-buy-first-sell-last-v1", "fixed-buy-first-sell-last-v2", ""} {
		a.Engine = legacy
		b.Engine = legacy
		if _, err = Compare(a, b); err == nil || !strings.Contains(err.Error(), "unverified") {
			t.Fatalf("legacy accepted: %q %v", legacy, err)
		}
	}
}

func TestHeldCommitCountsAgainstCapacity(t *testing.T) {
	s, _ := Open(t.TempDir())
	snap, err := s.Import(fixture(t))
	if err != nil {
		t.Fatal(err)
	}
	j, err := s.Submit(snap.ID, 0)
	if err != nil {
		t.Fatal(err)
	}
	var fail atomic.Bool
	fail.Store(true)
	entered := make(chan struct{}, 1)
	s.publishHook = func(kind string) error {
		if kind == "result" && fail.Load() {
			select {
			case entered <- struct{}{}:
			default:
			}
			return errors.New("sync unavailable")
		}
		return nil
	}
	w, err := StartJobs(s)
	if err != nil {
		t.Fatal(err)
	}
	var closeOnce sync.Once
	closeWorker := func() { closeOnce.Do(w.Close) }
	t.Cleanup(closeWorker)
	select {
	case <-entered:
	case <-time.After(3 * time.Second):
		closeWorker()
		t.Fatal("commit not reached")
	}
	for i := 1; i < maxQueued; i++ {
		if _, err = s.Submit(snap.ID, 0); err != nil {
			closeWorker()
			t.Fatal(err)
		}
	}
	if _, err = s.Submit(snap.ID, 0); err == nil {
		closeWorker()
		t.Fatal("queue exceeded during commit")
	}
	fail.Store(false)
	deadline := time.Now().Add(5 * time.Second)
	for {
		jobs, e := s.JobsList()
		if e == nil {
			for _, item := range jobs {
				if item.ID == j.ID && item.Status == "succeeded" {
					goto completed
				}
			}
		}
		if time.Now().After(deadline) {
			closeWorker()
			t.Fatalf("job did not succeed: %v", e)
		}
		time.Sleep(10 * time.Millisecond)
	}
completed:
	closed := make(chan struct{})
	go func() { closeWorker(); close(closed) }()
	select {
	case <-closed:
	case <-time.After(time.Second):
		t.Fatal("shutdown blocked")
	}
	s.publishHook = nil
	reopened, _ := Open(s.root)
	worker, err := StartJobs(reopened)
	if err != nil {
		t.Fatal(err)
	}
	defer worker.Close()
	waitJob(t, reopened, j.ID, "succeeded")
}

func TestCommittingCountsAgainstQueueCapacity(t *testing.T) {
	s, _ := Open(t.TempDir())
	snap, err := s.Import(fixture(t))
	if err != nil {
		t.Fatal(err)
	}
	j, err := s.Submit(snap.ID, 0)
	if err != nil {
		t.Fatal(err)
	}
	j.Status = "committing"
	j.ResultID = strings.Repeat("d", 64)
	if err = s.saveJob(j); err != nil {
		t.Fatal(err)
	}
	for i := 1; i < maxQueued; i++ {
		if _, err = s.Submit(snap.ID, 0); err != nil {
			t.Fatal(err)
		}
	}
	if _, err = s.Submit(snap.ID, 0); err == nil {
		t.Fatal("committing job omitted from limit")
	}
	w, err := StartJobs(s)
	if err != nil {
		t.Fatal(err)
	}
	defer w.Close()
	waitJob(t, s, j.ID, "interrupted")
}
