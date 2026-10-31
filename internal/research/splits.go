// Package research contains offline research contracts.
package research

import (
	"math"
	"time"
)

// SplitWindow describes a named window in Unix nanoseconds. A nil end is open.
type SplitWindow struct {
	Name    string `json:"name"`
	StartNS int64  `json:"start_ns"`
	EndNS   *int64 `json:"end_ns"`
}

// DatasetSplitSpec defines the train, dev, and test windows for an asset.
type DatasetSplitSpec struct {
	Version string      `json:"version"`
	Asset   string      `json:"asset"`
	Train   SplitWindow `json:"train"`
	Dev     SplitWindow `json:"dev"`
	Test    SplitWindow `json:"test"`
}

// DefaultBTCSplitSpec returns the canonical BTC temporal split.
func DefaultBTCSplitSpec() DatasetSplitSpec {
	ns := func(year int) int64 { return time.Date(year, 1, 1, 0, 0, 0, 0, time.UTC).UnixNano() }
	trainEnd, devEnd := ns(2024), ns(2026)
	return DatasetSplitSpec{
		Version: "btc_temporal_v2_train_selection_only",
		Asset:   "BTC",
		Train:   SplitWindow{Name: "train", StartNS: ns(2020), EndNS: &trainEnd},
		Dev:     SplitWindow{Name: "dev", StartNS: ns(2024), EndNS: &devEnd},
		Test:    SplitWindow{Name: "test", StartNS: ns(2026)},
	}
}

// SplitWindows contains the window entries duplicated in a split summary.
type SplitWindows struct {
	Train SplitWindow `json:"train"`
	Dev   SplitWindow `json:"dev"`
	Test  SplitWindow `json:"test"`
}

// SplitSummary is the JSON-serializable Python split summary contract.
type SplitSummary struct {
	Version         string       `json:"version"`
	Asset           string       `json:"asset"`
	SelectionPolicy string       `json:"selection_policy"`
	Train           SplitWindow  `json:"train"`
	Dev             SplitWindow  `json:"dev"`
	Test            SplitWindow  `json:"test"`
	Windows         SplitWindows `json:"windows"`
}

// SplitSummaryJSON constructs an independent snapshot of the split contract.
func SplitSummaryJSON(spec DatasetSplitSpec) SplitSummary {
	copyWindow := func(w SplitWindow) SplitWindow {
		if w.EndNS != nil {
			end := *w.EndNS
			w.EndNS = &end
		}
		return w
	}
	return SplitSummary{
		Version: spec.Version, Asset: spec.Asset,
		SelectionPolicy: "train_only_search_dev_diagnostics_test_acceptance",
		Train:           copyWindow(spec.Train), Dev: copyWindow(spec.Dev), Test: copyWindow(spec.Test),
		Windows: SplitWindows{Train: copyWindow(spec.Train), Dev: copyWindow(spec.Dev), Test: copyWindow(spec.Test)},
	}
}

// PeriodWithinSplit follows Python's endpoint comparisons, without validating order.
func PeriodWithinSplit(startNS, endNS int64, window SplitWindow) bool {
	end := int64(math.MaxInt64)
	if window.EndNS != nil {
		end = *window.EndNS
	}
	return startNS >= window.StartNS && endNS <= end
}
