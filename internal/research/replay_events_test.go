package research

import (
	"encoding/json"
	"os"
	"reflect"
	"testing"
)

func TestReplayEventsPythonGolden(t *testing.T) {
	data, err := os.ReadFile("testdata/replay_events_golden.json")
	if err != nil {
		t.Fatal(err)
	}
	var fixture struct {
		Cases []struct {
			Name       string                    `json:"name"`
			Rows       map[string][]ReplayCandle `json:"rows"`
			Timeframes []string                  `json:"timeframes"`
			Execution  string                    `json:"execution"`
			Start      *int64                    `json:"start_ns"`
			End        *int64                    `json:"end_ns"`
			Events     []ReplayEvent             `json:"events"`
		} `json:"cases"`
	}
	if err := json.Unmarshal(data, &fixture); err != nil {
		t.Fatal(err)
	}
	for _, tc := range fixture.Cases {
		t.Run(tc.Name, func(t *testing.T) {
			got, err := PrepareReplayEvents(tc.Rows, tc.Timeframes, tc.Execution, tc.Start, tc.End)
			if err != nil {
				t.Fatal(err)
			}
			// Python preserves source timestamps; Go exposes the actual interval end.
			durations := map[string]int64{"5m": 300e9, "1h": 3600e9, "4h": 4 * 3600e9, "1d": 24 * 3600e9}
			want := make([]ReplayEvent, 0, len(tc.Events))
			for _, event := range tc.Events {
				event.CloseTimeNS = event.OpenTimeNS + durations[event.EventTimeframe]
				if tc.Start != nil && event.CloseTimeNS < *tc.Start || tc.End != nil && event.CloseTimeNS > *tc.End {
					continue
				}
				want = append(want, event)
			}
			if tc.Name == "inclusive_bounds_partial_hour" {
				// Go completes the first hour from constituents before the start.
				hour := ReplayEvent{ReplayCandle: ReplayCandle{Symbol: "BTC", Timeframe: "1h", OpenTimeNS: 0, CloseTimeNS: 3600e9, Open: 100, High: 113, Low: 99, Close: 112, Volume: 6, TradeCount: 24}, EventTimeframe: "1h"}
				want = append(want[:10], append([]ReplayEvent{hour}, want[10:]...)...)
			}
			if !reflect.DeepEqual(got, want) {
				t.Fatalf("events differ\ngot: %#v\nwant: %#v", got, tc.Events)
			}
		})
	}
}

func TestReplayEventsIndependentMixedBoundaryExpectation(t *testing.T) {
	const day int64 = 24 * 3600e9
	daily := replayTestBar("1d", 0, day, 100)
	four := replayTestBar("4h", day-4*3600e9, 4*3600e9, 100)
	daily.CloseTimeNS--
	four.CloseTimeNS -= 1_000_000
	start := day
	events, err := PrepareReplayEvents(map[string][]ReplayCandle{"1d": {daily}, "4h": {four}}, []string{"4h", "1d"}, "4h", &start, nil)
	if err != nil || len(events) != 2 || events[0].EventTimeframe != "1d" || events[1].EventTimeframe != "4h" || events[0].CloseTimeNS != day || events[1].CloseTimeNS != day {
		t.Fatalf("events=%+v err=%v", events, err)
	}
}

func TestReplayEventsBoundsUseCompletedInterval(t *testing.T) {
	end := int64(300e9)
	for _, offset := range []int64{0, -1, -1_000_000} {
		row := replayTestBar("5m", 0, end, 100)
		row.CloseTimeNS += offset
		for _, cutoffOffset := range []int64{-1_000_000, -1, 0, 1} {
			cutoff := end + cutoffOffset
			events, err := PrepareReplayEvents(map[string][]ReplayCandle{"5m": {row}}, []string{"5m"}, "5m", nil, &cutoff)
			if err != nil || (len(events) == 1) != (cutoff >= end) {
				t.Fatalf("offset=%d cutoff=%d events=%v err=%v", offset, cutoffOffset, events, err)
			}
			events, err = PrepareReplayEvents(map[string][]ReplayCandle{"5m": {row}}, []string{"5m"}, "5m", &cutoff, nil)
			if err != nil || (len(events) == 1) != (cutoff <= end) {
				t.Fatalf("start offset=%d cutoff=%d events=%v err=%v", offset, cutoffOffset, events, err)
			}
		}
	}
}

