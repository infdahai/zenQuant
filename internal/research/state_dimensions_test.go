package research

import (
	"encoding/json"
	"math"
	"os"
	"reflect"
	"testing"
)

func TestStateDimensionsPythonGolden(t *testing.T) {
	data, err := os.ReadFile("testdata/state_dimensions_golden.json")
	if err != nil {
		t.Fatal(err)
	}
	var cases []struct {
		Name        string               `json:"name"`
		Input       StateDimensionsInput `json:"input"`
		Directional PlaybookState        `json:"directional"`
		Expected    PlaybookState        `json:"expected"`
		Playbook    PlaybookSelection    `json:"playbook"`
	}
	if err := json.Unmarshal(data, &cases); err != nil {
		t.Fatal(err)
	}
	for _, tc := range cases {
		t.Run(tc.Name, func(t *testing.T) {
			state, err := ClassifyStateDimensions(tc.Input, tc.Directional)
			if err != nil {
				t.Fatal(err)
			}
			if !reflect.DeepEqual(state, tc.Expected) {
				t.Fatalf("state got %+v; want %+v", state, tc.Expected)
			}
			playbook, err := SelectPlaybook(state, DefaultPlaybookConfig())
			if err != nil {
				t.Fatal(err)
			}
			if !reflect.DeepEqual(playbook, tc.Playbook) {
				t.Fatalf("playbook got %+v; want %+v", playbook, tc.Playbook)
			}
		})
	}
}

func TestStateDimensionsInvalidInputs(t *testing.T) {
	for _, value := range []float64{math.NaN(), math.Inf(1), math.Inf(-1)} {
		for _, field := range []string{"vol", "vix", "funding", "confidence", "signal"} {
			input := StateDimensionsInput{QualityFlags: []string{"VENUE_DOWN"}}
			state := PlaybookState{DirectionalConfidence: 0.8}
			switch field {
			case "vol":
				input.RealizedVol = value
			case "vix":
				input.VIXZ = value
			case "funding":
				input.FundingOIZ = value
			case "confidence":
				state.DirectionalConfidence = value
			case "signal":
				state.DirectionSignal = value
			}
			got, err := ClassifyStateDimensions(input, state)
			if err == nil || got != (PlaybookState{}) {
				t.Fatalf("%s %v accepted: %+v %v", field, value, got, err)
			}
		}
	}
	for _, value := range []float64{-0.01, 1.01} {
		if _, err := ClassifyStateDimensions(StateDimensionsInput{}, PlaybookState{DirectionalConfidence: value}); err == nil {
			t.Fatalf("confidence %v accepted", value)
		}
	}
}
