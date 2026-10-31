package workbench

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"os"
	"path/filepath"
	"syscall"
	"time"
)

// Job is a durable diagnostic request. Interrupted jobs require resubmission.
type Job struct {
	ID              string  `json:"id"`
	SnapshotID      string  `json:"snapshot_id"`
	FeeRate         float64 `json:"fee_rate"`
	Engine          string  `json:"engine"`
	Status          string  `json:"status"`
	ResultID        string  `json:"result_id,omitempty"`
	Error           string  `json:"error,omitempty"`
	CancelRequested bool    `json:"cancel_requested,omitempty"`
}

// A single process owns the worker for the lifetime of the lock. Other
// processes can submit and cancel jobs under the separate short metadata lock.
type Jobs struct {
	store *Store
	owner *os.File
	wake  chan struct{}
	stop  context.CancelFunc
	done  chan struct{}
}

const maxQueued = 8

func lockFile(path string, nonblock bool) (*os.File, error) {
	f, err := os.OpenFile(path, os.O_CREATE|os.O_RDWR, 0600)
	if err != nil {
		return nil, err
	}
	flags := syscall.LOCK_EX
	if nonblock {
		flags |= syscall.LOCK_NB
	}
	if err = syscall.Flock(int(f.Fd()), flags); err != nil {
		f.Close()
		return nil, err
	}
	return f, nil
}
func unlock(f *os.File) { _ = syscall.Flock(int(f.Fd()), syscall.LOCK_UN); _ = f.Close() }
func (s *Store) metadataLock() (*os.File, error) {
	deadline := time.Now().Add(200 * time.Millisecond)
	for {
		f, err := lockFile(filepath.Join(s.root, ".jobs-lock"), true)
		if err == nil {
			return f, nil
		}
		if !errors.Is(err, syscall.EWOULDBLOCK) || time.Now().After(deadline) {
			return nil, err
		}
		time.Sleep(5 * time.Millisecond)
	}
}
func (s *Store) jobPath(id string) (string, error) {
	if !idPattern.MatchString(id) {
		return "", errors.New("invalid job id")
	}
	return filepath.Join(s.root, "job-"+id+".json"), nil
}
func (s *Store) readJob(id string) (Job, error) {
	var j Job
	if s.jobIOHook != nil {
		if err := s.jobIOHook("read", Job{ID: id}); err != nil {
			return j, err
		}
	}
	p, e := s.jobPath(id)
	if e != nil {
		return j, e
	}
	b, e := os.ReadFile(p)
	if e != nil {
		return j, e
	}
	if len(b) > 16384 {
		return j, errors.New("job too large")
	}
	if e = strictDecode(b, &j); e != nil {
		return j, e
	}
	if j.ID != id {
		return j, errors.New("job id mismatch")
	}
	if j.Engine == "" {
		return j, errors.New("missing job engine")
	}
	switch j.Status {
	case "queued", "running", "committing", "succeeded", "failed", "cancelled", "interrupted":
	default:
		return j, errors.New("invalid job status")
	}
	return j, nil
}
func (s *Store) saveJob(j Job) error {
	if s.jobIOHook != nil {
		if err := s.jobIOHook("write", j); err != nil {
			return err
		}
	}
	p, e := s.jobPath(j.ID)
	if e != nil {
		return e
	}
	b, e := json.Marshal(j)
	if e != nil {
		return e
	}
	f, e := os.CreateTemp(s.root, ".job-")
	if e != nil {
		return e
	}
	name := f.Name()
	defer os.Remove(name)
	if _, e = f.Write(b); e == nil {
		e = f.Sync()
	}
	ce := f.Close()
	if e == nil {
		e = ce
	}
	if e != nil {
		return e
	}
	if e = os.Rename(name, p); e != nil {
		return e
	}
	dir, e := os.Open(s.root)
	if e != nil {
		return e
	}
	defer dir.Close()
	if s.jobIOHook != nil {
		if err := s.jobIOHook("dirsync", j); err != nil {
			return err
		}
	}
	return dir.Sync()
}
func (s *Store) jobIDs() ([]string, error) {
	es, e := os.ReadDir(s.root)
	if e != nil {
		return nil, e
	}
	ids := []string{}
	for _, v := range es {
		n := v.Name()
		if len(n) == 73 && n[:4] == "job-" && n[68:] == ".json" && idPattern.MatchString(n[4:68]) {
			ids = append(ids, n[4:68])
		}
	}
	return ids, nil
}
func (s *Store) JobsList() ([]Job, error) {
	l, e := s.metadataLock()
	if e != nil {
		return nil, e
	}
	defer unlock(l)
	dir, err := os.Open(s.root)
	if err != nil {
		return nil, err
	}
	err = dir.Sync()
	_ = dir.Close()
	if err != nil {
		return nil, err
	}
	ids, e := s.jobIDs()
	if e != nil {
		return nil, e
	}
	jobs := make([]Job, 0, len(ids))
	for _, id := range ids {
		j, e := s.readJob(id)
		if e != nil {
			return nil, fmt.Errorf("job %s: %w", id, e)
		}
		jobs = append(jobs, j)
	}
	return jobs, nil
}
func (s *Store) Submit(id string, fee float64) (Job, error) {
	var j Job
	if _, e := s.Snapshot(id); e != nil {
		return j, e
	}
	if math.IsNaN(fee) || math.IsInf(fee, 0) || fee < 0 || fee > 0.1 {
		return j, errors.New("invalid fee")
	}
	l, e := s.metadataLock()
	if e != nil {
		return j, e
	}
	defer unlock(l)
	ids, e := s.jobIDs()
	if e != nil {
		return j, e
	}
	if len(ids) >= 1024 {
		return j, errors.New("job history limit reached")
	}
	count := 0
	for _, id := range ids {
		x, e := s.readJob(id)
		if e != nil {
			return j, e
		}
		if x.Status == "queued" || x.Status == "running" {
			count++
		}
	}
	if count >= maxQueued {
		return j, errors.New("job queue full")
	}
	var b [32]byte
	if _, e = rand.Read(b[:]); e != nil {
		return j, e
	}
	engine, e := EngineIdentity()
	if e != nil {
		return j, e
	}
	j = Job{ID: hex.EncodeToString(b[:]), SnapshotID: id, FeeRate: fee, Engine: engine, Status: "queued"}
	if e = s.saveJob(j); e != nil {
		return j, fmt.Errorf("job %s submission durability unconfirmed: %w", j.ID, e)
	}
	return j, nil
}
func (s *Store) CancelJob(id string) (Job, error) {
	l, e := s.metadataLock()
	if e != nil {
		return Job{}, e
	}
	defer unlock(l)
	j, e := s.readJob(id)
	if e != nil {
		return j, e
	}
	switch j.Status {
	case "queued":
		j.Status = "cancelled"
	case "running":
		j.CancelRequested = true
	default:
		return j, errors.New("job is already terminal")
	}
	e = s.saveJob(j)
	return j, e
}
func StartJobs(s *Store) (*Jobs, error) {
	owner, e := lockFile(filepath.Join(s.root, ".worker-lock"), true)
	if e != nil {
		return nil, fmt.Errorf("research worker already owned: %w", e)
	}
	ctx, cancel := context.WithCancel(context.Background())
	w := &Jobs{store: s, owner: owner, wake: make(chan struct{}, 1), stop: cancel, done: make(chan struct{})}
	l, e := s.metadataLock()
	if e != nil {
		unlock(owner)
		cancel()
		return nil, e
	}
	ids, e := s.jobIDs()
	if e == nil {
		for _, id := range ids {
			var j Job
			j, e = s.readJob(id)
			if e != nil {
				break
			}
			if j.Status == "succeeded" {
				if _, e = s.Result(j.ResultID); e != nil {
					break
				}
			}
			if j.Status == "committing" {
				if _, resultErr := s.Result(j.ResultID); resultErr == nil {
					j.Status = "succeeded"
				} else if os.IsNotExist(resultErr) {
					j.Status = "interrupted"
					j.ResultID = ""
					j.Error = "result publication interrupted"
				} else {
					e = resultErr
					break
				}
				e = s.saveJob(j)
				if e != nil {
					break
				}
			}
			if j.Status == "running" {
				j.Status = "interrupted"
				j.Error = "worker stopped before completion"
				e = s.saveJob(j)
				if e != nil {
					break
				}
			}
		}
	}
	if e == nil {
		var dir *os.File
		dir, e = os.Open(s.root)
		if e == nil {
			e = dir.Sync()
			_ = dir.Close()
		}
	}
	unlock(l)
	if e != nil {
		unlock(owner)
		cancel()
		return nil, e
	}
	go w.loop(ctx)
	return w, nil
}
func (w *Jobs) Close() { w.stop(); <-w.done; unlock(w.owner) }
func (w *Jobs) Notify() {
	select {
	case w.wake <- struct{}{}:
	default:
	}
}
func (w *Jobs) loop(ctx context.Context) {
	defer close(w.done)
	ticker := time.NewTicker(250 * time.Millisecond)
	defer ticker.Stop()
	for {
		if ctx.Err() != nil {
			return
		}
		j, e := w.take()
		if e != nil {
			if j.ID != "" {
				w.recoverInterrupted(ctx, j.ID)
			} else {
				w.recoverRunning(ctx)
			}
			select {
			case <-ctx.Done():
				return
			case <-ticker.C:
				continue
			}
		}
		if j.ID == "" {
			select {
			case <-ctx.Done():
				return
			case <-w.wake:
			case <-ticker.C:
			}
			continue
		}
		w.execute(ctx, j)
	}
}
func (w *Jobs) take() (Job, error) {
	s := w.store
	l, e := s.metadataLock()
	if e != nil {
		return Job{}, e
	}
	defer unlock(l)
	ids, e := s.jobIDs()
	if e != nil {
		return Job{}, e
	}
	for _, id := range ids {
		j, e := s.readJob(id)
		if e != nil {
			return Job{}, e
		}
		if j.Status == "queued" {
			engine, err := EngineIdentity()
			if err != nil {
				return Job{}, err
			}
			if j.Engine != engine {
				j.Status = "interrupted"
				j.Error = "engine identity changed; resubmit explicitly"
				if err = s.saveJob(j); err != nil {
					return j, err
				}
				continue
			}
			j.Status = "running"
			if e = s.saveJob(j); e != nil {
				return j, e
			}
			return j, nil
		}
	}
	return Job{}, nil
}
func (w *Jobs) execute(ctx context.Context, j Job) {
	s := w.store
	runctx, cancel := context.WithCancel(ctx)
	defer cancel()
	done := make(chan error, 1)
	go func() {
		var monitorErr error
		defer func() { done <- monitorErr }()
		ticker := time.NewTicker(50 * time.Millisecond)
		defer ticker.Stop()
		for {
			select {
			case <-runctx.Done():
				return
			case <-ticker.C:
				l, e := s.metadataLock()
				if e != nil {
					monitorErr = e
					cancel()
					return
				}
				current, e := s.readJob(j.ID)
				unlock(l)
				if e != nil {
					monitorErr = e
					cancel()
					return
				}
				if current.CancelRequested {
					cancel()
					return
				}
			}
		}
	}()
	result, err := s.run(runctx, j.SnapshotID, j.FeeRate)
	cancel()
	monitorErr := <-done
	if monitorErr != nil {
		w.recoverInterrupted(ctx, j.ID)
		return
	}
	w.finish(ctx, j.ID, result, err)
}

