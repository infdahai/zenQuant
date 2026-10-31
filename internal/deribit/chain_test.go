package deribit

import (
	"os"
	"path/filepath"
	"testing"
)

func TestStagedParquetIsNotDiscoverable(t *testing.T) {
	matches, err := filepath.Match("*.parquet", stagedParquetName)
	if err != nil || matches {
		t.Fatalf("staging file matches parquet discovery: %v, %v", matches, err)
	}
}

func TestSaveCollisionAndRollback(t *testing.T) {
	root := t.TempDir()
	row := Row{SnapshotTS: 1700000000000000000, Currency: "BTC"}
	dir := filepath.Join(root, "BTC", "2023-11")
	if err := os.MkdirAll(dir, 0755); err != nil {
		t.Fatal(err)
	}
	jp := filepath.Join(dir, "options_chain_20231114T221320Z.json")
	pp := filepath.Join(dir, "options_chain_20231114T221320Z.parquet")
	if err := os.WriteFile(pp, []byte("existing"), 0644); err != nil {
		t.Fatal(err)
	}
	if _, _, err := Save([]Row{row}, root); err == nil {
		t.Fatal("expected collision")
	}
	if _, err := os.Stat(jp); !os.IsNotExist(err) {
		t.Fatalf("JSON was published: %v", err)
	}
	data, err := os.ReadFile(pp)
	if err != nil || string(data) != "existing" {
		t.Fatalf("existing parquet changed: %q %v", data, err)
	}
	if err := os.Remove(pp); err != nil {
		t.Fatal(err)
	}
	if _, _, err := Save([]Row{row}, root); err != nil {
		t.Fatal(err)
	}
	if _, _, err := Save([]Row{row}, root); err == nil {
		t.Fatal("expected JSON collision")
	}
}
