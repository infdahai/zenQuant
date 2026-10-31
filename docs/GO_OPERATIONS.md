# Go workbench operations

The default runtime is `bin/zenquant`, built with `make build`. It requires only the binary and `options-ui/dist` at runtime; no Python, Rust or network service is called to evaluate requests. Run from the repository checkout, or pass `--assets` for the static bundle. Supported hosts for this first migration are macOS and Linux.

```sh
npm ci --ignore-scripts
npm ci --ignore-scripts --prefix options-ui
make build
./bin/zenquant serve --port 8092
```

The listener is fixed to 127.0.0.1. The URL is a local development address. There is no public deployment, authentication, tunnel, live trading adapter or automated strategy. POSTs require the matching Origin, Host, JSON content type and X-ZenQuant-Request header. The adapter bounds headers, bodies, connection deadlines and worker count. Static assets are loaded at startup from the built bundle only.

`/` provides conditional option payoff diagnostics; `/paper` provides manual capital allocation and unassessed intent recording. The existing manual workflow restrictions in [the manual guide](MANUAL_PAPER_RUST_GUIDE.md) remain in force. The USD100000 value is a combined declaration, not exchange collateral or cash granted to each product. A recorded intent never implies execution permission.

The fixed `.manual-paper/snapshot.json` is resolved through Git worktree metadata to the common repository root. No automatic migration or dual write to `.simulated-reservations` occurs. Existing missing/corrupt state fails closed. Keep the original request ID after uncertain persistence and retry/read state; do not delete a journal to recover budget. Do not run ordinary write tests against this directory.

For isolated checks use `./bin/zenquant serve --engineering --port 8093`; it creates a fresh temporary journal marked ENGINEERING_TEST_ONLY and removes it on normal shutdown. SIGKILL can leave the temporary directory behind. Browser configuration always selects this mode and refuses reuse of an existing service.

```sh
make check       # Go format, vet, race tests; Svelte checks and unit tests
make browser     # native Go build and existing Playwright workflows
./bin/zenquant evaluate --route /api/curve < request.json
```

The evaluate command reads a bounded versioned request from standard input and prints the same report envelope used by HTTP. `/api/synthetic` is also supported for explicit engineering fixtures. It does not fetch market data or load arbitrary data paths.

Legacy tools remain explicit: `make legacy-rust` runs retained Rust tests and `make legacy-python` runs existing Python gates A–E explicitly, excluding the data-dependent walk-forward research Gate F. Legacy research/collector operation is documented in [OPERATIONS.md](OPERATIONS.md); it is outside the new default service. Migration does not release the research freeze or establish strategy profitability. Existing Rust artifacts and golden fixtures remain historical evidence and are not rewritten to conceal Go regressions.

Rollback is a reviewed code rollback to the previous runtime. Persisted schema/hash compatibility must be checked before switching a writer; never run two independent allocation authorities or reset budget as part of rollback. No production rollout is included in this change.
