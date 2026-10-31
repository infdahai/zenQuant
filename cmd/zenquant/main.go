package main

import (
	"fmt"
	"os"

	"github.com/infdahai/zenQuant/internal/cli"
)

func main() {
	if err := cli.Run(os.Args[1:], os.Stdout, os.Stderr); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
