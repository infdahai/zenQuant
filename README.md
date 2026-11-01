# ZenQuant — trading research workbench

ZenQuant is a BTC-first, regime-aware trading research system. The current Go product is a local options and manual paper workbench with a Svelte UI. It analyzes supplied or synthetic scenarios and records manual declarations and unassessed intents; it does not place orders, generate fills, or authorize live trading.

Go is the intended primary implementation language, with Python capabilities replaced in stages. The retained Python system still owns strategy, historical replay, collection, automated risk and execution, and paper-live workflows. The Go research workspace is diagnostic and does not establish parity with those workflows. Before retiring an active Python trading, risk or replay path, validate event-by-event behavior, point-in-time rules, errors and operational recovery against the Python reference; review callers, cutover and rollback. Existing synthetic fixtures do not establish full parity.

Trading decisions must use confirmed bar closes and emit intent before orders; risk can reduce or rewrite exposure using actual fills. Replay and live paths must preserve point-in-time correctness, realistic execution and auditable decisions. The frozen Quant V2 result blocks promotion and further parameter training: late-dev was negative under an additional 15bp cost on all five seeds, with strict robustness 0/5. See [the live validation handoff](LIVE_VALIDATION_HANDOFF_SPEC.md) and [repository engineering rules](AGENTS.md).

## Local Go product

Requires Go 1.25+, Node 24 and npm on macOS or Linux.

```sh
npm ci --ignore-scripts
npm ci --ignore-scripts --prefix options-ui
make build
./bin/zenquant serve
# http://127.0.0.1:8092/ and /paper
make check
make browser
```

`make check` runs Go formatting, vet, race tests and frontend checks. `make browser` builds and runs browser tests. The Go backend owns domain validation and paper-state authority; the UI collects input and presents results. `serve --engineering` uses an isolated temporary paper journal; the normal entrypoint shares the repository's manual paper authority across worktrees.

## Supplied historical klines

```sh
./bin/zenquant history-import --input /path/to/klines.csv --funding-input /path/to/funding.json --symbol BTCUSDT --timeframe 4h --as-of 2024-02-01T00:00:00Z > /tmp/btc-dataset.json
./bin/zenquant workbench --store /tmp/zenquant-research import < /tmp/btc-dataset.json
```

This offline command accepts regular, headerless 12-column Binance USD-M futures millisecond CSV files for caller-verified BTCUSDT, with timeframes 5m, 1h, 4h or 1d. Input paths cannot contain symlinks or `..` components. Repeat `--input` in chronological order to merge files, for example `--input /path/to/january.csv --input /path/to/february.zip`. For multiple inputs, every candle must be consecutive across and within files; overlaps, duplicates, unordered bars, gaps and empty files fail. A single input retains the existing behavior: internal gaps are reported by workbench quality rather than filled. Completeness remains unknown without a verified requested range, including when there are no gaps.

The command emits workbench `dataset-v1` JSON, maps the instrument to BTC, and converts inclusive millisecond close times to exclusive nanosecond bar ends, as the Python downloader does. It rejects malformed, duplicate, unordered, misaligned and not-yet-closed bars. Each CSV is limited to 4 MiB, and the total uncompressed CSV bytes across all inputs are also limited to 4 MiB. The dataset is limited to 20,000 bars and 1 MiB of serialized JSON including its trailing newline; the 1 MiB limit may be reached first for long histories. Use a new output path: shell redirection can truncate an existing file before validation.

Dataset provenance records the caller-supplied symbol and canonical UTC cutoff. For one input, `source_sha256` remains the SHA-256 of the exact CSV bytes. For multiple inputs, `source_parts_sha256` lists each CSV byte digest in input order and `source_sha256` is SHA-256 of the ASCII bytes `zenquant-ordered-csv-parts-v1` followed by a zero byte and then the concatenated 32-byte binary part digests in order. These values contribute to snapshot identity; the workbench checks the aggregate against the ordered part digests. They identify supplied content, not its authenticity; independently imported dataset JSON contains caller-supplied provenance claims. The cutoff prevents future closes but cannot establish historical arrival time.

The optional `--funding-input` accepts a local Binance `fundingRate` JSON array with BTCUSDT symbols, positive millisecond `fundingTime` values and finite string `fundingRate` values. Times must be strictly increasing, representable in nanoseconds and no later than `--as-of`. Its exact bytes have a separate SHA-256 in provenance and snapshot identity; omit the flag for the original no-funding path. Funding completeness and historical arrival time remain unknown. Go replay applies each newly visible supplied settlement before the execution-bar decision, using the latest available bar price at each settlement. Funding coverage and historical arrival remain unverified; this does not establish Python settlement parity.

The input can also be a `.zip` file containing exactly one regular `.csv` member with a plain filename (no directories). The ZIP and its compressed and uncompressed member are each limited to 4 MiB; corrupt archives are rejected. Provenance hashes the exact uncompressed CSV bytes, not the ZIP container: equivalent CSV and ZIP inputs have the same dataset and snapshot identity, while ZIP metadata and container bytes are not recorded or authenticated. This supplied-file subset does not support download, Parquet output or open interest. No network or Python runtime is used. Synthetic fixtures generated by `python3 scripts/generate_history_ingest_fixture.py` compare Python downloader kline and funding conversion; Go tests exercise CLI import through a workbench diagnostic replay. They do not establish full replay or trading parity.

## Imported research closed loop

