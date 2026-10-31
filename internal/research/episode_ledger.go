package research

import (
	"crypto/sha256"
	"fmt"
	"math"
	"sort"
)

// ExecutionReport is the fill information consumed by the episode ledger.
type ExecutionReport struct {
	ClientOrderID     string  `json:"client_order_id"`
	Symbol            string  `json:"symbol"`
	Side              int     `json:"side"`
	Status            string  `json:"status"`
	FilledQty         float64 `json:"filled_qty"`
	AvgPrice          float64 `json:"avg_price"`
	Fee               float64 `json:"fee"`
	TransactionTimeNS int64   `json:"transaction_time_ns"`
	RejectReason      string  `json:"reject_reason"`
}

type FundingAllocation struct {
	Symbol      string  `json:"symbol"`
	TimestampNS int64   `json:"timestamp_ns"`
	PnL         float64 `json:"pnl"`
}
type PriceObservation struct {
	Symbol      string  `json:"symbol"`
	TimestampNS int64   `json:"timestamp_ns"`
	High        float64 `json:"high"`
	Low         float64 `json:"low"`
	Close       float64 `json:"close"`
}

type PositionEpisode struct {
	EpisodeID         string   `json:"episode_id"`
	Symbol            string   `json:"symbol"`
	Side              int      `json:"side"`
	OpenedAtNS        int64    `json:"opened_at_ns"`
	ClosedAtNS        *int64   `json:"closed_at_ns"`
	EntryPrice        float64  `json:"entry_price"`
	ExitPrice         *float64 `json:"exit_price"`
	MaxAbsQty         float64  `json:"max_abs_qty"`
	RemainingQty      float64  `json:"remaining_qty"`
	GrossRealizedPnL  float64  `json:"gross_realized_pnl"`
	Fees              float64  `json:"fees"`
	FundingPnL        float64  `json:"funding_pnl"`
	NetPnL            float64  `json:"net_pnl"`
	MFEPnL            float64  `json:"mfe_pnl"`
	MAEPnL            float64  `json:"mae_pnl"`
	UnrealizedPnL     float64  `json:"unrealized_pnl"`
	FillCount         int      `json:"fill_count"`
	Closed            bool     `json:"closed"`
	AddCount          int      `json:"add_count"`
	ReduceCount       int      `json:"reduce_count"`
	HoldingDurationNS int64    `json:"holding_duration_ns"`
}

type episodeState struct {
	episode               PositionEpisode
	lastEventNS           int64
	exitQty, exitNotional float64
}

func (s *episodeState) snapshot(closedAt *int64) PositionEpisode {
	e := s.episode
	if s.exitQty > 0 {
		value := s.exitNotional / s.exitQty
		e.ExitPrice = &value
	}
	e.Closed = closedAt != nil
	e.ClosedAtNS = closedAt
	end := s.lastEventNS
	// Python's closed_at_ns or last_event_ns treats zero as false.
	if closedAt != nil && *closedAt != 0 {
		end = *closedAt
	}
	e.HoldingDurationNS = end - e.OpenedAtNS
	if e.HoldingDurationNS < 0 {
		e.HoldingDurationNS = 0
	}
	e.NetPnL = e.GrossRealizedPnL + e.FundingPnL - e.Fees
	if e.Closed {
		e.RemainingQty = 0
		e.UnrealizedPnL = 0
	}
	return e
}

// PositionEpisodeLedger maintains independent per-symbol episode accounting.
// Its zero value is ready to use. Copies made after first use share all state;
// copies of an unused zero value initialize independently. It is not concurrency-safe.
// Events that would make an open episode's duration exceed int64 nanoseconds
// are rejected without changing the ledger. Unlike Python, non-finite numeric
// inputs are rejected, including those on otherwise ignored execution reports.
// Events producing non-finite accounting results are also rejected atomically.
type PositionEpisodeLedger struct {
	*positionEpisodeLedgerState
}

type positionEpisodeLedgerState struct {
	open      map[string]*episodeState
	closed    []PositionEpisode
	last      map[string]int64
	sequences map[string]int
	previous  map[string]int
	switches  int
}

