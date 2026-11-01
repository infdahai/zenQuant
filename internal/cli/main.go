// Package cli composes the native local workbench. No legacy process is invoked.
package cli

import (
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"net"
	"net/http"
	"os"
	"os/signal"
	"path/filepath"
	"strconv"
	"syscall"
	"time"

	"github.com/infdahai/zenQuant/internal/cot"
	"github.com/infdahai/zenQuant/internal/deribit"
	"github.com/infdahai/zenQuant/internal/localfile"
	"github.com/infdahai/zenQuant/internal/options"
	"github.com/infdahai/zenQuant/internal/paper"
	"github.com/infdahai/zenQuant/internal/research"
	"github.com/infdahai/zenQuant/internal/server"
	"github.com/infdahai/zenQuant/internal/workbench"
)

func Run(args []string, out, stderr io.Writer) error {
	if len(args) == 0 || args[0] == "help" || args[0] == "--help" {
		fmt.Fprintln(out, "zenquant serve [--port 8092] [--assets options-ui/dist] [--engineering] [--research-store DIR]\nzenquant workbench --store DIR import|list|snapshot|submit|jobs|cancel|result|compare < request.json\nzenquant evaluate --route /api/curve < request.json\nzenquant vix-snapshot --request FILE --manifest FILE --evidence DIR\nzenquant research-splits\nzenquant download-cot [--years 5] [--output-root DIR] [--base-url URL] [--market NAME]\nzenquant download-deribit-options [--currency BTC] [--output-root DIR] [--endpoint URL]\nzenquant review-loop-stability [--results-root DIR] [--output-dir DIR]\nzenquant review-stage2-benchmark --failed-status FILE --baseline-status FILE --rerun-status FILE --output-dir DIR [--rerun-report FILE]\nzenquant gate-v3 < request.json\nNative Go local diagnostics and manual record-only paper workbench.")
		return nil
	}
	switch args[0] {
	case "download-cot":
		flags := flag.NewFlagSet("download-cot", flag.ContinueOnError)
		flags.SetOutput(stderr)
		years := flags.Int("years", 5, "number of years including the current year")
		root := flags.String("output-root", "data/raw/cftc_go", "isolated output root")
		base := flags.String("base-url", cot.DefaultBaseURL, "CFTC report base URL")
		market := flags.String("market", "", "exact Bitcoin market name (case insensitive)")
		if err := flags.Parse(args[1:]); err != nil {
			if errors.Is(err, flag.ErrHelp) {
				return nil
			}
			return err
		}
		if flags.NArg() != 0 || *years < 1 || *years > 100 {
			return errors.New("invalid arguments")
		}
		year := time.Now().UTC().Year()
		rows, err := cot.Download(&http.Client{Timeout: 60 * time.Second}, *base, *market, year-*years+1, year)
		if err != nil {
			return err
		}
		path, err := cot.Save(rows, *root)
		if err != nil {
			return err
		}
		if _, err := fmt.Fprintf(out, "rows=%d\nparquet=%s\n", len(rows), path); err != nil {
			return fmt.Errorf("dataset published at %s; stdout failed: %w", path, err)
		}
		return nil
	case "download-deribit-options":
		flags := flag.NewFlagSet("download-deribit-options", flag.ContinueOnError)
		flags.SetOutput(stderr)
		currency := flags.String("currency", "BTC", "underlying currency")
		root := flags.String("output-root", "data/raw/deribit_options_go", "snapshot output root (separate from Python collector by default)")
		endpoint := flags.String("endpoint", deribit.DefaultEndpoint, "Deribit public API base URL")
		if err := flags.Parse(args[1:]); err != nil {
			if errors.Is(err, flag.ErrHelp) {
				return nil
			}
			return err
		}
		if flags.NArg() != 0 {
			return errors.New("unexpected argument")
		}
		client := &http.Client{Timeout: 15 * time.Second}
		rows, err := deribit.Download(client, *endpoint, *currency, time.Now)
		if err != nil {
			return err
		}
		jp, pp, err := deribit.Save(rows, *root)
		if err != nil {
			return err
		}
		if _, err := fmt.Fprintf(out, "Deribit options chain snapshot complete\nrows=%d\njson=%s\nparquet=%s\n", len(rows), jp, pp); err != nil {
			return fmt.Errorf("snapshot published at %s and %s; stdout failed: %w", jp, pp, err)
		}
		return nil
	case "review-loop-stability":
		flags := flag.NewFlagSet("review-loop-stability", flag.ContinueOnError)
		flags.SetOutput(stderr)
		root := flags.String("results-root", "results", "campaign results directory")
		output := flags.String("output-dir", "results", "review output directory")
		if err := flags.Parse(args[1:]); err != nil {
			if errors.Is(err, flag.ErrHelp) {
				return nil
			}
			return err
		}
		if flags.NArg() != 0 {
			return errors.New("unexpected argument")
		}
		review, err := research.ReviewLoopStability(*root, time.Now())
		if err != nil {
			return err
		}
		written, err := research.WriteLoopReview(review, *output)
		if err != nil {
			return err
		}
		enc := json.NewEncoder(out)
		enc.SetIndent("", "  ")
		if err := enc.Encode(struct {
			Review    map[string]string `json:"review"`
			LoopCount int               `json:"loop_count"`
		}{written, review.LoopCount}); err != nil {
			return fmt.Errorf("report published at %s; stdout failed: %w", filepath.Dir(written["json_path"]), err)
		}
		return nil
	case "review-stage2-benchmark":
		return runBenchmarkReview(args[1:], out, stderr)
	case "gate-v3":
		if len(args) != 1 {
			return errors.New("unexpected argument")
		}
		return runGateV3(os.Stdin, out)
	case "research-splits":
		if len(args) != 1 {
			return errors.New("unexpected argument")
		}
		return json.NewEncoder(out).Encode(research.SplitSummaryJSON(research.DefaultBTCSplitSpec()))
	case "evaluate":
		flags := flag.NewFlagSet("evaluate", flag.ContinueOnError)
		flags.SetOutput(stderr)
		route := flags.String("route", "/api/curve", "/api/curve or /api/synthetic")
		if err := flags.Parse(args[1:]); err != nil {
			return err
		}
		if flags.NArg() != 0 {
			return errors.New("unexpected argument")
		}
		b, err := io.ReadAll(io.LimitReader(os.Stdin, server.BodyLimit+1))
		if err != nil {
			return err
		}
		if len(b) > server.BodyLimit {
			return errors.New("request exceeds limit")
		}
		b, err = options.Evaluate(*route, b)
		if err != nil {
			return err
		}
		_, err = fmt.Fprintln(out, string(b))
		return err
	case "vix-snapshot":
		flags := flag.NewFlagSet("vix-snapshot", flag.ContinueOnError)
		flags.SetOutput(stderr)
		request := flags.String("request", "", "observation request JSON")
		manifest := flags.String("manifest", "", "snapshot manifest JSON")
		evidence := flags.String("evidence", "", "local evidence directory")
		if err := flags.Parse(args[1:]); err != nil {
			return err
		}
		if flags.NArg() != 0 || *request == "" || *manifest == "" || *evidence == "" {
			return errors.New("request, manifest and evidence are required")
		}
		r, e := localfile.Read(*request, server.BodyLimit)
		if e != nil {
			return e
		}
		m, e := localfile.Read(*manifest, server.BodyLimit)
		if e != nil {
			return e
		}
		b, e := options.AdmitVixSnapshot(r, m, *evidence)
		if e != nil {
			return e
		}
		_, e = fmt.Fprintln(out, string(b))
		return e
	case "workbench":
		return runWorkbench(args[1:], out, stderr)
	case "serve":
		return serve(args[1:], out, stderr)
	default:
		return errors.New("unknown command; use zenquant help")
	}
}
func serve(args []string, out, stderr io.Writer) error {
	flags := flag.NewFlagSet("serve", flag.ContinueOnError)
	flags.SetOutput(stderr)
	port := flags.Int("port", 8092, "loopback port")
	assetsPath := flags.String("assets", "options-ui/dist", "built static bundle")
	engineering := flags.Bool("engineering", false, "isolated temporary paper journal; ENGINEERING_TEST_ONLY")
	researchPath := flags.String("research-store", "", "local research store directory")
	if err := flags.Parse(args); err != nil {
		return err
	}
	if flags.NArg() != 0 || *port < 1 || *port > 65535 {
		return errors.New("invalid serve arguments")
	}
	assetsSpecified := false
	flags.Visit(func(f *flag.Flag) {
		if f.Name == "assets" {
			assetsSpecified = true
		}
	})
	if !assetsSpecified {
		cwd, err := os.Getwd()
		if err != nil {
			return err
		}
		root, err := CheckoutRoot(cwd)
		if err != nil {
			return err
		}
		*assetsPath = filepath.Join(root, "options-ui", "dist")
	}
	assets, err := server.LoadAssets(*assetsPath)
	if err != nil {
		return err
	}
	var store *paper.Store
	if *engineering {
		dir, err := os.MkdirTemp("", "zenquant-engineering-")
		if err != nil {
			return err
		}
		defer os.RemoveAll(dir)
		dir, err = filepath.EvalSymlinks(dir)
		if err != nil {
			return err
		}
		store = paper.NewEngineeringStore(filepath.Join(dir, "journal"))
	} else {
		cwd, err := os.Getwd()
		if err != nil {
			return err
		}
		root, err := RepositoryRoot(cwd)
		if err != nil {
			return err
		}
		store = paper.NewStore(filepath.Join(root, ".manual-paper"))
	}
	var researchAPI server.Evaluate
	if *researchPath != "" {
		researchStore, err := workbench.Open(*researchPath)
		if err != nil {
			return err
		}
		worker, err := workbench.StartJobs(researchStore)
		if err != nil {
			return err
		}
		defer worker.Close()
		researchAPI = researchStore.API
		for path, asset := range server.ResearchAssets() {
			assets[path] = asset
		}
	}
	listener, err := net.Listen("tcp4", "127.0.0.1:"+strconv.Itoa(*port))
	if err != nil {
		return err
	}
	defer listener.Close()
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	done := make(chan struct{})
	defer close(done)
	go func() {
		select {
		case <-ctx.Done():
			listener.Close()
		case <-done:
		}
	}()
	fmt.Fprintf(out, "ZenQuant Go local workbench: http://%s/ (record only; no trading)\n", listener.Addr())
	if researchAPI != nil {
		fmt.Fprintf(out, "Research workspace: http://%s/research\n", listener.Addr())
	}
	s := server.Server{Assets: assets, Options: options.Evaluate, Paper: store.API, Research: researchAPI}
	return s.Serve(listener)
}
