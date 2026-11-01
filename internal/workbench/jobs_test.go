package workbench

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"syscall"
	"testing"
	"time"
)

func pollJob(list func() ([]Job, error), id, status string, deadline time.Time) (Job, error) {
	var lastErr error
	for time.Now().Before(deadline) {
		jobs, err := list()
		lastErr = err
		if err != nil && !errors.Is(err, syscall.EWOULDBLOCK) {
			return Job{}, err
		}
		for _, j := range jobs {
			if j.ID == id && j.Status == status {
				return j, nil
			}
		}
		time.Sleep(10 * time.Millisecond)
	}
	if lastErr != nil {
		return Job{}, fmt.Errorf("job did not reach %s before deadline: %w", status, lastErr)
	}
	return Job{}, fmt.Errorf("job did not reach %s before deadline", status)
}

func waitJob(t *testing.T, s *Store, id string, status string) Job {
	t.Helper()
	job, err := pollJob(s.JobsList, id, status, time.Now().Add(5*time.Second))
	if err != nil {
		t.Fatal(err)
	}
	return job
}

func TestPollJobErrors(t *testing.T) {
	want := Job{ID: "job", Status: "succeeded"}
	calls := 0
	job, err := pollJob(func() ([]Job, error) {
		calls++
		if calls == 1 {
			return nil, fmt.Errorf("metadata lock: %w", syscall.EWOULDBLOCK)
		}
		return []Job{want}, nil
	}, want.ID, want.Status, time.Now().Add(time.Second))
	if err != nil || job != want || calls != 2 {
		t.Fatalf("contention retry: job=%+v calls=%d err=%v", job, calls, err)
	}

	other := errors.New("read failure")
	calls = 0
	_, err = pollJob(func() ([]Job, error) { calls++; return nil, other }, want.ID, want.Status, time.Now().Add(time.Second))
	if !errors.Is(err, other) || calls != 1 {
		t.Fatalf("unexpected error handling: calls=%d err=%v", calls, err)
	}

	_, err = pollJob(func() ([]Job, error) { return nil, syscall.EWOULDBLOCK }, want.ID, want.Status, time.Now().Add(20*time.Millisecond))
	if !errors.Is(err, syscall.EWOULDBLOCK) {
		t.Fatalf("persistent contention did not time out with lock error: %v", err)
	}
}
func TestTerminalWriteFailureDoesNotBlockCancellation(t *testing.T) {
	s, _ := Open(t.TempDir())
	snap, _ := s.Import(fixture(t))
	job, _ := s.Submit(snap.ID, 0)
	failing := make(chan struct{}, 1)
	s.jobIOHook = func(op string, j Job) error {
		if op == "write" && j.Status == "succeeded" {
			select {
			case failing <- struct{}{}:
			default:
			}
			return errors.New("injected terminal write failure")
		}
		return nil
	}
	w, e := StartJobs(s)
	if e != nil {
		t.Fatal(e)
	}
	var closeOnce sync.Once
	closeWorker := func() { closeOnce.Do(w.Close) }
	t.Cleanup(closeWorker)
	select {
	case <-failing:
	case <-time.After(3 * time.Second):
		t.Fatal("terminal write was not reached")
	}
	cancelled := make(chan struct{})
	go func() { _, _ = s.CancelJob(job.ID); close(cancelled) }()
	select {
	case <-cancelled:
	case <-time.After(time.Second):
		w.stop()
		t.Fatal("cancellation blocked behind retry")
	}
	closed := make(chan struct{})
	go func() { closeWorker(); close(closed) }()
	select {
	case <-closed:
	case <-time.After(time.Second):
		t.Fatal("shutdown blocked by persistent write failure")
	}
	s.jobIOHook = nil
	reopened, _ := Open(s.root)
	worker, e := StartJobs(reopened)
	if e != nil {
		t.Fatal(e)
	}
	defer worker.Close()
	jobs, e := reopened.JobsList()
	if e != nil {
		t.Fatal(e)
	}
	if len(jobs) != 1 || (jobs[0].Status != "cancelled" && jobs[0].Status != "interrupted" && jobs[0].Status != "succeeded") {
		t.Fatalf("unexpected recovered state: %+v", jobs)
	}
}
func TestClaimSyncFailureRecoversWithoutRestart(t *testing.T) {
	s, _ := Open(t.TempDir())
	snap, _ := s.Import(fixture(t))
	job, _ := s.Submit(snap.ID, 0)
	var once atomic.Bool
	s.jobIOHook = func(op string, j Job) error {
		if op == "dirsync" && j.Status == "running" && once.CompareAndSwap(false, true) {
			return errors.New("injected dir sync failure")
		}
		return nil
	}
	w, e := StartJobs(s)
	if e != nil {
		t.Fatal(e)
	}
	defer w.Close()
	waitJob(t, s, job.ID, "interrupted")
}

