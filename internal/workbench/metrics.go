package workbench

import (
	"errors"
	"math"

	"github.com/infdahai/zenQuant/internal/research"
)

// Metrics use a fixed initial cash balance and the replay's documented funding
// convention. Drawdown is the maximum close-to-close NAV decline in USD.
type Metrics struct {
	InitialCash         float64 `json:"initial_cash"`
	Cash                float64 `json:"cash"`
	NAV                 float64 `json:"nav"`
	NetPnL              float64 `json:"net_pnl"`
	Fees                float64 `json:"fees"`
	FundingPnL          float64 `json:"funding_pnl"`
	Turnover            float64 `json:"turnover"`
	MaxDrawdown         float64 `json:"max_drawdown"`
	MaxExposure         float64 `json:"max_exposure"`
	ReconciliationError float64 `json:"reconciliation_error"`
}

func calculateMetrics(d Dataset, replay *research.OfflineReplayResult) (Metrics, error) {
	m := Metrics{InitialCash: 100000, Cash: 100000}
	qty := 0.0
	peak := m.InitialCash
	fi := 0
	lastClose := 0.0
	ri := 0
	for _, bar := range d.Candles {
		for fi < len(d.Funding) && d.Funding[fi].TimeNS <= bar.CloseTimeNS {
			row := d.Funding[fi]
			price := lastClose
			if row.TimeNS == bar.CloseTimeNS {
				price = bar.Close
			}
			pnl := -qty * price * row.Rate
			m.Cash += pnl
			m.FundingPnL += pnl
			fi++
		}
		lastClose = bar.Close
		for ri < len(replay.Reports) && replay.Reports[ri].TransactionTimeNS == bar.CloseTimeNS {
			r := replay.Reports[ri]
			if r.Status == "FILLED" {
				notional := r.FilledQty * r.AvgPrice
				m.Cash -= float64(r.Side)*notional + r.Fee
				qty += float64(r.Side) * r.FilledQty
				m.Fees += r.Fee
				m.Turnover += notional
			}
			ri++
		}
		nav := m.Cash + qty*bar.Close
		if nav > peak {
			peak = nav
		}
		if peak-nav > m.MaxDrawdown {
			m.MaxDrawdown = peak - nav
		}
		if math.Abs(qty*bar.Close) > m.MaxExposure {
			m.MaxExposure = math.Abs(qty * bar.Close)
		}
		m.NAV = nav
	}
	if ri != len(replay.Reports) || qty != replay.PositionQty {
		return Metrics{}, errors.New("accounting report reconciliation failed")
	}
	m.NetPnL = m.NAV - m.InitialCash
	ledger := 0.0
	for _, e := range replay.Episodes {
		ledger += e.NetPnL + e.UnrealizedPnL
	}
	m.ReconciliationError = m.NetPnL - ledger
	if math.IsNaN(m.NAV) || math.IsInf(m.NAV, 0) || math.Abs(m.ReconciliationError) > 1e-7*math.Max(1, math.Abs(ledger)) {
		return Metrics{}, errors.New("cash and episode accounting disagree")
	}
	return m, nil
}