func TestReplayEventsFallbackAndAdjacentWindows(t *testing.T) {
	const step int64 = 300e9
	const hour = 12 * step
	rows := map[string][]ReplayCandle{}
	for i := int64(0); i < 12; i++ {
		bar := replayTestBar("5m", i*step, step, 100)
		bar.CloseTimeNS--
		rows["5m"] = append(rows["5m"], bar)
	}
	native := replayTestBar("1h", hour, hour, 110)
	rows["1h"] = []ReplayCandle{native}
	end := int64(hour)
	events, err := PrepareReplayEvents(rows, []string{"1h", "5m"}, "5m", nil, &end)
	if err != nil || len(events) != 13 || events[11].EventTimeframe != "1h" || events[11].CloseTimeNS != hour || events[11].Volume != 12 {
		t.Fatalf("fallback events=%v err=%v", events, err)
	}
	previousEnd := end - 1
	before, err := PrepareReplayEvents(rows, []string{"5m"}, "5m", nil, &previousEnd)
	if err != nil || len(before) != 11 {
		t.Fatalf("previous window events=%v err=%v", before, err)
	}
	after, err := PrepareReplayEvents(rows, []string{"5m"}, "5m", &end, nil)
	if err != nil || len(after) != 1 || after[0].CloseTimeNS != end {
		t.Fatalf("next window events=%v err=%v", after, err)
	}
	rows["1h"] = []ReplayCandle{replayTestBar("1h", 0, hour, 120)}
	events, err = PrepareReplayEvents(rows, []string{"1h", "5m"}, "5m", nil, &end)
	if err != nil || len(events) != 13 || events[11].Close != 120 {
		t.Fatalf("native events=%v err=%v", events, err)
	}
}

func TestReplayEventsIgnoresInvalidOutsideWindow(t *testing.T) {
	row := replayTestBar("5m", 0, 300e9, 100)
	row.CloseTimeNS -= 2
	start := int64(300e9 + 1)
	if _, err := PrepareReplayEvents(map[string][]ReplayCandle{"5m": {row}}, []string{"5m"}, "5m", &start, nil); err != nil {
		t.Fatal(err)
	}
}

func TestReplayEventsRejectInvalid(t *testing.T) {
	good := ReplayCandle{Symbol: "BTC", Timeframe: "5m", OpenTimeNS: 0, CloseTimeNS: 300*1e9 - 1, Open: 100, High: 101, Low: 99, Close: 100, Volume: 1, TradeCount: 1}
	late := good
	late.CloseTimeNS = 3600 * 1e9
	invalid := good
	invalid.High = 98
	other := good
	other.Symbol = "ETH"
	zero := good
	zero.CloseTimeNS = zero.OpenTimeNS
	short := good
	short.CloseTimeNS--
	misaligned := good
	misaligned.OpenTimeNS++
	misaligned.CloseTimeNS++
	start, end := int64(2), int64(1)
	cases := []struct {
		name       string
		rows       map[string][]ReplayCandle
		frames     []string
		exec       string
		start, end *int64
	}{
		{"unknown", nil, []string{"5m", "2h"}, "5m", nil, nil},
		{"duplicate", nil, []string{"5m", "5m"}, "5m", nil, nil},
		{"execution not selected", nil, []string{"1h"}, "5m", nil, nil},
		{"reversed bounds", nil, []string{"5m"}, "5m", &start, &end},
		{"invalid candle", map[string][]ReplayCandle{"5m": {invalid}}, []string{"5m"}, "5m", nil, nil},
		{"zero duration", map[string][]ReplayCandle{"5m": {zero}}, []string{"5m"}, "5m", nil, nil},
		{"short duration", map[string][]ReplayCandle{"5m": {short}}, []string{"5m"}, "5m", nil, nil},
		{"misaligned", map[string][]ReplayCandle{"5m": {misaligned}}, []string{"5m"}, "5m", nil, nil},
		{"mixed symbols", map[string][]ReplayCandle{"5m": {good, other}}, []string{"5m"}, "5m", nil, nil},
		{"future close", map[string][]ReplayCandle{"5m": {late}}, []string{"1h", "5m"}, "5m", nil, nil},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if _, err := PrepareReplayEvents(tc.rows, tc.frames, tc.exec, tc.start, tc.end); err == nil {
				t.Fatal("expected error")
			}
		})
	}
}
