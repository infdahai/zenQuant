package research

import (
	"fmt"
	"math"
	"sort"
)

// ReplayCandle is the bounded OHLCV subset used by offline event preparation.
// Times are Unix nanoseconds. Opens must align to the timeframe's UTC epoch
// grid; closes must equal the interval end, end - 1 ns, or end - 1 ms.
type ReplayCandle struct {
	Symbol      string  `json:"symbol"`
	Timeframe   string  `json:"timeframe"`
	OpenTimeNS  int64   `json:"open_time_ns"`
	CloseTimeNS int64   `json:"close_time_ns"`
	Open        float64 `json:"open"`
	High        float64 `json:"high"`
	Low         float64 `json:"low"`
	Close       float64 `json:"close"`
	Volume      float64 `json:"volume"`
	TradeCount  int64   `json:"trade_count"`
}

// ReplayEvent pairs a candle with the selected event timeframe.
type ReplayEvent struct {
	ReplayCandle
	EventTimeframe string `json:"event_timeframe"`
}

// PrepareReplayEvents filters on inclusive interval-end bounds and validates selected
// intervals, then sorts by interval end and Python's timeframe priority. Adjacent
// half-open research splits [start,end) must pass end-1ns as the inclusive end;
// otherwise a boundary bar belongs to both replays. Event close times are
// normalized to the interval end, so observations never precede bar completion.
// Python filters and sorts raw close timestamps, so inclusive close conventions
// intentionally differ. When 1h is selected,
// its filtered series is empty, and execution is 5m, complete 5m hours are
// aggregated, including constituents before the start bound when their hour
// closes inside the window. Input order breaks ties within a timeframe, as in Python's stable
// sort; callers should supply each series in stable close-time source order.
// This is an in-memory, single-symbol subset: no stores, auxiliary joins, cache,
// execution, or arbitrary candle fields are reproduced. Inputs are validated
// more strictly than Python: all bars require aligned, full-duration intervals,
// with exclusive closes or inclusive closes one nanosecond or millisecond earlier.
// Volume aggregation follows Python 3.12+ compensated summation, not 3.11.
func PrepareReplayEvents(rows map[string][]ReplayCandle, timeframes []string, execution string, startNS, endNS *int64) ([]ReplayEvent, error) {
	priority := map[string]int{"1d": 0, "4h": 1, "1h": 2, "5m": 3}
	if _, ok := priority[execution]; !ok {
		return nil, fmt.Errorf("unsupported execution timeframe %q", execution)
	}
	if startNS != nil && endNS != nil && *startNS > *endNS {
		return nil, fmt.Errorf("start exceeds end")
	}
	selected := make(map[string]bool)
	for _, tf := range timeframes {
		if _, ok := priority[tf]; !ok {
			return nil, fmt.Errorf("unsupported timeframe %q", tf)
		}
		if selected[tf] {
			return nil, fmt.Errorf("duplicate timeframe %q", tf)
		}
		selected[tf] = true
	}
	if !selected[execution] {
		return nil, fmt.Errorf("execution timeframe must be selected")
	}
	durations := map[string]int64{"5m": 300 * 1e9, "1h": 3600 * 1e9, "4h": 4 * 3600 * 1e9, "1d": 24 * 3600 * 1e9}
	filtered := make(map[string][]ReplayCandle)
	var hourlyInputs []ReplayCandle
	var symbol string
	// Check native hours first so unused fallback constituents are not validated.
	validationOrder := append([]string(nil), timeframes...)
	sort.SliceStable(validationOrder, func(i, j int) bool { return priority[validationOrder[i]] < priority[validationOrder[j]] })
	for _, tf := range validationOrder {
		for _, row := range rows[tf] {
			duration := durations[tf]
			// An overflowing interval cannot be assigned a representable end.
			// Filter it by its source timestamp before reporting invalid input.
			if row.OpenTimeNS > math.MaxInt64-duration {
				if startNS != nil && row.CloseTimeNS < *startNS || endNS != nil && row.CloseTimeNS > *endNS {
					continue
				}
				return nil, fmt.Errorf("invalid %s candle interval", tf)
			}
			closeTime := row.OpenTimeNS + duration
			inWindow := (startNS == nil || closeTime >= *startNS) && (endNS == nil || closeTime <= *endNS)
			// Only retain earlier constituents of the hour containing the start;
			// the resulting aggregate is still filtered on its own close time.
			forHour := false
			if tf == "5m" && selected["1h"] && len(filtered["1h"]) == 0 && execution == "5m" {
				rem := row.OpenTimeNS % durations["1h"]
				if rem < 0 {
					rem += durations["1h"]
				}
				if row.OpenTimeNS >= math.MinInt64+rem {
					hourStart := row.OpenTimeNS - rem
					if hourStart <= math.MaxInt64-durations["1h"] {
						hourEnd := hourStart + durations["1h"]
						forHour = (startNS == nil || hourEnd >= *startNS) && (endNS == nil || hourEnd <= *endNS)
					}
				}
			}
			if !inWindow && !forHour {
				continue
			}
			if row.OpenTimeNS%duration != 0 || (row.CloseTimeNS != closeTime && row.CloseTimeNS != closeTime-1 && row.CloseTimeNS != closeTime-1_000_000) {
				return nil, fmt.Errorf("invalid %s candle interval", tf)
			}
			if row.Timeframe != tf || row.Symbol == "" || !finite(row.Open) || !finite(row.High) || !finite(row.Low) || !finite(row.Close) || !finite(row.Volume) || row.Low <= 0 || row.Open < row.Low || row.Open > row.High || row.Close < row.Low || row.Close > row.High || row.Volume < 0 || row.TradeCount < 0 {
				return nil, fmt.Errorf("invalid %s candle", tf)
			}
			if symbol != "" && symbol != row.Symbol {
				return nil, fmt.Errorf("candles must have one symbol")
			}
			symbol = row.Symbol
			if inWindow {
				filtered[tf] = append(filtered[tf], row)
			}
			if forHour {
				hourlyInputs = append(hourlyInputs, row)
			}
		}
	}
	if selected["1h"] && len(filtered["1h"]) == 0 && execution == "5m" {
		hours, err := aggregateReplayHours(hourlyInputs)
		if err != nil {
			return nil, err
		}
		filtered["1h"] = hours
	}
	events := make([]ReplayEvent, 0)
	for _, tf := range timeframes {
		for _, row := range filtered[tf] {
			row.CloseTimeNS = row.OpenTimeNS + durations[tf]
			events = append(events, ReplayEvent{ReplayCandle: row, EventTimeframe: tf})
		}
	}
	sort.SliceStable(events, func(i, j int) bool {
		if events[i].CloseTimeNS != events[j].CloseTimeNS {
			return events[i].CloseTimeNS < events[j].CloseTimeNS
		}
		return priority[events[i].EventTimeframe] < priority[events[j].EventTimeframe]
	})
	return events, nil
}

