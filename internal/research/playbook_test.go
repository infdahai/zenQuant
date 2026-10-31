package research

import (
	"encoding/json"
	"math"
	"os"
	"reflect"
	"testing"
)

func TestPlaybookPythonGolden(t *testing.T) {
	data, err := os.ReadFile("testdata/playbook_golden.json")
	if err != nil {
		t.Fatal(err)
	}
	var cases []struct {
		Name     string            `json:"name"`
		State    PlaybookState     `json:"state"`
		Config   PlaybookConfig    `json:"config"`
		Expected PlaybookSelection `json:"expected"`
	}
	if err := json.Unmarshal(data, &cases); err != nil {
		t.Fatal(err)
	}
	for _, tc := range cases {
		t.Run(tc.Name, func(t *testing.T) {
			got, err := SelectPlaybook(tc.State, tc.Config)
			if err != nil {
				t.Fatal(err)
			}
			if !reflect.DeepEqual(got, tc.Expected) {
				t.Fatalf("got %+v\nwant %+v", got, tc.Expected)
			}
		})
	}
}

func TestPlaybookConfidenceBoundary(t *testing.T) {
	thresholds := []float64{math.SmallestNonzeroFloat64, 0.35, 0.5, 1, 2, math.MaxFloat64}
	for _, threshold := range thresholds {
		// Cover zero, a subnormal product, ordinary values and the maximum float.
		for _, product := range []float64{0, math.SmallestNonzeroFloat64, 0.0625, 1, math.MaxFloat64} {
			got := playbookConfidenceBelow(product, threshold)
			// At these products the fourth root is known exactly, except MaxFloat64,
			// whose root is strictly between 1 and MaxFloat64.
			var root float64
			switch product {
			case 0:
				root = 0
			case math.SmallestNonzeroFloat64:
				root = math.Ldexp(1, -268) * math.Sqrt2
			case 0.0625:
				root = 0.5
			case 1:
				root = 1
			default:
				root = math.Ldexp(1, 256)
			}
			if got != (root < threshold) {
				t.Fatalf("product=%g threshold=%g got %t", product, threshold, got)
			}
		}
	}
	p := 0.35 * 0.35
	p *= 0.35
	p *= 0.35
	for _, tc := range []struct {
		threshold float64
		below     bool
	}{
		{math.Nextafter(0.35, math.Inf(-1)), false}, {0.35, false}, {math.Nextafter(0.35, math.Inf(1)), true},
	} {
		if got := playbookConfidenceBelow(p, tc.threshold); got != tc.below {
			t.Fatalf("threshold %.17g: got %t want %t", tc.threshold, got, tc.below)
		}
	}
}

func TestPlaybookConfidencePythonPowDivergence(t *testing.T) {
	// Some Python libm implementations return 0.959600497389907 for this
	// product. The correctly rounded fourth root is the next float above it.
	const product = 0.8479336249272827
	const pythonRoot = 0.959600497389907
	const roundedRoot = 0.9596004973899072
	state := PlaybookState{DirectionalState: "NEUTRAL", DirectionalConfidence: product, VolatilityConfidence: 1, PositioningConfidence: 1, QualityConfidence: 1, QualityState: "TRADABLE"}
	config := DefaultPlaybookConfig()
	for _, tc := range []struct {
		threshold float64
		want      string
	}{
		{pythonRoot, "RANGE_LIGHT"},
		{roundedRoot, "RANGE_LIGHT"},
		{math.Nextafter(roundedRoot, math.Inf(1)), "CAPITAL_PRESERVATION"},
	} {
		config.PreserveCapitalMinOverallConfidence = tc.threshold
		got, err := SelectPlaybook(state, config)
		if err != nil || got.PlaybookID != tc.want {
			t.Fatalf("threshold %.17g: got %s, %v; want %s", tc.threshold, got.PlaybookID, err, tc.want)
		}
	}
}

