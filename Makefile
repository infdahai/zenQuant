.PHONY: build run check test ui browser legacy-rust legacy-python

build: ui
	go build -trimpath -o bin/zenquant ./cmd/zenquant

ui:
	npm --prefix options-ui run check
	npm --prefix options-ui run build

run: build
	./bin/zenquant serve

check:
	@test -z "$$(gofmt -l cmd internal)"
	go vet ./...
	go test -race ./...
	npm --prefix options-ui run check
	npm --prefix options-ui test

test: check

browser: build
	npm run test:options
	npm run test:paper

legacy-rust:
	cd rust && cargo test --workspace --all-features --locked

legacy-python:
	uv run zenquant-check --gate a
	uv run zenquant-check --gate b
	uv run zenquant-check --gate c
	uv run zenquant-check --gate d
	uv run zenquant-check --gate e
