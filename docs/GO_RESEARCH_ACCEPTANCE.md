# Research workspace acceptance sample

Measurements on the local macOS host, 2026-11-02, using a Go binary built from the working tree, an isolated engineering server and a synthetic two-candle BTC fixture. No production data or Python research was used. These small samples are not capacity estimates.

- WebKit 26.0 (Playwright 1.58): import, quality, submit, completion, page reload, reopen and compare passed on the isolated server.
- Four concurrent clients, 50 batches (200 POST list requests): p50 0.634 ms, p95 1.617 ms, p99 8.945 ms; 200 success, zero errors. Timing includes local HTTP and response parsing up to response headers.
- Twenty sequential fixed two-candle jobs, polling every 20 ms: 20 succeeded, zero failures, 4.718 s elapsed, 4.24 jobs/s. This measures queue polling and fsync overhead as well as replay; it is not a replay kernel throughput benchmark.
- One idle process sample after testing: RSS 19,904 KiB, CPU 0.6%; binary size 8,404,098 bytes. No peak RSS, startup distribution, CPU profile, allocation, realistic dataset throughput or WebKit process-tree measurements were collected.

Commands: `go build -o /tmp/zenquant-acceptance-latest-20261102 ./cmd/zenquant`; start `serve --engineering --research-store <isolated-dir> --assets options-ui/dist` on loopback; issue 50 four-request batches of `POST /api/research/list` with `{"kind":"snapshot"}`; submit and poll 20 jobs using the two-candle fixture. Browser automation used the repository's pinned Playwright installation with a fresh WebKit context.
