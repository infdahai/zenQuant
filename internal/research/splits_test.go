package research

import (
	"encoding/json"
	"math"
	"testing"
)

func TestDefaultSplitSummaryGolden(t *testing.T) {
	// Captured from split_summary_json(default_btc_split_spec()).
	const want = `{"version":"btc_temporal_v2_train_selection_only","asset":"BTC","selection_policy":"train_only_search_dev_diagnostics_test_acceptance","train":{"name":"train","start_ns":1577836800000000000,"end_ns":1704067200000000000},"dev":{"name":"dev","start_ns":1704067200000000000,"end_ns":1767225600000000000},"test":{"name":"test","start_ns":1767225600000000000,"end_ns":null},"windows":{"train":{"name":"train","start_ns":1577836800000000000,"end_ns":1704067200000000000},"dev":{"name":"dev","start_ns":1704067200000000000,"end_ns":1767225600000000000},"test":{"name":"test","start_ns":1767225600000000000,"end_ns":null}}}`
	got, err := json.Marshal(SplitSummaryJSON(DefaultBTCSplitSpec()))
	if err != nil {
		t.Fatal(err)
	}
	if string(got) != want {
		t.Fatalf("summary differs\n got: %s\nwant: %s", got, want)
	}
}

func TestPeriodWithinSplit(t *testing.T) {
	end := int64(20)
	bounded := SplitWindow{StartNS: 10, EndNS: &end}
	open := SplitWindow{StartNS: 10}
	for _, tc := range []struct {
		name       string
		start, end int64
		window     SplitWindow
		want       bool
	}{
		{"exact boundaries", 10, 20, bounded, true},
		{"before start", 9, 20, bounded, false},
		{"after end", 10, 21, bounded, false},
		{"empty at end", 20, 20, bounded, true},
		{"reversed inside", 19, 11, bounded, true},
		{"reversed outside", 21, 9, bounded, true},
		{"open maximum", 10, math.MaxInt64, open, true},
		{"open before start", 9, math.MaxInt64, open, false},
		{"minimum start", math.MinInt64, math.MinInt64, SplitWindow{StartNS: math.MinInt64}, true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			if got := PeriodWithinSplit(tc.start, tc.end, tc.window); got != tc.want {
				t.Fatalf("got %v, want %v", got, tc.want)
			}
		})
	}
}

func TestCustomSummarySnapshotsWindows(t *testing.T) {
	end := int64(25)
	spec := DatasetSplitSpec{Version: "custom", Asset: "ETH", Train: SplitWindow{Name: "custom train", StartNS: -1, EndNS: &end}}
	summary := SplitSummaryJSON(spec)
	end = 30
	if summary.Version != "custom" || summary.Asset != "ETH" || summary.Train.Name != "custom train" || *summary.Train.EndNS != 25 {
		t.Fatalf("custom summary changed with input: %+v", summary)
	}
	*summary.Train.EndNS = 35
	if *summary.Windows.Train.EndNS != 25 {
		t.Fatal("summary windows share mutable end pointers")
	}
}
