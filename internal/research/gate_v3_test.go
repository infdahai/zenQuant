package research

import (
	"encoding/json"
	"math"
	"os"
	"reflect"
	"strings"
	"testing"
)

func TestProfitFactorCompensatedSummation(t *testing.T) {
	episodes := []GateV3Episode{
		episode("BTC", 1, 0, 1e16, true), episode("BTC", 1, 1, 1, true),
		episode("BTC", 1, 2, 1, true), episode("BTC", 1, 3, -10000000000000002, true),
	}
	got, err := EvaluateGateV3(episodes, GateV3Options{SwitchesPerDay: number(0)})
	if err != nil || !got.Passed || got.EpisodeProfitFactor.GrossProfit != 10000000000000002 || got.EpisodeProfitFactor.Value == nil || *got.EpisodeProfitFactor.Value != 1 {
		t.Fatalf("compensated sum changed gate: %+v, %v", got, err)
	}
	// Exercise the separate loss accumulator at the same rounding boundary.
	for i := range episodes {
		episodes[i].NetPnL = -episodes[i].NetPnL
	}
	got, err = EvaluateGateV3(episodes, GateV3Options{SwitchesPerDay: number(0)})
	if err != nil || !got.Passed || got.EpisodeProfitFactor.GrossLoss != 10000000000000002 {
		t.Fatalf("compensated loss changed gate: %+v, %v", got, err)
	}
	// A non-finite correction must not turn an overflowing sum into NaN.
	pf := CalculateEpisodeProfitFactor([]GateV3Episode{episode("BTC", 1, 0, math.MaxFloat64, true), episode("BTC", 1, 1, math.MaxFloat64, true)})
	if !math.IsInf(pf.GrossProfit, 1) {
		t.Fatalf("overflow: %+v", pf)
	}
}

func TestGateV3PythonGolden(t *testing.T) {
	data, err := os.ReadFile("testdata/gate_v3_golden.json")
	if err != nil {
		t.Fatal(err)
	}
	var fixture struct {
		Version int `json:"version"`
		Cases   []struct {
			Name  string `json:"name"`
			Input struct {
				Episodes []GateV3Episode `json:"episodes"`
				Options  struct {
					SwitchesPerDay         *float64           `json:"switches_per_day"`
					ElapsedDays            *float64           `json:"elapsed_days"`
					MinEpisodeProfitFactor *float64           `json:"min_episode_profit_factor"`
					Policy                 *string            `json:"policy"`
					FillProfitFactor       *float64           `json:"fill_profit_factor"`
					ActionProfitFactor     map[string]float64 `json:"action_profit_factor"`
				} `json:"options"`
			} `json:"input"`
			Expected struct {
				Result *GateV3Result `json:"result"`
				Error  *struct {
					Type    string `json:"type"`
					Message string `json:"message"`
				} `json:"error"`
			} `json:"expected"`
		} `json:"cases"`
	}
	if err := json.Unmarshal(data, &fixture); err != nil {
		t.Fatal(err)
	}
	if fixture.Version != 1 || len(fixture.Cases) == 0 {
		t.Fatal("unexpected or empty fixture")
	}
	for _, tc := range fixture.Cases {
		t.Run(tc.Name, func(t *testing.T) {
			o := tc.Input.Options
			got, err := EvaluateGateV3(tc.Input.Episodes, GateV3Options{
				SwitchesPerDay: o.SwitchesPerDay, ElapsedDays: o.ElapsedDays,
				MinEpisodeProfitFactor: o.MinEpisodeProfitFactor, Policy: o.Policy,
				FillProfitFactor: o.FillProfitFactor, ActionProfitFactor: o.ActionProfitFactor,
			})
			if tc.Expected.Error != nil {
				if tc.Expected.Result != nil || tc.Expected.Error.Type != "ValueError" || err == nil || err.Error() != tc.Expected.Error.Message {
					t.Fatalf("got error %v, want %+v", err, tc.Expected.Error)
				}
				return
			}
			if err != nil || tc.Expected.Result == nil {
				t.Fatalf("got error %v, expected result %v", err, tc.Expected.Result)
			}
			if !reflect.DeepEqual(got, *tc.Expected.Result) {
				t.Fatalf("got %+v, want %+v", got, *tc.Expected.Result)
			}
		})
	}
}

