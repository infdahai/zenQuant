package options

import (
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"testing"
)

func TestArchivedRustReports(t *testing.T) {
	paths, err := filepath.Glob("testdata/*.json")
	if err != nil || len(paths) != 4 {
		t.Fatalf("fixture inventory: %v %v", paths, err)
	}
	for _, path := range paths {
		t.Run(filepath.Base(path), func(t *testing.T) {
			data, err := os.ReadFile(path)
			if err != nil {
				t.Fatal(err)
			}
			var fixture struct {
				Reference string          `json:"reference"`
				Request   json.RawMessage `json:"request"`
				Report    json.RawMessage `json:"report"`
			}
			if err = json.Unmarshal(data, &fixture); err != nil {
				t.Fatal(err)
			}
			if fixture.Reference != "Rust options-workbench at 5fca5d6" {
				t.Fatal("unknown reference")
			}
			result, err := Evaluate("/api/curve", fixture.Request)
			if err != nil {
				t.Fatal(err)
			}
			var envelope struct {
				Report         json.RawMessage `json:"report"`
				Implementation string          `json:"implementation"`
			}
			if err = json.Unmarshal(result, &envelope); err != nil {
				t.Fatal(err)
			}
			var want, got any
			if err = json.Unmarshal(fixture.Report, &want); err != nil {
				t.Fatal(err)
			}
			if err = json.Unmarshal(envelope.Report, &got); err != nil {
				t.Fatal(err)
			}
			if !reflect.DeepEqual(want, got) {
				t.Fatalf("archived Rust report differs\nwant %s\ngot %s", fixture.Report, envelope.Report)
			}
			if envelope.Implementation != "options-payoff-native.go.v1" {
				t.Fatal("missing native provenance")
			}
		})
	}
}