func TestReadFailureRecoversAndShutdownRestarts(t *testing.T) {
	s, _ := Open(t.TempDir())
	snap, _ := s.Import(fixture(t))
	job, _ := s.Submit(snap.ID, 0)
	var running atomic.Bool
	var failed atomic.Bool
	s.jobIOHook = func(op string, j Job) error {
		if op == "dirsync" && j.Status == "running" {
			running.Store(true)
		}
		if op == "read" && running.Load() && failed.CompareAndSwap(false, true) {
			return errors.New("injected read failure")
		}
		return nil
	}
	w, e := StartJobs(s)
	if e != nil {
		t.Fatal(e)
	}
	var closeOnce sync.Once
	firstWorker := w
	closeWorker := func() { closeOnce.Do(firstWorker.Close) }
	t.Cleanup(closeWorker)
	deadline := time.Now().Add(5 * time.Second)
	for !failed.Load() && time.Now().Before(deadline) {
		time.Sleep(10 * time.Millisecond)
	}
	if !failed.Load() {
		t.Fatal("read failure not reached")
	}
	waitJob(t, s, job.ID, "succeeded")
	closeWorker()
	reopened, e := Open(s.root)
	if e != nil {
		t.Fatal(e)
	}
	w, e = StartJobs(reopened)
	if e != nil {
		t.Fatal(e)
	}
	defer w.Close()
	waitJob(t, reopened, job.ID, "succeeded")
}

func TestShutdownWithCompetingMetadataLock(t *testing.T) {
	s, _ := Open(t.TempDir())
	w, e := StartJobs(s)
	if e != nil {
		t.Fatal(e)
	}
	l, e := s.metadataLock()
	if e != nil {
		w.Close()
		t.Fatal(e)
	}
	stopped := make(chan struct{})
	go func() { w.Close(); close(stopped) }()
	select {
	case <-stopped:
	case <-time.After(time.Second):
		unlock(l)
		t.Fatal("worker shutdown blocked by metadata lock")
	}
	unlock(l)
}

