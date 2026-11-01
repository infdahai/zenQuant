package cli

import (
	"bytes"
	"errors"
	"flag"
	"fmt"
	"io"
	"path/filepath"
	"strings"
	"time"

	"github.com/infdahai/zenQuant/internal/history"
	"github.com/infdahai/zenQuant/internal/localfile"
)

type historyInputs []string

func (v *historyInputs) String() string { return strings.Join(*v, ", ") }
func (v *historyInputs) Set(s string) error {
	if s == "" {
		return errors.New("input path must not be empty")
	}
	*v = append(*v, s)
	return nil
}

func runHistoryImport(args []string, out, stderr io.Writer) error {
	flags := flag.NewFlagSet("history-import", flag.ContinueOnError)
	flags.SetOutput(stderr)
	var inputs historyInputs
	flags.Var(&inputs, "input", "ordered headerless Binance futures kline CSV or single-member ZIP; repeat for multiple files")
	fundingInput := flags.String("funding-input", "", "optional local Binance fundingRate JSON array")
	symbol := flags.String("symbol", "", "source exchange symbol (BTCUSDT)")
	timeframe := flags.String("timeframe", "", "5m, 1h, 4h or 1d")
	cutoff := flags.String("as-of", "", "explicit RFC3339 observation cutoff")
	if err := flags.Parse(args); err != nil {
		if errors.Is(err, flag.ErrHelp) {
			return nil
		}
		return err
	}
	fundingProvided := false
	flags.Visit(func(f *flag.Flag) {
		if f.Name == "funding-input" {
			fundingProvided = true
		}
	})
	if fundingProvided && *fundingInput == "" {
		return errors.New("funding input path must not be empty")
	}
	if flags.NArg() != 0 || len(inputs) == 0 || *symbol != "BTCUSDT" || *cutoff == "" {
		return errors.New("input, symbol BTCUSDT, timeframe and as-of are required")
	}
	asOf, err := time.Parse(time.RFC3339Nano, *cutoff)
	if err != nil {
		return fmt.Errorf("invalid as-of: %w", err)
	}
	var parts []io.Reader
	total := 0
	for i, input := range inputs {
		raw, err := localfile.Read(input, history.MaxCSV)
		if err != nil {
			return fmt.Errorf("input %d: %w", i+1, err)
		}
		if strings.EqualFold(filepath.Ext(input), ".zip") {
			raw, err = history.ZIPCSV(raw)
			if err != nil {
				return fmt.Errorf("input %d: %w", i+1, err)
			}
		}
		total += len(raw)
		if total > history.MaxCSV {
			return errors.New("total CSV exceeds 4 MiB")
		}
		parts = append(parts, bytes.NewReader(raw))
	}
	var funding []byte
	if *fundingInput != "" {
		funding, err = localfile.Read(*fundingInput, history.MaxCSV)
		if err != nil {
			return fmt.Errorf("funding input: %w", err)
		}
		if funding == nil {
			funding = []byte{}
		}
	}
	b, err := history.NormalizePartsWithFunding(parts, *timeframe, asOf, funding)
	if err != nil {
		return err
	}
	_, err = out.Write(b)
	return err
}