func aggregateReplayHours(rows []ReplayCandle) ([]ReplayCandle, error) {
	const hour int64 = 3600 * 1e9
	const step int64 = 300 * 1e9
	buckets := make(map[int64][]ReplayCandle)
	for _, row := range rows {
		// Python's modulo is non-negative, including before the Unix epoch.
		rem := row.OpenTimeNS % hour
		if rem < 0 {
			rem += hour
		}
		if row.OpenTimeNS < math.MinInt64+rem {
			return nil, fmt.Errorf("hour bucket outside int64 range")
		}
		start := row.OpenTimeNS - rem
		if start > math.MaxInt64-hour || row.CloseTimeNS > start+hour {
			return nil, fmt.Errorf("5m candle closes outside its hour")
		}
		buckets[start] = append(buckets[start], row)
	}
	starts := make([]int64, 0, len(buckets))
	for start := range buckets {
		starts = append(starts, start)
	}
	sort.Slice(starts, func(i, j int) bool { return starts[i] < starts[j] })
	var result []ReplayCandle
	for _, start := range starts {
		bucket := buckets[start]
		sort.SliceStable(bucket, func(i, j int) bool { return bucket[i].OpenTimeNS < bucket[j].OpenTimeNS })
		if len(bucket) != 12 {
			continue
		}
		contiguous := true
		for i := 1; i < len(bucket); i++ {
			if bucket[i].OpenTimeNS-bucket[i-1].OpenTimeNS != step {
				contiguous = false
				break
			}
		}
		if !contiguous {
			continue
		}
		first, last := bucket[0], bucket[11]
		out := ReplayCandle{Symbol: first.Symbol, Timeframe: "1h", OpenTimeNS: first.OpenTimeNS, CloseTimeNS: last.CloseTimeNS, Open: first.Open, High: first.High, Low: first.Low, Close: last.Close}
		var volume pythonSum
		for _, row := range bucket {
			if row.CloseTimeNS > last.CloseTimeNS {
				return nil, fmt.Errorf("5m constituent closes after aggregate")
			}
			if row.High > out.High {
				out.High = row.High
			}
			if row.Low < out.Low {
				out.Low = row.Low
			}
			volume.add(row.Volume)
			if row.TradeCount > math.MaxInt64-out.TradeCount {
				return nil, fmt.Errorf("trade count overflow")
			}
			out.TradeCount += row.TradeCount
		}
		out.Volume = volume.value()
		if !finite(out.Volume) {
			return nil, fmt.Errorf("volume overflow")
		}
		result = append(result, out)
	}
	return result, nil
}