func NewPositionEpisodeLedger() *PositionEpisodeLedger {
	l := &PositionEpisodeLedger{}
	l.init()
	return l
}
func (l *PositionEpisodeLedger) init() {
	if l.positionEpisodeLedgerState == nil {
		l.positionEpisodeLedgerState = &positionEpisodeLedgerState{
			open: make(map[string]*episodeState), last: make(map[string]int64),
			sequences: make(map[string]int), previous: make(map[string]int),
		}
	}
}
func (l *PositionEpisodeLedger) checkTime(symbol string, timestamp int64) error {
	l.init()
	if last, ok := l.last[symbol]; ok && timestamp < last {
		return fmt.Errorf("events for %s must be chronological: timestamp_ns=%d last_timestamp_ns=%d", symbol, timestamp, last)
	}
	if s := l.open[symbol]; s != nil && s.episode.OpenedAtNS < 0 && timestamp > math.MaxInt64+s.episode.OpenedAtNS {
		return fmt.Errorf("episode holding duration for %s exceeds int64 nanoseconds", symbol)
	}
	l.last[symbol] = timestamp
	return nil
}
func (l *PositionEpisodeLedger) newEpisode(symbol string, side int, qty, price, fee float64, timestamp int64) *episodeState {
	l.sequences[symbol]++
	if previous, ok := l.previous[symbol]; ok && previous != side {
		l.switches++
	}
	l.previous[symbol] = side
	hash := sha256.Sum256([]byte(fmt.Sprintf("%s|%d|%d|%d", symbol, timestamp, side, l.sequences[symbol])))
	return &episodeState{episode: PositionEpisode{EpisodeID: fmt.Sprintf("%x", hash), Symbol: symbol, Side: side, OpenedAtNS: timestamp, EntryPrice: price, RemainingQty: qty, MaxAbsQty: qty, Fees: fee, FillCount: 1}, lastEventNS: timestamp}
}
func (l *PositionEpisodeLedger) PositionQty(symbol string) float64 {
	l.init()
	if s := l.open[symbol]; s != nil {
		return float64(s.episode.Side) * s.episode.RemainingQty
	}
	return 0
}
func (l *PositionEpisodeLedger) PositionSwitchCount() int { l.init(); return l.switches }
func (l *PositionEpisodeLedger) ClosedEpisodes() []PositionEpisode {
	l.init()
	result := append([]PositionEpisode{}, l.closed...)
	for i := range result {
		if result[i].ClosedAtNS != nil {
			value := *result[i].ClosedAtNS
			result[i].ClosedAtNS = &value
		}
		if result[i].ExitPrice != nil {
			value := *result[i].ExitPrice
			result[i].ExitPrice = &value
		}
	}
	return result
}
func (l *PositionEpisodeLedger) OpenEpisodes() []PositionEpisode {
	l.init()
	symbols := make([]string, 0, len(l.open))
	for symbol := range l.open {
		symbols = append(symbols, symbol)
	}
	sort.Strings(symbols)
	result := make([]PositionEpisode, 0, len(symbols))
	for _, symbol := range symbols {
		result = append(result, l.open[symbol].snapshot(nil))
	}
	return result
}
func (l *PositionEpisodeLedger) Episodes() []PositionEpisode {
	result := append(l.ClosedEpisodes(), l.OpenEpisodes()...)
	sort.SliceStable(result, func(i, j int) bool {
		if result[i].OpenedAtNS != result[j].OpenedAtNS {
			return result[i].OpenedAtNS < result[j].OpenedAtNS
		}
		return result[i].Symbol < result[j].Symbol
	})
	return result
}
func observeEpisode(s *episodeState, high, low, close float64) error {
	e := &s.episode
	var favorable, adverse float64
	if e.Side > 0 {
		favorable = e.GrossRealizedPnL + (high-e.EntryPrice)*e.RemainingQty
		adverse = e.GrossRealizedPnL + (low-e.EntryPrice)*e.RemainingQty
	} else {
		favorable = e.GrossRealizedPnL + (e.EntryPrice-low)*e.RemainingQty
		adverse = e.GrossRealizedPnL + (e.EntryPrice-high)*e.RemainingQty
	}
	if !finite(favorable) || !finite(adverse) {
		return fmt.Errorf("episode accounting produced non-finite excursion")
	}
	if favorable > e.MFEPnL {
		e.MFEPnL = favorable
	}
	if adverse < e.MAEPnL {
		e.MAEPnL = adverse
	}
	e.UnrealizedPnL = (close - e.EntryPrice) * e.RemainingQty * float64(e.Side)
	if !finite(e.UnrealizedPnL) {
		return fmt.Errorf("episode accounting produced non-finite unrealized pnl")
	}
	return nil
}

func finite(value float64) bool { return !math.IsNaN(value) && !math.IsInf(value, 0) }

func finiteEpisode(e PositionEpisode) bool {
	values := []float64{e.EntryPrice, e.MaxAbsQty, e.RemainingQty, e.GrossRealizedPnL, e.Fees, e.FundingPnL, e.NetPnL, e.MFEPnL, e.MAEPnL, e.UnrealizedPnL}
	if e.ExitPrice != nil {
		values = append(values, *e.ExitPrice)
	}
	for _, v := range values {
		if !finite(v) {
			return false
		}
	}
	return true
}

