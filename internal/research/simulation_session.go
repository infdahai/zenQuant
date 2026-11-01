package research

import (
	"fmt"
	"os"
	"path/filepath"
	"sync"
)

// simulationSession admits one synchronous incremental result per attempt.
// It is private to offline replay: there is no adapter registration, external
// report ingestion or report publisher capability exposed to policies.
// Consumers run in order; a failed consumer may already have changed state, so
// any submission/publication error (or panic) permanently blocks the session.
// Partial results end the attempt. There is no outstanding exchange order.
type simulationSession struct {
	mu      sync.Mutex
	busy    bool
	blocked bool

	path      string
	started   bool
	orderIDs  map[string]struct{}
	onSubmit  func(OfflineExecutionOrder) error
	consumers []func(ExecutionReport) error
	// Private seams for fault tests. Production always uses the built-in
	// synchronous simulator and real file/directory Sync.
	simulate func(OfflineExecutionOrder, float64, float64, int64) (ExecutionReport, error)
	syncFile func(*os.File) error
}

func newSimulationSession(path string, consumers ...func(ExecutionReport) error) (*simulationSession, error) {
	if path != "" {
		if _, err := os.Lstat(path); err == nil {
			return nil, fmt.Errorf("simulation session cannot resume: %s; account recovery is not implemented", path)
		} else if !os.IsNotExist(err) {
			return nil, err
		}
	}
	return &simulationSession{
		path: path, orderIDs: make(map[string]struct{}),
		consumers: append([]func(ExecutionReport) error(nil), consumers...),
		simulate:  SimulateOfflineExecution, syncFile: (*os.File).Sync,
	}, nil
}

func (s *simulationSession) start() error {
	if s.started {
		return nil
	}
	if s.path != "" {
		// Parent directories must already exist. Never remove a marker after
		// failure: even an empty/partially written marker prevents reopening.
		marker, err := os.OpenFile(s.path, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
		if err != nil {
			return err
		}
		_, writeErr := marker.WriteString("zenquant-simulation-v1: resume unsupported\n")
		if writeErr == nil {
			writeErr = s.syncFile(marker)
		}
		closeErr := marker.Close()
		if writeErr != nil {
			return writeErr
		}
		if closeErr != nil {
			return closeErr
		}
		directory, err := os.Open(filepath.Dir(s.path))
		if err != nil {
			return err
		}
		syncErr := s.syncFile(directory)
		closeErr = directory.Close()
		if syncErr != nil {
			return syncErr
		}
		if closeErr != nil {
			return closeErr
		}
	}
	s.started = true
	return nil
}

func copyOfflineOrder(order OfflineExecutionOrder) OfflineExecutionOrder {
	if order.LimitPrice != nil {
		price := *order.LimitPrice
		order.LimitPrice = &price
	}
	return order
}

func (s *simulationSession) submit(order OfflineExecutionOrder, mark, rate float64, timestamp int64) (ExecutionReport, error) {
	s.mu.Lock()
	if s.blocked || s.busy {
		s.mu.Unlock()
		return ExecutionReport{}, fmt.Errorf("simulation session is blocked or already submitting")
	}
	s.busy = true
	s.mu.Unlock()
	succeeded := false
	defer func() {
		s.mu.Lock()
		s.busy = false
		s.blocked = !succeeded
		s.mu.Unlock()
	}()

	order = copyOfflineOrder(order)
	if err := s.start(); err != nil {
		return ExecutionReport{}, fmt.Errorf("start simulation session: %w", err)
	}
	if _, exists := s.orderIDs[order.ClientOrderID]; order.ClientOrderID == "" || exists {
		return ExecutionReport{}, fmt.Errorf("simulation order identity must be unique and nonempty")
	}
	s.orderIDs[order.ClientOrderID] = struct{}{}
	if s.onSubmit != nil {
		if err := s.onSubmit(copyOfflineOrder(order)); err != nil {
			return ExecutionReport{}, fmt.Errorf("publish order: %w", err)
		}
	}
	report, err := s.simulate(copyOfflineOrder(order), mark, rate, timestamp)
	if err != nil {
		return ExecutionReport{}, fmt.Errorf("simulate order: %w", err)
	}
	if err := validateSimulationReport(order, report); err != nil {
		return ExecutionReport{}, err
	}
	for _, consume := range s.consumers {
		if err := consume(report); err != nil {
			return ExecutionReport{}, fmt.Errorf("publish report: %w", err)
		}
	}
	succeeded = true
	return report, nil
}

// Match Python PR55 admission, including its accepted zero-fill/PENDING status.
// Go's built-in simulator retains its stricter input and order-type checks.
func validateSimulationReport(order OfflineExecutionOrder, report ExecutionReport) error {
	validStatus := report.Status == "FILLED" || report.Status == "PARTIALLY_FILLED" || report.Status == "REJECTED" || report.Status == "PENDING"
	if report.ClientOrderID != order.ClientOrderID || report.Symbol != order.Symbol || report.Side != order.Side ||
		(report.Side != -1 && report.Side != 1) || !validStatus ||
		!finite(report.FilledQty) || !finite(report.AvgPrice) || !finite(report.Fee) ||
		report.FilledQty < 0 || report.FilledQty > order.Quantity || report.AvgPrice < 0 || report.Fee < 0 ||
		(report.FilledQty > 0 && report.AvgPrice <= 0) ||
		((report.Status == "REJECTED" || report.Status == "PENDING") && (report.FilledQty != 0 || report.Fee != 0)) {
		return fmt.Errorf("invalid or mismatched simulation report")
	}
	return nil
}
