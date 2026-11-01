package cli

import (
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"time"

	"github.com/infdahai/zenQuant/internal/history"
	"golang.org/x/sys/unix"
)

func runHistoryImport(args []string, out, stderr io.Writer) error {
	flags := flag.NewFlagSet("history-import", flag.ContinueOnError)
	flags.SetOutput(stderr)
	input := flags.String("input", "", "headerless Binance futures kline CSV")
	symbol := flags.String("symbol", "", "source exchange symbol (BTCUSDT)")
	timeframe := flags.String("timeframe", "", "5m, 1h, 4h or 1d")
	cutoff := flags.String("as-of", "", "explicit RFC3339 observation cutoff")
	if err := flags.Parse(args); err != nil {
		if errors.Is(err, flag.ErrHelp) {
			return nil
		}
		return err
	}
	if flags.NArg() != 0 || *input == "" || *symbol != "BTCUSDT" || *cutoff == "" {
		return errors.New("input, symbol BTCUSDT, timeframe and as-of are required")
	}
	asOf, err := time.Parse(time.RFC3339Nano, *cutoff)
	if err != nil {
		return fmt.Errorf("invalid as-of: %w", err)
	}
	fd, err := unix.Open(*input, unix.O_RDONLY|unix.O_NONBLOCK|unix.O_NOFOLLOW, 0)
	if err != nil {
		return err
	}
	f := os.NewFile(uintptr(fd), *input)
	defer f.Close()
	info, err := f.Stat()
	if err != nil {
		return err
	}
	if !info.Mode().IsRegular() {
		return errors.New("input must be a regular file (not a symlink)")
	}
	b, err := history.Normalize(f, *timeframe, asOf)
	if err != nil {
		return err
	}
	_, err = out.Write(b)
	return err
}