`zenquant history-closed-loop` runs an existing imported BTCUSDT 4h snapshot with externally supplied decisions. Supply `--store DIR --snapshot ID --start-ns NS --end-ns NS --decisions FILE --config FILE --source TEXT --availability TEXT --quote-assumption 'BTCUSDT prices treated as USD at 1:1'`. Bounds are inclusive 4h close timestamps in Unix nanoseconds. Decisions must be a JSON array of complete `research.OfflineDecisionInput` objects, including every indicator and state field even when zero. Each must match its candle and declare availability at that close; the caller's declaration is not proof of historical availability.

The config file is a complete JSON object with `TakerFeeRate`, `MaxTradeQtyPerCutoff` (positive), `HistoryLimit`, `IndicatorWindow`, `StrategyConfig` and `RiskConfig`; nested fields match the exported Go structs and must all be present. There are no implicit strategy or risk defaults. Files are limited to 4 MiB, regular files only, without symlink or parent path components. The JSON output identifies the snapshot, input digest, configuration, caller claims, provenance, decisions, simulated reports and risk state. It reports Go build metadata when available; a modified or unidentified source tree has unknown build identity, and even a clean VCS revision does not identify build flags or dependencies. Validation and replay failures produce no JSON result. Use a new output path when redirecting stdout.

This is research only. It uses same-close fills without slippage, fixed strategy equity, a limited risk overlay and no margin check; missing funding is not modeled. It does not prove source authenticity, live suitability or Python parity.

## Go offline replay admission

`RunOfflineReplay` now routes its built-in synchronous mark-price simulator through session admission before accounting or result collection. Policies must supply a nonempty `ClientOrderID` unique across the run, including rejected attempts. The workbench's buy-first/sell-last diagnostic supplies deterministic IDs. Submitted order material is copied, including the optional price pointer; consumers receive report values. Each attempt admits exactly one validated incremental result. Partial results end the attempt; any remaining quantity requires a new ID. The built-in Go simulator currently returns only full fills or rejections. Partial and malformed results are exercised through private test seams.

The session exposes no exchange adapter or external/late report ingestion API. Reentrancy and concurrent submissions are refused. A simulator, validation or consumer failure blocks further submissions; already delivered consumer effects cannot be rolled back. Replay returns no result on failure. `ReplayAccounting` and the ledger remain standalone calculation APIs, not report admission authorities.

Replay is ephemeral by default. Go callers may set `OfflineReplayConfig.SessionPath` to a marker in an existing directory. Before the first submission, the session creates it exclusively, writes and syncs it, then syncs its parent directory. Existing markers, including empty files left by failed writes or crashes, refuse reopening before policy runs. Keep used markers for audit. An explicitly new path starts a fresh simulated account; no positions, cash, orders or decision authority are recovered. The workbench stays ephemeral and does not expose persistent-account controls.

This slice ports Python PR55 session admission, not the strategy/router, bar quantity budgets, live risk chain or complete replay semantics. Existing Go restrictions remain: no reduce-only or limit orders, no slippage enforcement, finite inputs and nonnegative fees, explicit timestamps (including zero). Python legacy behavior and COT policy are unchanged. No Python subprocess is used at runtime.

Focused verification: `go test ./internal/research ./internal/workbench`; full local checks: `make check`. Synthetic reference traces come from Python `SimulationSession` at `e449310`; with Python development dependencies installed, run `python scripts/generate_simulation_session_golden.py --check` to verify or `--write` to regenerate them. Go tests consume the checked-in fixture without Python and cover partials, duplicate identities, malformed reports, consumer failures, reentrancy, marker sync failures and process-crash restart refusal. These fixtures establish the tested admission contract only, not full trading parity or operational recovery.

## Retained Python system

Python requires 3.11 or later. `uv sync --group dev` installs its development dependencies; `make legacy-python` runs harness gates A–E. The full `zenquant-check` also includes data-dependent research checks. Training, tuning, promotion and paper-live remain subject to the [handoff](LIVE_VALIDATION_HANDOFF_SPEC.md), not to the existence of commands or passing unit tests.

Python execution accepts only the built-in simulators. Each order ID identifies one synchronous attempt; its report quantity and fee are incremental for that attempt. A partial result ends the attempt, with no outstanding exchange order; retrying uses a new ID and the remaining filled-quantity budget for that symbol/bar. The event bus rejects external, duplicate and late reports before any risk, strategy, accounting or persistence subscriber sees them. Order and report objects are immutable. Actual inventory uses the shared signed-quantity rule in `zenquant/domain/quantity.py`; order minima never erase actual partial fills.

Both the web live-paper runtime and `scripts/paper_trade.py` use `ZENQUANT_PAPER_SESSION_PATH` (default `python-paper-session.lock`). Select a path in an existing directory. The marker is created exclusively and synced before the first submission; an existing marker, including one left by a crash or failed write, prevents resuming that session. Keep it for audit. Selecting a new path explicitly starts a new simulated account; it does not restore positions, cash, orders or decision authority. Replay/demo sessions are intentionally ephemeral. Exchange submission and recovery of an existing account remain unsupported.

COT position/report dates do not establish publication availability. The Python COT and macro stores therefore reject the current date-only files, just as research admission does. COT-dependent research remains unavailable; optional COT features are omitted and `cot_available` is false. Missing COT does not impose a new portfolio-wide reduce-only rule. Re-enabling ingestion requires a reviewed publication-time contract and corresponding data evidence.

Focused verification: `python -m pytest -q tests/test_execution_session.py tests/test_execution_router.py tests/test_parity.py tests/test_cot_store.py tests/test_cot_consumers.py tests/test_macro_store.py`.

Historical documentation and archived evidence removed during consolidation remain available in [repository history at the pre-cleanup revision](https://github.com/infdahai/zenQuant/tree/3b4a508/docs). Their claims and commands describe that revision and are not current acceptance evidence.