func TestPlaybookInvalidNumbers(t *testing.T) {
	base := PlaybookState{DirectionalState: "UPTREND", DirectionalConfidence: 0.8, VolatilityConfidence: 0.8, PositioningConfidence: 0.8, QualityConfidence: 0.8, QualityState: "TRADABLE"}
	config := DefaultPlaybookConfig()
	cases := []struct {
		name   string
		state  PlaybookState
		config PlaybookConfig
	}{}
	for _, v := range []float64{math.NaN(), math.Inf(1), math.Inf(-1)} {
		s := base
		s.DirectionSignal = v
		cases = append(cases, struct {
			name   string
			state  PlaybookState
			config PlaybookConfig
		}{"state", s, config})
		c := config
		c.BullMaxLeverage = v
		cases = append(cases, struct {
			name   string
			state  PlaybookState
			config PlaybookConfig
		}{"config", base, c})
	}
	s := base
	s.DirectionalConfidence = math.MaxFloat64
	s.VolatilityConfidence = 2
	cases = append(cases, struct {
		name   string
		state  PlaybookState
		config PlaybookConfig
	}{"product overflow", s, config})
	s = base
	s.DirectionalConfidence = -1
	cases = append(cases, struct {
		name   string
		state  PlaybookState
		config PlaybookConfig
	}{"negative product", s, config})
	s = base
	s.QualityState = "DEGRADED"
	s.DirectionSignal = math.NaN()
	cases = append(cases, struct {
		name   string
		state  PlaybookState
		config PlaybookConfig
	}{"invalid despite preservation", s, config})
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			got, err := SelectPlaybook(tc.state, tc.config)
			if err == nil || !reflect.DeepEqual(got, PlaybookSelection{}) {
				t.Fatalf("expected zero result and error, got %+v, %v", got, err)
			}
		})
	}
}

func TestPlaybookExtremeRangeConfidence(t *testing.T) {
	// Zero positioning confidence keeps the product finite; the other extreme
	// inputs exercise the range confidence computation without product overflow.
	s := PlaybookState{DirectionalState: "NEUTRAL", DirectionalConfidence: -math.MaxFloat64, VolatilityConfidence: 1, PositioningConfidence: 0, QualityConfidence: math.MaxFloat64, QualityState: "TRADABLE"}
	config := DefaultPlaybookConfig()
	config.PreserveCapitalMinOverallConfidence = -1
	got, err := SelectPlaybook(s, config)
	if err != nil || got.PlaybookID != "RANGE_LIGHT" || got.PlaybookConfidence != 1 {
		t.Fatalf("unexpected range result: %+v, %v", got, err)
	}
}

func TestPlaybookConfigOverrideKeepsDefaults(t *testing.T) {
	config := DefaultPlaybookConfig()
	config.BullMaxLeverage = 1.1
	s := PlaybookState{DirectionalState: "UPTREND", DirectionalConfidence: 0.8, VolatilityConfidence: 0.8, PositioningConfidence: 0.8, QualityConfidence: 0.8, QualityState: "TRADABLE"}
	got, err := SelectPlaybook(s, config)
	if err != nil {
		t.Fatal(err)
	}
	if got.PlaybookID != "BULL_CONTINUATION" || got.MaxLeverage != 1.1 || got.TrailingStopATRMult != 2.5 || !got.AllowPyramid {
		t.Fatalf("override lost defaults: %+v", got)
	}
}

func TestPlaybookSizingComposition(t *testing.T) {
	s := PlaybookState{Symbol: "SYNTHETIC", Timeframe: "4h", CutoffTimeNS: 123, DirectionalState: "UPTREND", DirectionalConfidence: 0.8, VolatilityState: "NORMAL", VolatilityConfidence: 0.8, PositioningState: "HEALTHY", PositioningConfidence: 0.8, QualityState: "TRADABLE", QualityConfidence: 0.8}
	p, err := SelectPlaybook(s, DefaultPlaybookConfig())
	if err != nil {
		t.Fatal(err)
	}
	size, err := ComputeSizing(SizingState{Symbol: s.Symbol, Timeframe: s.Timeframe, CutoffTimeNS: s.CutoffTimeNS, DirectionalConfidence: s.DirectionalConfidence, VolatilityState: s.VolatilityState, VolatilityConfidence: s.VolatilityConfidence, PositioningConfidence: s.PositioningConfidence, QualityState: s.QualityState, QualityConfidence: s.QualityConfidence}, SizingPlaybook{PlaybookConfidence: p.PlaybookConfidence, MaxLeverage: p.MaxLeverage}, 1, SizingIndicators{RealizedVol: 0.6}, DefaultSizingConfig())
	if err != nil {
		t.Fatal(err)
	}
	if p.PlaybookID != "BULL_CONTINUATION" || size.FinalLeverage <= 0 || size.FinalLeverage > p.MaxLeverage || size.Symbol != p.Symbol {
		t.Fatalf("unexpected composition: %+v %+v", p, size)
	}
}
