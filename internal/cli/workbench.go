package cli

import (
	"errors"
	"flag"
	"fmt"
	"io"
	"os"

	"github.com/infdahai/zenQuant/internal/workbench"
)

// workbench accepts the same JSON request bodies as the browser API.
func runWorkbench(args []string, out, stderr io.Writer) error {
	flags := flag.NewFlagSet("workbench", flag.ContinueOnError)
	flags.SetOutput(stderr)
	storePath := flags.String("store", "", "research store directory")
	if err := flags.Parse(args); err != nil {
		return err
	}
	if *storePath == "" || flags.NArg() != 1 {
		return errors.New("usage: zenquant workbench --store DIR import|list|snapshot|run|result|compare < request.json")
	}
	action := flags.Arg(0)
	switch action {
	case "import", "list", "snapshot", "run", "result", "compare":
	default:
		return errors.New("unknown workbench action")
	}
	b, err := io.ReadAll(io.LimitReader(os.Stdin, workbench.MaxInput+1))
	if err != nil {
		return err
	}
	if len(b) > workbench.MaxInput {
		return errors.New("request too large")
	}
	store, err := workbench.Open(*storePath)
	if err != nil {
		return err
	}
	result, err := store.API("/api/research/"+action, b)
	if err != nil {
		return err
	}
	_, err = fmt.Fprintln(out, string(result))
	return err
}
