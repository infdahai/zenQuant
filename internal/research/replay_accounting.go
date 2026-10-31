package research

import (
	"fmt"
	"math"
)

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
	// Check both ledger operations' failure conditions before consuming funding.
	if !finite(bar.High) || !finite(bar.Low) || !finite(bar.Close) {
		return fmt.Errorf("price observation high, low and close must be finite")
	}
	if bar.High < bar.Low {
		return fmt.Errorf("price observation high must be at least low")
	}
	if bar.Close <= 0 || bar.Low <= 0 {
		return fmt.Errorf("price observations must be positive")
	}
	if state := a.ledger.positionEpisodeLedgerState; state != nil {
		if last, ok := state.last[a.symbol]; ok && bar.CloseTimeNS < last {
			return fmt.Errorf("events for %s must be chronological: timestamp_ns=%d last_timestamp_ns=%d", a.symbol, bar.CloseTimeNS, last)
		}
		if episode := state.open[a.symbol]; episode != nil && episode.episode.OpenedAtNS < 0 && bar.CloseTimeNS > math.MaxInt64+episode.episode.OpenedAtNS {
			return fmt.Errorf("episode holding duration for %s exceeds int64 nanoseconds", a.symbol)
		}
	}
	index := a.fundingIndex
	for index+1 < len(a.funding) && a.funding[index+1].FundingTimeNS <= bar.CloseTimeNS {
		index++
	}
	if index >= 0 {
		row := a.funding[index]
		if row.FundingTimeNS > a.lastFundingTime {
			pnl := -a.ledger.PositionQty(a.symbol) * bar.Close * row.FundingRate
			if !finite(pnl) {
				return fmt.Errorf("funding pnl must be finite")
			}
			if err := a.ledger.AllocateFunding(FundingAllocation{Symbol: a.symbol, TimestampNS: bar.CloseTimeNS, PnL: pnl}); err != nil {
				return err
			}
			a.lastFundingTime = row.FundingTimeNS
		}
	}
	if err := a.ledger.ObservePrice(PriceObservation{Symbol: a.symbol, TimestampNS: bar.CloseTimeNS, High: bar.High, Low: bar.Low, Close: bar.Close}); err != nil {
		return err
	}
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
