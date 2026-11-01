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

## Retained Python system

Python requires 3.11 or later. `uv sync --group dev` installs its development dependencies; `make legacy-python` runs harness gates A–E. The full `zenquant-check` also includes data-dependent research checks. Training, tuning, promotion and paper-live remain subject to the [handoff](LIVE_VALIDATION_HANDOFF_SPEC.md), not to the existence of commands or passing unit tests.

Python execution accepts only the built-in simulators. Each order ID identifies one synchronous attempt; its report quantity and fee are incremental for that attempt. A partial result ends the attempt, with no outstanding exchange order; retrying uses a new ID and the remaining filled-quantity budget for that symbol/bar. The event bus rejects external, duplicate and late reports before any risk, strategy, accounting or persistence subscriber sees them. Order and report objects are immutable. Actual inventory uses the shared signed-quantity rule in `zenquant/domain/quantity.py`; order minima never erase actual partial fills.

Both the web live-paper runtime and `scripts/paper_trade.py` use `ZENQUANT_PAPER_SESSION_PATH` (default `python-paper-session.lock`). Select a path in an existing directory. The marker is created exclusively and synced before the first submission; an existing marker, including one left by a crash or failed write, prevents resuming that session. Keep it for audit. Selecting a new path explicitly starts a new simulated account; it does not restore positions, cash, orders or decision authority. Replay/demo sessions are intentionally ephemeral. Exchange submission and recovery of an existing account remain unsupported.

COT position/report dates do not establish publication availability. The Python COT and macro stores therefore reject the current date-only files, just as research admission does. COT-dependent research remains unavailable; optional COT features are omitted and `cot_available` is false. Missing COT does not impose a new portfolio-wide reduce-only rule. Re-enabling ingestion requires a reviewed publication-time contract and corresponding data evidence.

Focused verification: `python -m pytest -q tests/test_execution_session.py tests/test_execution_router.py tests/test_parity.py tests/test_cot_store.py tests/test_cot_consumers.py tests/test_macro_store.py`.

Historical documentation and archived evidence removed during consolidation remain available in [repository history at the pre-cleanup revision](https://github.com/infdahai/zenQuant/tree/3b4a508/docs). Their claims and commands describe that revision and are not current acceptance evidence.
