package research

import "fmt"

// ReplayFundingRow is an auxiliary funding observation, ordered by source time.
type ReplayFundingRow struct {
	FundingTimeNS int64
	FundingRate   float64
}

// ReplayExecutionBar is a confirmed execution-timeframe bar.
type ReplayExecutionBar struct {
	CloseTimeNS int64
	High        float64
	Low         float64
	Close       float64
}

// ReplayAccounting adapts a single-symbol replay's market events and actual
// execution reports to a position episode ledger. It performs no execution.
type ReplayAccounting struct {
	ledger          *PositionEpisodeLedger
	symbol          string
	funding         []ReplayFundingRow
	fundingIndex    int
	lastFundingTime int64
	cutoff          int64
	hasCutoff       bool
}

// NewReplayAccounting copies funding rows, which must be in source-time order.
// Equal timestamps retain input order; the last visible row is authoritative.
func NewReplayAccounting(symbol string, ledger *PositionEpisodeLedger, funding []ReplayFundingRow) (*ReplayAccounting, error) {
	if ledger == nil {
		return nil, fmt.Errorf("replay accounting requires a ledger")
	}
	for i := range funding {
		if !finite(funding[i].FundingRate) {
			return nil, fmt.Errorf("funding rate must be finite")
		}
		if i > 0 && funding[i].FundingTimeNS < funding[i-1].FundingTimeNS {
			return nil, fmt.Errorf("funding rows must be ordered by source time")
		}
	}
	return &ReplayAccounting{symbol: symbol, ledger: ledger, funding: append([]ReplayFundingRow(nil), funding...), fundingIndex: -1}, nil
}

// SetCutoff sets the visibility time for reports emitted by any replay event.
func (a *ReplayAccounting) SetCutoff(cutoffNS int64) {
	a.cutoff, a.hasCutoff = cutoffNS, true
}

// OnExecutionBar applies the latest visible funding and then the bar price.
// Call this before collecting reports triggered by this bar. Unlike the Python
// replay, invalid bars are rejected before funding or cutoff state is changed.
func (a *ReplayAccounting) OnExecutionBar(bar ReplayExecutionBar) error {
	index := a.fundingIndex
	for index+1 < len(a.funding) && a.funding[index+1].FundingTimeNS <= bar.CloseTimeNS {
		index++
	}
	lastFundingTime := a.lastFundingTime
	err := a.ledger.applyAtomic(func(ledger *PositionEpisodeLedger) error {
		if index >= 0 {
			row := a.funding[index]
			if row.FundingTimeNS > lastFundingTime {
				pnl := -ledger.PositionQty(a.symbol) * bar.Close * row.FundingRate
				if err := ledger.AllocateFunding(FundingAllocation{Symbol: a.symbol, TimestampNS: bar.CloseTimeNS, PnL: pnl}); err != nil {
					return err
				}
				lastFundingTime = row.FundingTimeNS
			}
		}
		return ledger.ObservePrice(PriceObservation{Symbol: a.symbol, TimestampNS: bar.CloseTimeNS, High: bar.High, Low: bar.Low, Close: bar.Close})
	})
	if err != nil {
		return err
	}
	a.lastFundingTime = lastFundingTime
	a.fundingIndex = index
	a.SetCutoff(bar.CloseTimeNS)
	return nil
}

// CollectReport normalizes stale transaction times to the current visible
// cutoff and forwards the actual report, including non-fills, to the ledger.
// The returned copy is the report as observed by replay.
func (a *ReplayAccounting) CollectReport(report ExecutionReport) (ExecutionReport, error) {
	if report.Symbol != a.symbol {
		return report, fmt.Errorf("report symbol %q does not match replay symbol %q", report.Symbol, a.symbol)
	}
	if a.hasCutoff && report.TransactionTimeNS < a.cutoff {
		report.TransactionTimeNS = a.cutoff
	}
	return report, a.ledger.OnFill(report)
}