func number(v float64) *float64 { return &v }
func episode(symbol string, side int, time int64, pnl float64, closed bool) GateV3Episode {
	return GateV3Episode{Symbol: symbol, Side: side, OpenedAtNS: time, NetPnL: pnl, Closed: closed}
}

func TestEpisodeProfitFactor(t *testing.T) {
	cases := []struct {
		name         string
		episodes     []GateV3Episode
		status       ProfitFactorStatus
		value        *float64
		count        int
		profit, loss float64
	}{
		{"empty", nil, ProfitFactorInsufficientSample, nil, 0, 0, 0},
		{"open ignored", []GateV3Episode{episode("BTC", 1, 0, 100, false)}, ProfitFactorInsufficientSample, nil, 0, 0, 0},
		{"flat", []GateV3Episode{episode("BTC", 1, 0, 0, true)}, ProfitFactorDefined, number(0), 1, 0, 0},
		{"loss only", []GateV3Episode{episode("BTC", 1, 0, -5, true)}, ProfitFactorDefined, number(0), 1, 0, 5},
		{"no loss", []GateV3Episode{episode("BTC", 1, 0, 10, true), episode("BTC", 1, 1, 0, true)}, ProfitFactorNoLossEpisodes, nil, 2, 10, 0},
		{"mixed", []GateV3Episode{episode("BTC", 1, 0, 10, true), episode("BTC", -1, 1, -4, true), episode("BTC", 1, 2, 100, false)}, ProfitFactorDefined, number(2.5), 2, 10, 4},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			got := CalculateEpisodeProfitFactor(tc.episodes)
			if got.Status != tc.status || got.ClosedEpisodeCount != tc.count || got.GrossProfit != tc.profit || got.GrossLoss != tc.loss || !reflect.DeepEqual(got.Value, tc.value) {
				t.Fatalf("unexpected result: %+v", got)
			}
		})
	}
}

func TestCadenceBoundaries(t *testing.T) {
	cases := []struct {
		n        float64
		severity GateSeverity
		reason   string
	}{
		{0, GateSeverityNone, ""}, {1, GateSeverityNone, ""}, {math.Nextafter(1, 2), GateSeveritySoft, "position_switch_cadence_above_preferred"},
		{2, GateSeveritySoft, "position_switch_cadence_above_preferred"}, {math.Nextafter(2, 3), GateSeverityHard, "position_switch_cadence_above_normal_limit"},
		{5, GateSeverityHard, "position_switch_cadence_above_normal_limit"}, {math.Nextafter(5, 6), GateSeverityHard, "position_switch_cadence_above_absolute_limit"},
	}
	for _, tc := range cases {
		for _, policy := range []string{"normal", "research", "emergency"} {
			got, err := EvaluateCadence(tc.n, policy)
			if err != nil || got.Severity != tc.severity || got.Policy != policy || (tc.reason == "" && got.Reason != nil) || (tc.reason != "" && (got.Reason == nil || *got.Reason != tc.reason)) {
				t.Fatalf("%v %s: %+v %v", tc.n, policy, got, err)
			}
		}
	}
}

func TestPositionSwitchCountOrderAndSymbols(t *testing.T) {
	episodes := []GateV3Episode{
		episode("BTC", 1, 10, 0, true), episode("ETH", -1, 0, 0, true), episode("BTC", -1, 10, 0, false),
		episode("BTC", 1, 10, 0, true), episode("ETH", -1, 11, 0, true), episode("ETH", 1, 12, 0, true),
	}
	count, err := PositionSwitchCount(episodes)
	if err != nil || count != 3 {
		t.Fatalf("count=%d err=%v", count, err)
	}
	if episodes[0].Symbol != "BTC" {
		t.Fatal("input mutated")
	}
}

