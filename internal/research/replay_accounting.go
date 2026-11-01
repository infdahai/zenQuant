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
	lastClose       float64
	cutoff          int64
	hasCutoff       bool
}

// NewReplayAccounting copies funding rows, which must be in source-time order.
// Each source timestamp identifies a unique settlement.
func NewReplayAccounting(symbol string, ledger *PositionEpisodeLedger, funding []ReplayFundingRow) (*ReplayAccounting, error) {
	if ledger == nil {
		return nil, fmt.Errorf("replay accounting requires a ledger")
	}
	for i := range funding {
		if !finite(funding[i].FundingRate) {
			return nil, fmt.Errorf("funding rate must be finite")
		}
		if i > 0 && funding[i].FundingTimeNS <= funding[i-1].FundingTimeNS {
			return nil, fmt.Errorf("funding rows must have strictly increasing source times")
		}
	}
	return &ReplayAccounting{symbol: symbol, ledger: ledger, funding: append([]ReplayFundingRow(nil), funding...), fundingIndex: -1}, nil
}

// SetCutoff advances the visibility time for reports emitted by a replay event.
// Reports cannot pass an unprocessed settlement; an execution bar must process it.
func (a *ReplayAccounting) SetCutoff(cutoffNS int64) error {
	if a.hasCutoff && cutoffNS < a.cutoff {
		return fmt.Errorf("replay cutoff cannot move backwards")
	}
	a.cutoff, a.hasCutoff = cutoffNS, true
	return nil
}

// OnExecutionBar applies each visible settlement before the bar's fills. A
// settlement uses the latest execution close at or before its source time.
// Invalid events leave the ledger and replay cursor unchanged.
func (a *ReplayAccounting) OnExecutionBar(bar ReplayExecutionBar) error {
	_, err := a.onExecutionBar(bar)
	return err
}

type replayFundingSettlement struct {
	quantity, price, pnl float64
}

func (a *ReplayAccounting) onExecutionBar(bar ReplayExecutionBar) ([]replayFundingSettlement, error) {
	if a.hasCutoff && bar.CloseTimeNS < a.cutoff {
		return nil, fmt.Errorf("execution bar precedes replay cutoff")
	}
	var settlements []replayFundingSettlement
	index := a.fundingIndex
	lastFundingTime := a.lastFundingTime
	err := a.ledger.applyAtomic(func(ledger *PositionEpisodeLedger) error {
		for index+1 < len(a.funding) && a.funding[index+1].FundingTimeNS <= bar.CloseTimeNS {
			index++
			row := a.funding[index]
			if row.FundingTimeNS <= lastFundingTime {
				continue
			}
			price := a.lastClose
			if row.FundingTimeNS == bar.CloseTimeNS {
				price = bar.Close
			}
			qty := ledger.PositionQty(a.symbol)
			if price == 0 && qty != 0 {
				return fmt.Errorf("funding settlement at %d has no point-in-time execution price", row.FundingTimeNS)
			}
			pnl := -qty * price * row.FundingRate
			if err := ledger.AllocateFunding(FundingAllocation{Symbol: a.symbol, TimestampNS: bar.CloseTimeNS, PnL: pnl}); err != nil {
				return err
			}
			settlements = append(settlements, replayFundingSettlement{qty, price, pnl})
			lastFundingTime = row.FundingTimeNS
		}
		return ledger.ObservePrice(PriceObservation{Symbol: a.symbol, TimestampNS: bar.CloseTimeNS, High: bar.High, Low: bar.Low, Close: bar.Close})
	})
	if err != nil {
		return nil, err
	}
	a.lastFundingTime = lastFundingTime
	a.fundingIndex = index
	a.lastClose = bar.Close
	a.cutoff, a.hasCutoff = bar.CloseTimeNS, true
	return settlements, nil
}

// CollectReport normalizes stale transaction times to the current visible
// cutoff and forwards the actual report, including non-fills, to the ledger.
// Future reports and reports crossing an unprocessed settlement are rejected.
// The returned copy is the report as observed by replay.
func (a *ReplayAccounting) CollectReport(report ExecutionReport) (ExecutionReport, error) {
	if report.Symbol != a.symbol {
		return report, fmt.Errorf("report symbol %q does not match replay symbol %q", report.Symbol, a.symbol)
	}
	if a.hasCutoff {
		if report.TransactionTimeNS > a.cutoff {
			return report, fmt.Errorf("report time exceeds replay cutoff")
		}
		if report.TransactionTimeNS < a.cutoff {
			report.TransactionTimeNS = a.cutoff
		}
	}
	for i := a.fundingIndex + 1; i < len(a.funding) && a.funding[i].FundingTimeNS <= report.TransactionTimeNS; i++ {
		if a.funding[i].FundingTimeNS > 0 {
			return report, fmt.Errorf("report precedes processing of visible funding settlement")
		}
	}
	return report, a.ledger.OnFill(report)
}