// applyAtomic groups multiple ledger operations into one transaction.
func (l *PositionEpisodeLedger) applyAtomic(apply func(*PositionEpisodeLedger) error) error {
	staged := NewPositionEpisodeLedger()
	if l.positionEpisodeLedgerState != nil {
		for k, v := range l.open {
			state := *v
			staged.open[k] = &state
		}
		for k, v := range l.last {
			staged.last[k] = v
		}
		for k, v := range l.sequences {
			staged.sequences[k] = v
		}
		for k, v := range l.previous {
			staged.previous[k] = v
		}
		// Cap the shared, immutable prefix so appends cannot mutate its backing array.
		staged.closed = l.closed[:len(l.closed):len(l.closed)]
		staged.switches = l.switches
	}
	if err := apply(staged); err != nil {
		return err
	}
	if l.positionEpisodeLedgerState == nil {
		l.positionEpisodeLedgerState = staged.positionEpisodeLedgerState
	} else {
		*l.positionEpisodeLedgerState = *staged.positionEpisodeLedgerState
	}
	return nil
}

// applyEvent stages only the affected symbol so failed arithmetic cannot change
// positions, clocks, sequences, closed episodes, or shared ledger copies.
func (l *PositionEpisodeLedger) applyEvent(symbol string, apply func(*PositionEpisodeLedger) error) error {
	staged := NewPositionEpisodeLedger()
	if l.positionEpisodeLedgerState != nil {
		if s := l.open[symbol]; s != nil {
			copy := *s
			staged.open[symbol] = &copy
		}
		if value, ok := l.last[symbol]; ok {
			staged.last[symbol] = value
		}
		if value, ok := l.sequences[symbol]; ok {
			staged.sequences[symbol] = value
		}
		if value, ok := l.previous[symbol]; ok {
			staged.previous[symbol] = value
		}
		staged.switches = l.switches
	}
	if err := apply(staged); err != nil {
		return err
	}
	if s := staged.open[symbol]; s != nil {
		if !finite(s.exitQty) || !finite(s.exitNotional) || !finiteEpisode(s.snapshot(nil)) {
			return fmt.Errorf("episode accounting produced non-finite result")
		}
	}
	for _, e := range staged.closed {
		if !finiteEpisode(e) {
			return fmt.Errorf("episode accounting produced non-finite result")
		}
	}
	l.init()
	if s := staged.open[symbol]; s != nil {
		l.open[symbol] = s
	} else {
		delete(l.open, symbol)
	}
	if value, ok := staged.last[symbol]; ok {
		l.last[symbol] = value
	}
	if value, ok := staged.sequences[symbol]; ok {
		l.sequences[symbol] = value
	}
	if value, ok := staged.previous[symbol]; ok {
		l.previous[symbol] = value
	}
	l.switches = staged.switches
	l.closed = append(l.closed, staged.closed...)
	return nil
}

// OnFill processes any report with positive quantity and price, regardless of status.
func (l *PositionEpisodeLedger) OnFill(r ExecutionReport) error {
	if !finite(r.FilledQty) || !finite(r.AvgPrice) || !finite(r.Fee) {
		return fmt.Errorf("execution report quantity, price and fee must be finite")
	}
	if r.FilledQty <= 0 || r.AvgPrice <= 0 {
		return nil
	}
	return l.applyEvent(r.Symbol, func(staged *PositionEpisodeLedger) error { return staged.onFill(r) })
}
func (l *PositionEpisodeLedger) AllocateFunding(a FundingAllocation) error {
	return l.applyEvent(a.Symbol, func(staged *PositionEpisodeLedger) error { return staged.allocateFunding(a) })
}
func (l *PositionEpisodeLedger) ObservePrice(o PriceObservation) error {
	return l.applyEvent(o.Symbol, func(staged *PositionEpisodeLedger) error { return staged.observePrice(o) })
}