func TestGateV3VerdictsAndDiagnostics(t *testing.T) {
	tests := []struct {
		name       string
		episodes   []GateV3Episode
		options    GateV3Options
		hard, soft []string
	}{
		{"empty", nil, GateV3Options{SwitchesPerDay: number(0)}, []string{"episode_profit_factor_insufficient_sample"}, nil},
		{"one winner", []GateV3Episode{episode("BTC", 1, 0, 5, true)}, GateV3Options{SwitchesPerDay: number(0)}, []string{"episode_profit_factor_no_loss_sample_too_small"}, nil},
		{"winner and flat", []GateV3Episode{episode("BTC", 1, 0, 5, true), episode("BTC", 1, 1, 0, true)}, GateV3Options{SwitchesPerDay: number(0)}, nil, nil},
		{"zero minimum", []GateV3Episode{episode("BTC", 1, 0, -5, true)}, GateV3Options{SwitchesPerDay: number(0), MinEpisodeProfitFactor: number(0)}, nil, nil},
		{"combined failures", []GateV3Episode{episode("BTC", 1, 0, 4, true), episode("BTC", -1, 1, -5, true)}, GateV3Options{SwitchesPerDay: number(6)}, []string{"episode_profit_factor_below_minimum", "position_switch_cadence_above_absolute_limit"}, nil},
		{"soft", []GateV3Episode{episode("BTC", 1, 0, 10, true), episode("BTC", -1, 1, -5, true)}, GateV3Options{ElapsedDays: number(0.5), FillProfitFactor: number(0.1), ActionProfitFactor: map[string]float64{"reduce": 0.2}}, nil, []string{"position_switch_cadence_above_preferred"}},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			got, err := EvaluateGateV3(tc.episodes, tc.options)
			if err != nil {
				t.Fatal(err)
			}
			if !reflect.DeepEqual(got.HardFailures, append([]string{}, tc.hard...)) || !reflect.DeepEqual(got.SoftFlags, append([]string{}, tc.soft...)) || got.Passed != (len(tc.hard) == 0) || got.HardFailed == got.Passed {
				t.Fatalf("unexpected result: %+v", got)
			}
			if tc.name == "soft" {
				if got.Diagnostics.PositionSwitchCount != 1 || got.Cadence.SwitchesPerDay != 2 || *got.Diagnostics.FillProfitFactor != 0.1 {
					t.Fatal(got)
				}
				tc.options.ActionProfitFactor["reduce"] = 99
				if got.Diagnostics.ActionProfitFactor["reduce"] != 0.2 {
					t.Fatal("diagnostics alias input")
				}
			}
			data, err := json.Marshal(got)
			if err != nil {
				t.Fatal(err)
			}
			if strings.Contains(string(data), `"hard_failures":null`) || strings.Contains(string(data), `"action_profit_factor":null`) {
				t.Fatal(string(data))
			}
		})
	}
}

func TestGateV3ErrorsAndPrecedence(t *testing.T) {
	invalid := []GateV3Episode{episode("BTC", 0, 0, 0, false)}
	tests := []struct {
		episodes []GateV3Episode
		options  GateV3Options
		message  string
	}{
		{invalid, GateV3Options{MinEpisodeProfitFactor: number(-1)}, "min_episode_profit_factor cannot be negative"},
		{invalid, GateV3Options{}, "episode side must be -1 or 1"},
		{nil, GateV3Options{}, "positive elapsed_days is required when switches_per_day is omitted"},
		{nil, GateV3Options{ElapsedDays: number(0)}, "positive elapsed_days is required when switches_per_day is omitted"},
		{nil, GateV3Options{SwitchesPerDay: number(-1)}, "switches_per_day cannot be negative"},
	}
	for _, tc := range tests {
		_, err := EvaluateGateV3(tc.episodes, tc.options)
		if err == nil || err.Error() != tc.message {
			t.Fatalf("got %v, want %s", err, tc.message)
		}
	}
}
