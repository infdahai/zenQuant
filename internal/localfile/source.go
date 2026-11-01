package localfile

import (
	"crypto/sha256"
	"embed"
	"encoding/hex"
	"strings"
)

//go:embed *.go
var sources embed.FS

// SourceHashes binds admission behavior to the binary's local-file helpers.
// ReadDir returns lexical order; tests are excluded from implementation identity.
func SourceHashes() [][2]string {
	entries, _ := sources.ReadDir(".")
	pairs := make([][2]string, 0, len(entries))
	for _, entry := range entries {
		if strings.HasSuffix(entry.Name(), "_test.go") {
			continue
		}
		data, _ := sources.ReadFile(entry.Name())
		digest := sha256.Sum256(data)
		pairs = append(pairs, [2]string{"internal/localfile/" + entry.Name(), hex.EncodeToString(digest[:])})
	}
	return pairs
}
