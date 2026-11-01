package cli

import (
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"os"

	"github.com/infdahai/zenQuant/internal/history"
	"github.com/infdahai/zenQuant/internal/workbench"
)

func runHistoryCandleObservations(args []string, out, stderr io.Writer) error {
	flags := flag.NewFlagSet("history-candle-observations", flag.ContinueOnError)
	flags.SetOutput(stderr)
	storePath := flags.String("store", "", "existing workbench store directory")
	snapshot := flags.String("snapshot", "", "imported snapshot ID")
	start := flags.Int64("start-ns", 0, "inclusive 4h close in Unix nanoseconds")
	end := flags.Int64("end-ns", 0, "inclusive 4h close in Unix nanoseconds")
	if err := flags.Parse(args); err != nil {
		if errors.Is(err, flag.ErrHelp) {
			return nil
		}
		return err
	}
	if flags.NArg() != 0 || *storePath == "" || *snapshot == "" {
		return errors.New("store and snapshot required; positional arguments are not accepted")
	}
	info, err := os.Stat(*storePath)
	if err != nil {
		return fmt.Errorf("store: %w", err)
	}
	if !info.IsDir() {
		return errors.New("store must be an existing directory")
	}
	store, err := workbench.Open(*storePath)
	if err != nil {
		return err
	}
	result, err := history.ObserveImportedCandles(store, *snapshot, *start, *end)
	if err != nil {
		return err
	}
	return json.NewEncoder(out).Encode(result)
}
