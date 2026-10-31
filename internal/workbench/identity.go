package workbench

import (
	"crypto/sha256"
	"embed"
	"fmt"
	"runtime"
	"strings"
	"sync"

	"github.com/infdahai/zenQuant/internal/research"
)

//go:embed *.go
var workbenchSources embed.FS
var identityOnce sync.Once
var identityValue string
var identityError error

// EngineIdentity hashes embedded source and the numeric/toolchain contract.
func EngineIdentity() (string, error) {
	identityOnce.Do(func() {
		researchID, e := research.SourceIdentity()
		if e != nil {
			identityError = e
			return
		}
		entries, e := workbenchSources.ReadDir(".")
		if e != nil {
			identityError = e
			return
		}
		h := sha256.New()
		fmt.Fprintf(h, "diagnostic-v3;binary64;%s;%s;", runtime.Version(), researchID)
		for _, entry := range entries {
			if strings.HasSuffix(entry.Name(), "_test.go") {
				continue
			}
			b, e := workbenchSources.ReadFile(entry.Name())
			if e != nil {
				identityError = e
				return
			}
			fmt.Fprintf(h, "%d:%s:%d:", len(entry.Name()), entry.Name(), len(b))
			h.Write(b)
		}
		identityValue = fmt.Sprintf("diagnostic-v3;numeric=binary64;go=%s;source-sha256=%x", runtime.Version(), h.Sum(nil))
	})
	return identityValue, identityError
}
