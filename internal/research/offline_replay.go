package research

import "fmt"

// OfflineReplayConfig supplies all inputs to a single-symbol synthetic replay.
// HistoryLimit bounds candles retained per timeframe and IndicatorWindow sets
// the trailing ATR, realized-volatility, rate-of-change and OU lookback.
type OfflineReplayConfig struct {
	Symbol             string
	Candles            map[string][]ReplayCandle
	Timeframes         []string
	ExecutionTimeframe string
	StartNS, EndNS     *int64
	Funding            []ReplayFundingRow
	TakerFeeRate       float64
	// MaxTradeQtyPerCutoff caps filled quantity after policy sizing. Zero selects
	// the Python default of 1; an explicit cap must be finite and positive.
	MaxTradeQtyPerCutoff          float64
	HistoryLimit, IndicatorWindow int
	// SessionPath optionally marks a fresh, non-resumable simulated account.
	// Empty means ephemeral replay. The parent directory must already exist.
	SessionPath string
}

// OfflineReplayIndicators are calculated from the visible bounded history.
type OfflineReplayIndicators struct{ ATR, RealizedVol, RateOfChange, OUKappa float64 }

// OfflineReplaySnapshot is an independent point-in-time view supplied to a policy.
type OfflineReplaySnapshot struct {
	Event       ReplayEvent
	Histories   map[string][]ReplayCandle
	Indicators  map[string]OfflineReplayIndicators
	PositionQty float64
}

// OfflineReplayPolicy returns synthetic orders in execution order, each with a
// nonempty client order ID unique across all submitted orders. Orders skipped
// by admission produce no report and do not reserve an ID. The policy is
// responsible for its own determinism and must not rely on future input data.
type OfflineReplayPolicy func(OfflineReplaySnapshot) ([]OfflineExecutionOrder, error)

// OfflineReplayResult contains actual simulated reports and episode accounting.
type OfflineReplayResult struct {
	Reports     []ExecutionReport
	Episodes    []PositionEpisode
	PositionQty float64
}

// RunOfflineReplay processes prepared events in their cutoff and priority order.
// Events update their timeframe histories in priority order. Only execution
// events invoke policy, after funding and bar prices are applied to the existing
// position. Same-close higher-priority histories are visible to the policy.
// Duplicate execution intervals within the cutoff are rejected before policy runs.
// Orders fill synthetically at the execution bar's same close, with no slippage.
// Policy orders are sized by the caller; final admission enforces reduce-only and
// the filled-quantity cap, rounding down to six places with a 0.0001 minimum.
// Opposite-side non-reduce orders may flip in one fill; no Python target sizing,
// two-leg flip orchestration, or risk overlay is implemented. Accounting
// uses only the latest visible funding row per execution bar, not every skipped
// row; equal timestamps select the last row, and timestamps <= 0 are not newly
// charged by the underlying accounting adapter. This is not live execution,
// risk control, a strategy, or full Python replay parity. On error no result is
// returned; external side effects of a policy cannot be rolled back.
func RunOfflineReplay(config OfflineReplayConfig, policy OfflineReplayPolicy) (*OfflineReplayResult, error) {
	return runOfflineReplay(config, policy, nil)
}