// recoverRunning is called only by the exclusive owner between executions.
// Any running record is therefore an uncompleted claim, not an active replay.
func (w *Jobs) recoverRunning(ctx context.Context) {
	ids, err := w.store.jobIDs()
	if err != nil {
		return
	}
	for _, id := range ids {
		if ctx.Err() != nil {
			return
		}
		w.recoverInterrupted(ctx, id)
	}
}
func (w *Jobs) recoverInterrupted(ctx context.Context, id string) {
	w.transition(ctx, id, func(j *Job) {
		if j.Status == "running" {
			j.Status = "interrupted"
			j.Error = "job state could not be durably completed"
		}
	})
}
func (w *Jobs) finish(ctx context.Context, id string, result Result, runErr error) {
	for {
		lock, err := w.store.metadataLock()
		if err == nil {
			var j Job
			j, err = w.store.readJob(id)
			if err == nil && j.Status == "committing" {
				b, encodeErr := encode(result)
				err = encodeErr
				if err == nil && digest(b) != j.ResultID {
					err = errors.New("result identity changed")
				}
				if err == nil {
					err = w.store.publish("result", j.ResultID, b)
				}
				if err == nil {
					j.Status = "succeeded"
					err = w.store.saveJob(j)
				}
			}
			if err == nil && j.Status == "running" {
				switch {
				case j.CancelRequested:
					j.Status = "cancelled"
				case ctx.Err() != nil:
					j.Status = "interrupted"
					j.Error = "worker stopped before completion"
				case runErr != nil:
					j.Status = "failed"
					j.Error = runErr.Error()
				default:
					var b []byte
					b, err = encode(result)
					if err != nil || len(b) > MaxInput*4 {
						j.Status = "failed"
						j.Error = "result encoding failed or exceeds artifact limit"
						err = nil
					} else {
						resultID := digest(b)
						j.Status = "committing"
						j.ResultID = resultID
						err = w.store.saveJob(j)
						if err == nil {
							err = w.store.publish("result", resultID, b)
						}
						if err == nil {
							j.Status = "succeeded"
						}
					}
				}
				if err == nil {
					err = w.store.saveJob(j)
				}
			}
			if err == nil {
				dir, e := os.Open(w.store.root)
				if e != nil {
					err = e
				} else {
					err = dir.Sync()
					_ = dir.Close()
				}
			}
			unlock(lock)
		}
		if err == nil || ctx.Err() != nil {
			return
		}
		timer := time.NewTimer(100 * time.Millisecond)
		select {
		case <-ctx.Done():
			timer.Stop()
			return
		case <-timer.C:
		}
	}
}

// Each attempt releases the metadata lock before waiting. Once shutdown begins,
// make at most one attempt; a running record is recovered by the next owner.
func (w *Jobs) transition(ctx context.Context, id string, change func(*Job)) {
	for {
		l, err := w.store.metadataLock()
		if err == nil {
			var j Job
			j, err = w.store.readJob(id)
			if err == nil {
				before := j
				change(&j)
				if j != before {
					err = w.store.saveJob(j)
				} else {
					// A prior rename may have succeeded while its directory sync failed.
					dir, openErr := os.Open(w.store.root)
					if openErr != nil {
						err = openErr
					} else {
						err = dir.Sync()
						_ = dir.Close()
					}
				}
			}
			unlock(l)
		}
		if err == nil || ctx.Err() != nil {
			return
		}
		timer := time.NewTimer(100 * time.Millisecond)
		select {
		case <-ctx.Done():
			if !timer.Stop() {
				<-timer.C
			}
			return
		case <-timer.C:
		}
	}
}
