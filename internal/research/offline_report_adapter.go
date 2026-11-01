package research

import "fmt"

// OfflineReportAdapter dispatches one simulated final fill per client order ID.
// ExecutionReport has no fill ID or cumulative quantity, so partial fills and
// repeated IDs cannot be distinguished safely and are rejected. This adapter
// is in-memory, single-threaded, and is not a live execution integration.
// Callers must use it for all reports in the session and stop on any error.
// USD quote and fee units are a caller assertion; validUntilNS is the inclusive
// expiry of the execution-price mark. External component changes are not atomic.
// Positive fees that exceed post-fill cash are rejected: risk ApplyFee clamps
// cash to zero, matching Python but losing fees or increasing negative cash.
type OfflineReportAdapter struct {
	strategy *StrategyFlow
	risk     *StatefulRiskOverlay
	seen     map[string]struct{}
	lastTime int64
}

func NewOfflineReportAdapter(strategy *StrategyFlow, risk *StatefulRiskOverlay) (*OfflineReportAdapter, error) {
	if strategy == nil || risk == nil {
		return nil, fmt.Errorf("strategy and risk are required")
	}
	return &OfflineReportAdapter{strategy: strategy, risk: risk, seen: make(map[string]struct{})}, nil
}

// Dispatch consumes the incremental quantity and fee of a simulated FILLED
// report. The currency argument asserts both the instrument quote and fee unit.
// Rejected, partial, zero-fill and other reports are unsupported errors.
func (a *OfflineReportAdapter) Dispatch(report ExecutionReport, currency string, validUntilNS int64) error {
	if report.ClientOrderID == "" || report.Status != "FILLED" || report.RejectReason != "" || currency != "USD" || report.TransactionTimeNS <= 0 || report.TransactionTimeNS < a.lastTime || validUntilNS < report.TransactionTimeNS || !strategySymbol(report.Symbol) || (report.Side != 1 && report.Side != -1) || !sizingFinite(report.FilledQty) || report.FilledQty <= 0 || !sizingFinite(report.AvgPrice) || report.AvgPrice <= 0 || !sizingFinite(report.Fee) || report.Fee < 0 {
		return fmt.Errorf("unsupported or invalid simulated execution report")
	}
	if _, ok := a.seen[report.ClientOrderID]; ok {
		return fmt.Errorf("duplicate client order ID %q", report.ClientOrderID)
	}
	// Stage risk operations so a fee failure does not leave risk with only a fill.
	staged := a.risk.clone()
	if err := staged.OnFill(report.Symbol, report.Side, report.FilledQty, report.AvgPrice, currency, report.TransactionTimeNS, validUntilNS); err != nil {
		return fmt.Errorf("risk fill: %w", err)
	}
	if report.Fee > 0 && report.Fee > staged.state.Cash {
		return fmt.Errorf("risk fee exceeds post-fill cash; clamped fee accounting is unsupported")
	}
	if err := staged.ApplyFee(report.Fee, currency, report.TransactionTimeNS); err != nil {
		return fmt.Errorf("risk fee: %w", err)
	}
	if err := a.strategy.Fill(StrategyFill{Symbol: report.Symbol, Side: float64(report.Side), Quantity: report.FilledQty, Price: report.AvgPrice}); err != nil {
		return fmt.Errorf("strategy fill: %w", err)
	}
	*a.risk = *staged
	a.seen[report.ClientOrderID] = struct{}{}
	a.lastTime = report.TransactionTimeNS
	return nil
}
