# Go workbench performance baseline

This is a small local engineering measurement, not a production capacity claim. Results depend on the machine, filesystem, scheduling, build and background load. The server has two connection workers; a four-client workload therefore includes queueing. Busy responses are counted as errors and excluded from latency percentiles.

## Reproduce

Build the Go binary and the UI using the repository's normal build commands, then run from the checkout:

```sh
python3 scripts/measure_go_workbench.py --binary bin/zenquant --assets options-ui/dist --samples 40 --workers 4
```

The standard-library-only script launches the binary with `serve --engineering`, the built UI assets and an unused loopback port. The port is selected by binding and releasing it before launching the server, so another process could claim it in that short interval. The server creates its engineering journal under a temporary directory; the script removes the directory on exit. Requests are local HTTP with the required same-origin headers. Startup is process launch to the first successful HTTP response, polled approximately every 10 ms, and includes loading the built assets. Idle RSS is one sample immediately after readiness, obtained with `ps -o rss= -p PID`; on macOS and Linux `ps` reports RSS in KiB. It is not peak RSS.

The curve input is the mathematical fixture `internal/options/testdata/call.json`, with grid counts of 3 and 4096. Paper settings and intent use the Rust manual-paper fixtures. The script configures the journal with preview plus confirm, then validates each workload before timing it. The intent fixture has null evidence and no time-limited evidence; it represents a preview, not a confirmed intent. Configuration confirmation includes durable filesystem operations, including file and directory sync; it is setup and its latency is not measured. Paper operations use a nonblocking exclusive journal lock, so concurrent requests may return `Busy`; failures are not retried. The preview and state workloads measure the configured, one-entry journal, not a large history.

Each workload runs 40 serial requests, then 40 requests across four client threads. Wall time includes client scheduling and, for concurrent runs, thread-pool setup; throughput divides attempts or successes by that wall time. Latencies include HTTP connection and response transfer and use nearest-rank p50/p95 of successful requests only. This is a single short run, with no warmup beyond the validation request; percentiles and throughput are descriptive rather than stable estimates.

## Recorded run

2026-10-31, Apple M4 Pro, macOS 27.2 arm64; local `go version go1.27.3 darwin/arm64` (the installed version at measurement time, not independently verified binary build metadata). Binary SHA-256 `b0a50d478d39788b957feaeb7adf4861d3250930d69c5efb5bef476ef1cd7faf`, size 7,771,762 bytes; startup 15.275 ms; idle RSS 12,608 KiB. Curve request sizes were 3,505 and 3,508 bytes; paper state and preview requests were 2 and 578 bytes.

The following is the raw workload portion of the script's JSON output. Times are milliseconds for p50/p95 and seconds for wall time; rates are requests per second.

```json
{
  "curve_3_points": {
    "serial": {"attempts": 40, "successes": 40, "errors": {}, "wall_seconds": 0.027956, "attempts_per_second": 1430.81, "successes_per_second": 1430.81, "p50_ms": 0.642, "p95_ms": 0.845},
    "concurrent": {"attempts": 40, "successes": 40, "errors": {}, "wall_seconds": 0.015496, "attempts_per_second": 2581.26, "successes_per_second": 2581.26, "p50_ms": 1.293, "p95_ms": 1.547}
  },
  "curve_4096_points": {
    "serial": {"attempts": 40, "successes": 40, "errors": {}, "wall_seconds": 1.439213, "attempts_per_second": 27.79, "successes_per_second": 27.79, "p50_ms": 34.29, "p95_ms": 37.975},
    "concurrent": {"attempts": 40, "successes": 40, "errors": {}, "wall_seconds": 0.857263, "attempts_per_second": 46.66, "successes_per_second": 46.66, "p50_ms": 83.359, "p95_ms": 88.127}
  },
  "paper_state": {
    "serial": {"attempts": 40, "successes": 40, "errors": {}, "wall_seconds": 0.018535, "attempts_per_second": 2158.11, "successes_per_second": 2158.11, "p50_ms": 0.317, "p95_ms": 0.433},
    "concurrent": {"attempts": 40, "successes": 27, "errors": {"HTTP 422: Busy": 13}, "wall_seconds": 0.00604, "attempts_per_second": 6622.88, "successes_per_second": 4470.45, "p50_ms": 0.517, "p95_ms": 0.761}
  },
  "paper_preview": {
    "serial": {"attempts": 40, "successes": 40, "errors": {}, "wall_seconds": 0.014219, "attempts_per_second": 2813.08, "successes_per_second": 2813.08, "p50_ms": 0.336, "p95_ms": 0.38},
    "concurrent": {"attempts": 40, "successes": 31, "errors": {"HTTP 422: Busy": 9}, "wall_seconds": 0.006331, "attempts_per_second": 6318.28, "successes_per_second": 4896.67, "p50_ms": 0.539, "p95_ms": 0.921}
  }
}
```
