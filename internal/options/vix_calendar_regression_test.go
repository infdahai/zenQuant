package options

import "testing"

func TestVixCrossYearMonthlyCalendar(t *testing.T) {
	r := vixFixture(t)
	c := &r.Futures.Calendar
	c.First = 202612
	c.Contracts = []VixContract{
		{Symbol: "VX/Z6", Tenor: "MONTHLY", Month: 202612, Last: 1797429600000, Settlement: "2026-12-16"},
		{Symbol: "VX/F7", Tenor: "MONTHLY", Month: 202701, Last: 1800453600000, Settlement: "2027-01-20"},
	}
	r.Futures.Quotes = r.Futures.Quotes[:2]
	r.Futures.Quotes[0].Symbol = "VX/Z6"
	r.Futures.Quotes[1].Symbol = "VX/F7"
	p := vixAnalyze(r).Futures
	if p.Status != "READY" || len(p.Selected) != 2 || p.Selected[0].Symbol != "VX/Z6" || p.Selected[1].Symbol != "VX/F7" {
		t.Fatalf("cross-year pair: %+v", p)
	}
}

func TestVixMissingMonthlyCalendar(t *testing.T) {
	for _, tc := range []struct {
		name           string
		remove         int
		status, reason string
	}{
		{"first month", 0, "MISSING", "FIRST_MONTHLY_CONTRACT_REQUIRED"},
		{"middle month", 2, "INVALID", "NONCONSECUTIVE_MONTHLY_CALENDAR"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			r := vixFixture(t)
			c := &r.Futures.Calendar
			c.Contracts = append(c.Contracts[:tc.remove], c.Contracts[tc.remove+1:]...)
			p := vixAnalyze(r).Futures
			if p.Status != tc.status || p.Reason != tc.reason || p.Metrics != nil {
				t.Fatalf("missing calendar month: %+v", p)
			}
		})
	}
}

func TestVixPreviousPairRollBoundary(t *testing.T) {
	for _, tc := range []struct {
		name              string
		offset            int64
		front, next, roll string
	}{
		{"before", -1, "VX/V6", "VX/X6", "UNCHANGED_FROM_SUPPLIED_PAIR"},
		{"at", 0, "VX/X6", "VX/Z6", "CHANGED_FROM_SUPPLIED_PAIR"},
		{"after", 1, "VX/X6", "VX/Z6", "CHANGED_FROM_SUPPLIED_PAIR"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			r := vixFixture(t)
			r.Futures.Previous = &VixPrevious{Cutoff: r.Cutoff, Front: "VX/V6", Next: "VX/X6"}
			r.Cutoff = uint64(int64(r.Futures.Calendar.Contracts[0].Last) + tc.offset)
			p := vixAnalyze(r).Futures
			if p.Status != "STALE" || p.Reason != "QUOTE_EXPIRED" || p.Metrics != nil || p.Roll != tc.roll || len(p.Selected) != 2 || p.Selected[0].Symbol != tc.front || p.Selected[1].Symbol != tc.next {
				t.Fatalf("roll boundary: %+v", p)
			}
		})
	}
}

func TestVixRejectsInvalidPreviousPair(t *testing.T) {
	for _, tc := range []struct {
		name     string
		previous func(VixRequest) VixPrevious
		reason   string
	}{
		{"equal cutoff", func(r VixRequest) VixPrevious { return VixPrevious{Cutoff: r.Cutoff, Front: "VX/V6", Next: "VX/X6"} }, "INVALID_PREVIOUS_PAIR_TIME"},
		{"before coverage", func(r VixRequest) VixPrevious {
			return VixPrevious{Cutoff: r.Futures.Calendar.From - 1, Front: "VX/V6", Next: "VX/X6"}
		}, "INVALID_PREVIOUS_PAIR_TIME"},
		{"wrong pair", func(r VixRequest) VixPrevious {
			return VixPrevious{Cutoff: r.Cutoff - 1, Front: "VX/X6", Next: "VX/Z6"}
		}, "PREVIOUS_PAIR_DISAGREES_WITH_CALENDAR"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			r := vixFixture(t)
			previous := tc.previous(r)
			r.Futures.Previous = &previous
			p := vixAnalyze(r).Futures
			if p.Status != "INVALID" || p.Reason != tc.reason || p.Metrics != nil {
				t.Fatalf("invalid previous pair: %+v", p)
			}
		})
	}
}
