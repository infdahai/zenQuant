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
			if !reflect.DeepEqual(got, tc.Events) {
				t.Fatalf("events differ\ngot: %#v\nwant: %#v", got, tc.Events)
			}
		})
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
