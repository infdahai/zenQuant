package server

import "embed"

//go:embed researchui/*
var researchUI embed.FS

// ResearchAssets exposes only the embedded, explicitly named research assets.
func ResearchAssets() map[string]Asset {
	paths := map[string]struct{ file, mime string }{"/research": {"researchui/index.html", "text/html; charset=utf-8"}, "/research.css": {"researchui/research.css", "text/css; charset=utf-8"}, "/research.js": {"researchui/research.js", "text/javascript; charset=utf-8"}}
	assets := make(map[string]Asset, len(paths))
	for path, entry := range paths {
		body, err := researchUI.ReadFile(entry.file)
		if err != nil {
			panic(err)
		}
		assets[path] = Asset{entry.mime, body}
	}
	return assets
}
