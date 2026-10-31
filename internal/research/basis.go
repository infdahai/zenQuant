package research

import (
	"fmt"
	"math"
	"sort"
)

// BasisPrice is a close price at an event timestamp in nanoseconds.
type BasisPrice struct {
	TimeNS int64   `json:"time_ns"`
	Price  float64 `json:"price"`
}

type BasisSnapshot struct {
	BasisPct     float64  `json:"basis_pct"`
	BasisZ       float64  `json:"basis_z"`
	SpotPrice    float64  `json:"spot_price"`
	PerpPrice    float64  `json:"perp_price"`
	QualityFlags []string `json:"quality_flags"`
}

type basisPoint struct {
	time              int64
	basis, spot, perp float64
}

// BasisStore holds joined in-memory observations. All timestamps, including
// lookup cutoffs, use the same nonnegative nanosecond epoch convention.
type BasisStore struct {
	points             []basisPoint
	window, minSamples int
}

// NewBasisStore joins by timestamp; the last duplicate on each side wins.
// All corrections are assumed available at every lookup cutoff; without receipt
// timestamps this store cannot enforce point-in-time availability of corrections.
// Perpetual prices must be positive, a stricter rule than Python's store.
// Nonpositive spot prices are skipped, as in the Python store. The window
// includes the current point, leaving window-1 reference observations.
func NewBasisStore(spot, perp []BasisPrice, window, minSamples int) (*BasisStore, error) {
	if window < 2 || minSamples < 1 || minSamples >= window {
		return nil, fmt.Errorf("window must be at least 2 and min samples in [1, window)")
	}
	collect := func(rows []BasisPrice) (map[int64]float64, error) {
		m := make(map[int64]float64, len(rows))
		for _, row := range rows {
			if row.TimeNS < 0 || !finite(row.Price) {
				return nil, fmt.Errorf("invalid timestamp or non-finite price")
			}
			m[row.TimeNS] = row.Price
		}
		return m, nil
	}
	s, err := collect(spot)
	if err != nil {
		return nil, err
	}
	for _, row := range perp {
		if row.Price <= 0 {
			return nil, fmt.Errorf("perpetual price must be positive")
		}
	}
	p, err := collect(perp)
	if err != nil {
		return nil, err
	}
	times := make([]int64, 0, len(s))
	for ts := range s {
		if _, ok := p[ts]; ok && s[ts] > 0 {
			times = append(times, ts)
		}
	}
	sort.Slice(times, func(i, j int) bool { return times[i] < times[j] })
	store := &BasisStore{window: window, minSamples: minSamples}
	for _, ts := range times {
		delta := p[ts] - s[ts]
		if !finite(delta) {
			return nil, fmt.Errorf("basis subtraction overflow")
		}
		basis := delta / s[ts]
		if !finite(basis) {
			return nil, fmt.Errorf("basis division overflow")
		}
		store.points = append(store.points, basisPoint{ts, basis, s[ts], p[ts]})
	}
	return store, nil
}

// RobustZ scores current using all values except the last; it never mutates values.
func RobustZ(values []float64, current float64, minSamples int) (float64, error) {
	if minSamples < 1 || !finite(current) {
		return 0, fmt.Errorf("invalid robust z input")
	}
	for _, v := range values {
		if !finite(v) {
			return 0, fmt.Errorf("non-finite robust z input")
		}
	}
	if len(values) == 0 || len(values)-1 < minSamples {
		return 0, nil
	}
	ref := append([]float64(nil), values[:len(values)-1]...)
	median := func(a []float64) (float64, error) {
		sort.Float64s(a)
		n := len(a)
		if n%2 == 1 {
			return a[n/2], nil
		}
		sum := a[n/2-1] + a[n/2]
		if !finite(sum) {
			return 0, fmt.Errorf("median overflow")
		}
		return sum / 2, nil
	}
	med, err := median(ref)
	if err != nil {
		return 0, err
	}
	for i, v := range ref {
		d := v - med
		if !finite(d) {
			return 0, fmt.Errorf("deviation overflow")
		}
		ref[i] = math.Abs(d)
	}
	mad, err := median(ref)
	if err != nil {
		return 0, err
	}
	if mad < 1e-9 {
		return 0, nil
	}
	delta := current - med
	if !finite(delta) {
		return 0, fmt.Errorf("z subtraction overflow")
	}
	scaled := 0.6745 * delta
	// An infinite quotient has a well-defined clamped result. All inputs and
	// earlier intermediates are finite, and MAD is positive here.
	z := scaled / mad
	return math.Max(-6, math.Min(6, z)), nil
}

// Lookup returns the last observation at or before cutoffNS.
func (s *BasisStore) Lookup(cutoffNS int64) (BasisSnapshot, error) {
	empty := BasisSnapshot{QualityFlags: []string{}}
	if s == nil || cutoffNS < 0 {
		return empty, fmt.Errorf("invalid store or cutoff")
	}
	i := sort.Search(len(s.points), func(i int) bool { return s.points[i].time > cutoffNS }) - 1
	if i < 0 {
		return empty, nil
	}
	start := i + 1 - s.window
	if start < 0 {
		start = 0
	}
	values := make([]float64, 0, i+1-start)
	for _, p := range s.points[start : i+1] {
		values = append(values, p.basis)
	}
	p := s.points[i]
	z, err := RobustZ(values, p.basis, s.minSamples)
	if err != nil {
		return empty, err
	}
	return BasisSnapshot{BasisPct: sizingRound(p.basis, 6), BasisZ: z, SpotPrice: p.spot, PerpPrice: p.perp, QualityFlags: []string{}}, nil
}