func TestResultPublicationMustSyncBeforeSuccess(t *testing.T) {
	s, _ := Open(t.TempDir())
	snap, _ := s.Import(fixture(t))
	job, _ := s.Submit(snap.ID, 0)
	var fail atomic.Bool
	fail.Store(true)
	entered := make(chan struct{}, 1)
	s.publishHook = func(kind string) error {
		if kind == "result" && fail.Load() {
			select {
			case entered <- struct{}{}:
			default:
			}
			return errors.New("injected result sync failure")
		}
		return nil
	}
	w, e := StartJobs(s)
	if e != nil {
		t.Fatal(e)
	}
	defer w.Close()
	select {
	case <-entered:
	case <-time.After(3 * time.Second):
		t.Fatal("publish not reached")
	}
	time.Sleep(250 * time.Millisecond)
	committing := waitJob(t, s, job.ID, "committing")
	deadline := time.Now().Add(5 * time.Second)
	for {
		_, err := s.API("/api/research/result", []byte(`{"id":"`+committing.ResultID+`"}`))
		if errors.Is(err, syscall.EWOULDBLOCK) && time.Now().Before(deadline) {
			time.Sleep(10 * time.Millisecond)
			continue
		}
		if err == nil || !strings.Contains(err.Error(), "result is not linked to a succeeded job") {
			t.Fatalf("unexpected committing result visibility: %v", err)
		}
		break
	}
	fail.Store(false)
	waitJob(t, s, job.ID, "succeeded")
}
func TestCancellationCommitCutoffAndRecovery(t *testing.T) {
	s, _ := Open(t.TempDir())
	snap, _ := s.Import(fixture(t))
	job, _ := s.Submit(snap.ID, 0)
	ready := make(chan struct{}, 1)
	release := make(chan struct{})
	s.publishHook = func(kind string) error {
		if kind == "result" {
			select {
			case ready <- struct{}{}:
			default:
			}
			<-release
		}
		return nil
	}
	w, e := StartJobs(s)
	if e != nil {
		t.Fatal(e)
	}
	var releaseOnce, closeOnce sync.Once
	releasePublish := func() { releaseOnce.Do(func() { close(release) }) }
	closeWorker := func() { closeOnce.Do(w.Close) }
	t.Cleanup(func() { releasePublish(); closeWorker() })
	select {
	case <-ready:
	case <-time.After(3 * time.Second):
		t.Fatal("publication not reached")
	}
	done := make(chan error, 1)
	go func() { _, e := s.CancelJob(job.ID); done <- e }()
	releasePublish()
	if err := <-done; err == nil {
		t.Fatal("cancellation accepted after committing")
	}
	finished := waitJob(t, s, job.ID, "succeeded")
	closeWorker()
	reopened, _ := Open(s.root)
	worker, e := StartJobs(reopened)
	if e != nil {
		t.Fatal(e)
	}
	defer worker.Close()
	if _, e = reopened.Result(finished.ResultID); e != nil {
		t.Fatal(e)
	}
}
func TestJobsRecoverAndComplete(t *testing.T) {
	s, _ := Open(t.TempDir())
	snap, e := s.Import(fixture(t))
	if e != nil {
		t.Fatal(e)
	}
	interrupted, e := s.Submit(snap.ID, .001)
	if e != nil {
		t.Fatal(e)
	}
	interrupted.Status = "running"
	if e = s.saveJob(interrupted); e != nil {
		t.Fatal(e)
	}
	queued, e := s.Submit(snap.ID, .001)
	if e != nil {
		t.Fatal(e)
	}
	w, e := StartJobs(s)
	if e != nil {
		t.Fatal(e)
	}
	defer w.Close()
	if _, e = StartJobs(s); e == nil {
		t.Fatal("second worker accepted")
	}
	waitJob(t, s, interrupted.ID, "interrupted")
	done := waitJob(t, s, queued.ID, "succeeded")
	r, e := s.Result(done.ResultID)
	if e != nil {
		t.Fatal(e)
	}
	if r.Metrics.NAV < 100008.689 || r.Metrics.NAV > 100008.691 {
		t.Fatalf("metrics: %+v", r.Metrics)
	}
}
func TestJobQueueAndCorruption(t *testing.T) {
	s, _ := Open(t.TempDir())
	snap, e := s.Import(fixture(t))
	if e != nil {
		t.Fatal(e)
	}
	var first Job
	for i := 0; i < maxQueued; i++ {
		j, e := s.Submit(snap.ID, 0)
		if e != nil {
			t.Fatal(e)
		}
		if i == 0 {
			first = j
		}
	}
	if _, e = s.Submit(snap.ID, 0); e == nil {
		t.Fatal("queue exceeded")
	}
	if _, e = s.CancelJob(first.ID); e != nil {
		t.Fatal(e)
	}
	if _, e = s.Submit(snap.ID, 0); e != nil {
		t.Fatal(e)
	}
	if e = os.WriteFile(filepath.Join(s.root, "job-"+first.ID+".json"), []byte("{"), 0600); e != nil {
		t.Fatal(e)
	}
	if _, e = StartJobs(s); e == nil {
		t.Fatal("corrupt job ignored")
	}
}