func (l *PositionEpisodeLedger) onFill(r ExecutionReport) error {
	if r.Side != -1 && r.Side != 1 {
		return fmt.Errorf("filled execution report side must be -1 or 1")
	}
	if err := l.checkTime(r.Symbol, r.TransactionTimeNS); err != nil {
		return err
	}
	s := l.open[r.Symbol]
	if s == nil {
		l.open[r.Symbol] = l.newEpisode(r.Symbol, r.Side, r.FilledQty, r.AvgPrice, r.Fee, r.TransactionTimeNS)
		return nil
	}
	e := &s.episode
	if err := observeEpisode(s, r.AvgPrice, r.AvgPrice, r.AvgPrice); err != nil {
		return err
	}
	s.lastEventNS = r.TransactionTimeNS
	if e.Side == r.Side {
		next := e.RemainingQty + r.FilledQty
		e.EntryPrice = (e.EntryPrice*e.RemainingQty + r.AvgPrice*r.FilledQty) / next
		e.RemainingQty = next
		if next > e.MaxAbsQty {
			e.MaxAbsQty = next
		}
		e.Fees += r.Fee
		e.FillCount++
		e.AddCount++
		return nil
	}
	closeQty := e.RemainingQty
	if r.FilledQty < closeQty {
		closeQty = r.FilledQty
	}
	remainder := r.FilledQty - closeQty
	closeFee := r.Fee
	if remainder <= 1e-12 {
		remainder = 0
	} else {
		closeFee = r.Fee * (closeQty / r.FilledQty)
	}
	e.GrossRealizedPnL += (r.AvgPrice - e.EntryPrice) * closeQty * float64(e.Side)
	e.Fees += closeFee
	s.exitQty += closeQty
	s.exitNotional += closeQty * r.AvgPrice
	if !finite(s.exitNotional) || !finite(s.exitQty) {
		return fmt.Errorf("episode accounting produced non-finite exit notional")
	}
	e.RemainingQty -= closeQty
	if e.RemainingQty <= 1e-12 {
		e.RemainingQty = 0
		e.UnrealizedPnL = 0
	} else {
		e.UnrealizedPnL = (r.AvgPrice - e.EntryPrice) * e.RemainingQty * float64(e.Side)
	}
	e.FillCount++
	e.ReduceCount++
	if e.RemainingQty > 0 {
		return nil
	}
	timestamp := r.TransactionTimeNS
	l.closed = append(l.closed, s.snapshot(&timestamp))
	delete(l.open, r.Symbol)
	if remainder > 0 {
		l.open[r.Symbol] = l.newEpisode(r.Symbol, r.Side, remainder, r.AvgPrice, r.Fee-closeFee, r.TransactionTimeNS)
	}
	return nil
}
func (l *PositionEpisodeLedger) allocateFunding(a FundingAllocation) error {
	if !finite(a.PnL) {
		return fmt.Errorf("funding pnl must be finite")
	}
	if err := l.checkTime(a.Symbol, a.TimestampNS); err != nil {
		return err
	}
	if s := l.open[a.Symbol]; s != nil {
		s.episode.FundingPnL += a.PnL
		s.lastEventNS = a.TimestampNS
	}
	return nil
}
func (l *PositionEpisodeLedger) OnFunding(a FundingAllocation) error { return l.AllocateFunding(a) }
func (l *PositionEpisodeLedger) observePrice(o PriceObservation) error {
	if !finite(o.High) || !finite(o.Low) || !finite(o.Close) {
		return fmt.Errorf("price observation high, low and close must be finite")
	}
	if o.High < o.Low {
		return fmt.Errorf("price observation high must be at least low")
	}
	if o.Close <= 0 || o.Low <= 0 {
		return fmt.Errorf("price observations must be positive")
	}
	if err := l.checkTime(o.Symbol, o.TimestampNS); err != nil {
		return err
	}
	if s := l.open[o.Symbol]; s != nil {
		if err := observeEpisode(s, o.High, o.Low, o.Close); err != nil {
			return err
		}
		s.lastEventNS = o.TimestampNS
	}
	return nil
}

// BuildPositionEpisodes merges events by timestamp, then price, funding, fill.
// Events within each input retain their original order at equal timestamps.
func BuildPositionEpisodes(fills []ExecutionReport, funding []FundingAllocation, prices []PriceObservation) ([]PositionEpisode, error) {
	type event struct {
		timestamp int64
		kind      int
		index     int
	}
	events := make([]event, 0, len(fills)+len(funding)+len(prices))
	for i, p := range prices {
		events = append(events, event{p.TimestampNS, 0, i})
	}
	for i, f := range funding {
		events = append(events, event{f.TimestampNS, 1, i})
	}
	for i, f := range fills {
		events = append(events, event{f.TransactionTimeNS, 2, i})
	}
	sort.SliceStable(events, func(i, j int) bool {
		if events[i].timestamp != events[j].timestamp {
			return events[i].timestamp < events[j].timestamp
		}
		return events[i].kind < events[j].kind
	})
	var l PositionEpisodeLedger
	for _, e := range events {
		var err error
		switch e.kind {
		case 0:
			err = l.ObservePrice(prices[e.index])
		case 1:
			err = l.AllocateFunding(funding[e.index])
		case 2:
			err = l.OnFill(fills[e.index])
		}
		if err != nil {
			return nil, err
		}
	}
	return l.Episodes(), nil
}
