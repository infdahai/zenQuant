package research

import (
	"crypto/sha256"
	"embed"
	"fmt"
	"strings"
)

//go:embed *.go
var engineSources embed.FS

// SourceIdentity identifies the compiled research package's source inputs.
func SourceIdentity() (string, error) {
	entries, err := engineSources.ReadDir(".")
	if err != nil {
		return "", err
	}
	h := sha256.New()
	for _, e := range entries {
		if strings.HasSuffix(e.Name(), "_test.go") {
			continue
		}
		b, err := engineSources.ReadFile(e.Name())
		if err != nil {
			return "", err
		}
		fmt.Fprintf(h, "%d:%s:%d:", len(e.Name()), e.Name(), len(b))
		h.Write(b)
	}
	return fmt.Sprintf("%x", h.Sum(nil)), nil
}