// The private simulator seam exercises partial and rejected attempts in tests.
func runOfflineReplay(config OfflineReplayConfig, policy OfflineReplayPolicy, simulate func(OfflineExecutionOrder, float64, float64, int64) (ExecutionReport, error)) (*OfflineReplayResult, error) {
	if config.Symbol == "" || policy == nil {
		return nil, fmt.Errorf("symbol and policy are required")
	}
	if config.HistoryLimit < 1 || config.HistoryLimit > MaxIndicatorWindow+1 || config.IndicatorWindow < 1 || config.IndicatorWindow > MaxIndicatorWindow || config.HistoryLimit < config.IndicatorWindow+1 {
		return nil, fmt.Errorf("invalid history limit or indicator window")
	}
	if !finite(config.TakerFeeRate) || config.TakerFeeRate < 0 {
		return nil, fmt.Errorf("taker fee rate must be finite and nonnegative")
	}
	if !finite(config.MaxTradeQtyPerCutoff) || config.MaxTradeQtyPerCutoff < 0 {
		return nil, fmt.Errorf("quantity cap must be finite and nonnegative")
	}
	capQty := config.MaxTradeQtyPerCutoff
	if capQty == 0 {
		capQty = 1
	}
	events, err := PrepareReplayEvents(config.Candles, config.Timeframes, config.ExecutionTimeframe, config.StartNS, config.EndNS)
	if err != nil {
		return nil, fmt.Errorf("prepare events: %w", err)
	}
	executionIntervals := make(map[int64]struct{})
	for _, event := range events {
		if event.Symbol != config.Symbol {
			return nil, fmt.Errorf("event symbol %q does not match replay symbol %q", event.Symbol, config.Symbol)
		}
		if event.EventTimeframe == config.ExecutionTimeframe {
			if _, exists := executionIntervals[event.OpenTimeNS]; exists {
				return nil, fmt.Errorf("duplicate execution interval at %d", event.OpenTimeNS)
			}
			executionIntervals[event.OpenTimeNS] = struct{}{}
		}
	}
	if len(executionIntervals) == 0 {
		return nil, fmt.Errorf("no execution bars within replay bounds")
	}
	ledger := NewPositionEpisodeLedger()
	accounting, err := NewReplayAccounting(config.Symbol, ledger, config.Funding)
	if err != nil {
		return nil, fmt.Errorf("funding: %w", err)
	}
	histories := make(map[string][]ReplayCandle, len(config.Timeframes))
	result := &OfflineReplayResult{}
	admission := &offlineOrderAdmission{maxQty: capQty, positions: make(map[string]float64), filled: make(map[admissionKey]float64)}
	session, err := newSimulationSession(config.SessionPath, func(report ExecutionReport) error {
		report, err := accounting.CollectReport(report)
		if err != nil {
			return err
		}
		result.Reports = append(result.Reports, report)
		return nil
	})
	if err != nil {
		return nil, err
	}
	// Reduce-only is enforced against actual inventory before this stateless simulator.
	session.simulate = func(order OfflineExecutionOrder, mark, rate float64, timestamp int64) (ExecutionReport, error) {
		order.ReduceOnly = false
		return SimulateOfflineExecution(order, mark, rate, timestamp)
	}
	if simulate != nil {
		session.simulate = simulate
	}
	for _, event := range events {
		tf := event.EventTimeframe
		history := histories[tf]
		if len(history) == config.HistoryLimit {
			copy(history, history[1:])
			history = history[:len(history)-1]
		}
		histories[tf] = append(history, event.ReplayCandle)
		if tf != config.ExecutionTimeframe {
			continue
		}
		if err := accounting.OnExecutionBar(ReplayExecutionBar{CloseTimeNS: event.CloseTimeNS, High: event.High, Low: event.Low, Close: event.Close}); err != nil {
			return nil, fmt.Errorf("execution bar: %w", err)
		}
		snapshot := OfflineReplaySnapshot{Event: event, Histories: make(map[string][]ReplayCandle, len(config.Timeframes)), Indicators: make(map[string]OfflineReplayIndicators, len(config.Timeframes)), PositionQty: admission.positions[config.Symbol]}
		for _, timeframe := range config.Timeframes {
			rows := histories[timeframe]
			snapshot.Histories[timeframe] = append([]ReplayCandle(nil), rows...)
			candles := make([]IndicatorCandle, len(rows))
			for i, row := range rows {
				candles[i] = IndicatorCandle{High: row.High, Low: row.Low, Close: row.Close}
			}
			atr, err := OfflineATR(candles, config.IndicatorWindow)
			if err != nil {
				return nil, fmt.Errorf("%s ATR: %w", timeframe, err)
			}
			vol, err := OfflineRealizedVol(candles, config.IndicatorWindow, timeframe)
			if err != nil {
				return nil, fmt.Errorf("%s volatility: %w", timeframe, err)
			}
			roc, err := OfflineRateOfChange(candles, config.IndicatorWindow)
			if err != nil {
				return nil, fmt.Errorf("%s rate of change: %w", timeframe, err)
			}
			kappa, err := OfflineOUKappa(candles, config.IndicatorWindow)
			if err != nil {
				return nil, fmt.Errorf("%s OU kappa: %w", timeframe, err)
			}
			snapshot.Indicators[timeframe] = OfflineReplayIndicators{ATR: atr, RealizedVol: vol, RateOfChange: roc, OUKappa: kappa}
		}
		orders, err := policy(snapshot)
		if err != nil {
			return nil, fmt.Errorf("policy at %d: %w", event.CloseTimeNS, err)
		}
		for _, order := range orders {
			if order.Symbol != config.Symbol {
				return nil, fmt.Errorf("order symbol %q does not match replay symbol %q", order.Symbol, config.Symbol)
			}
			admitted, ok, err := admission.admit(order, event.CloseTimeNS)
			if err != nil {
				return nil, fmt.Errorf("order admission: %w", err)
			}
			if !ok {
				continue
			}
			report, err := session.submit(admitted, event.Close, config.TakerFeeRate, event.CloseTimeNS)
			if err != nil {
				return nil, fmt.Errorf("execution session: %w", err)
			}
			admission.record(report, event.CloseTimeNS)
		}
	}
	result.Episodes = ledger.Episodes()
	result.PositionQty = admission.positions[config.Symbol]
	return result, nil
}
