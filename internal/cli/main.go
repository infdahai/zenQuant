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
	"os"
	"os/signal"
	"path/filepath"
	"strconv"
	"syscall"

	"github.com/infdahai/zenQuant/internal/options"
	"github.com/infdahai/zenQuant/internal/paper"
	"github.com/infdahai/zenQuant/internal/research"
	"github.com/infdahai/zenQuant/internal/server"
	"github.com/infdahai/zenQuant/internal/workbench"
)

func Run(args []string, out, stderr io.Writer) error {
	if len(args) == 0 || args[0] == "help" || args[0] == "--help" {
		fmt.Fprintln(out, "zenquant serve [--port 8092] [--assets options-ui/dist] [--engineering] [--research-store DIR]\nzenquant workbench --store DIR import|list|snapshot|run|result|compare < request.json\nzenquant evaluate --route /api/curve < request.json\nzenquant research-splits\nzenquant gate-v3 < request.json\nNative Go local diagnostics and manual record-only paper workbench.")
		return nil
	}
	switch args[0] {
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
