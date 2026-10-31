// Package cli composes the native local workbench. No legacy process is invoked.
package cli

import (
	"context"
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
	"github.com/infdahai/zenQuant/internal/server"
)

func Run(args []string, out, stderr io.Writer) error {
	if len(args) == 0 || args[0] == "help" || args[0] == "--help" {
		fmt.Fprintln(out, "zenquant serve [--port 8092] [--assets options-ui/dist] [--engineering]\nzenquant evaluate --route /api/curve < request.json\nNative Go local diagnostics and manual record-only paper workbench.")
		return nil
	}
	switch args[0] {
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
	s := server.Server{Assets: assets, Options: options.Evaluate, Paper: store.API}
	return s.Serve(listener)
}
