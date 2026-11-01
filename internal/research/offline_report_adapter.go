package research

import (
	"fmt"
	"math"
)

// OfflineReportAdapter dispatches one incremental simulated fill per client
// order ID. A retry must use a new ID. Multiple events for one external order
// are unsupported: ExecutionReport has no fill ID or cumulative quantity.
// This adapter is in-memory, single-threaded, and not a live integration.
// Callers must use it for all reports in the session and stop on any error.
// USD quote and fee units are a caller assertion; validUntilNS is the inclusive
// expiry of the execution-price mark. External component changes are not atomic.
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

// Dispatch consumes the incremental quantity and fee of a simulated FILLED or
// PARTIALLY_FILLED report. The currency argument asserts both the instrument
// quote and fee unit. Rejected, zero-fill and other reports are unsupported.
func (a *OfflineReportAdapter) Dispatch(report ExecutionReport, currency string, validUntilNS int64) error {
	if report.ClientOrderID == "" || (report.Status != "FILLED" && report.Status != "PARTIALLY_FILLED") || report.RejectReason != "" || currency != "USD" || report.TransactionTimeNS <= 0 || report.TransactionTimeNS < a.lastTime || validUntilNS < report.TransactionTimeNS || !strategySymbol(report.Symbol) || (report.Side != 1 && report.Side != -1) || !sizingFinite(report.FilledQty) || report.FilledQty <= 0 || !sizingFinite(report.AvgPrice) || report.AvgPrice <= 0 || !sizingFinite(report.Fee) || report.Fee < 0 {
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
	if err := staged.ApplyFee(report.Fee, currency, report.TransactionTimeNS); err != nil {
		return fmt.Errorf("risk fee: %w", err)
	}
	// The fill and fee are one report: the temporary pre-fee valuation is not
	// an observable equity peak. Invalid valuations must not advance the peak.
	staged.state.HighWaterMark = a.risk.state.HighWaterMark
	if staged.state.EquityValid {
		staged.state.HighWaterMark = math.Max(staged.state.HighWaterMark, staged.state.Equity)
	}
	if err := a.strategy.Fill(StrategyFill{Symbol: report.Symbol, Side: float64(report.Side), Quantity: report.FilledQty, Price: report.AvgPrice}); err != nil {
		return fmt.Errorf("strategy fill: %w", err)
	}
	*a.risk = *staged
	a.seen[report.ClientOrderID] = struct{}{}
	a.lastTime = report.TransactionTimeNS
	return nil
}
