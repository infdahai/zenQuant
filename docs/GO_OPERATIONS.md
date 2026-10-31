# Go workbench operations

## Updating existing checkouts

Before updating an existing checkout to a revision that stops tracking `.claude/settings.local.json`, back up that file outside the checkout. Git may delete an unchanged tracked copy during the update, and local modifications may block the update. If modifications block it, first verify the backup, then restore the tracked version in the checkout and retry the update. After updating, restore the backup to `.claude/settings.local.json`; the file is ignored at the new revision. Repeat this for each checkout that has settings to keep.

## Running the workbench

The default runtime is `bin/zenquant`, built with `make build`. It requires only the binary and `options-ui/dist` at runtime; no Python, Rust or network service is called to evaluate requests. Run from the repository checkout or any of its subdirectories; the default static bundle comes from that checkout, including linked worktrees. An explicit `--assets` path remains relative to the current directory unless absolute. Supported hosts for this first migration are macOS and Linux.

```sh
npm ci --ignore-scripts
npm ci --ignore-scripts --prefix options-ui
make build
./bin/zenquant serve --port 8092
```

The listener is fixed to 127.0.0.1. The URL is a local development address. There is no public deployment, authentication, tunnel, live trading adapter or automated strategy. POSTs require the matching Origin, Host, JSON content type and X-ZenQuant-Request header. The adapter bounds headers, bodies, connection deadlines and worker count. Static assets are loaded at startup from the built bundle only.

`/` provides conditional option payoff diagnostics; `/paper` provides manual capital allocation and unassessed intent recording. The existing manual workflow restrictions in [the manual guide](MANUAL_PAPER_RUST_GUIDE.md) remain in force. The USD100000 value is a combined declaration, not exchange collateral or cash granted to each product. A recorded intent never implies execution permission.

The fixed `.manual-paper/snapshot.json` is resolved through Git worktree metadata to the common repository root. No automatic migration or dual write to `.simulated-reservations` occurs. First creation prepares and syncs a complete snapshot in a private staging directory before no-replace publication; retries before publication can create it, and retries after publication replay it. Existing missing/corrupt state fails closed. Rust source and its test helper are being removed; the historical cross-language checks cannot establish current mixed-writer safety. Owner/mode requirements and historical older-binary limits are detailed in the manual guide; the service never repairs permissions automatically. Keep the original request ID after uncertain persistence and retry/read state; do not delete a journal to recover budget. Do not run ordinary write tests against this directory.

For isolated checks use `./bin/zenquant serve --engineering --port 8093`; it creates a fresh temporary journal marked ENGINEERING_TEST_ONLY and removes it on normal shutdown. SIGKILL can leave the temporary directory behind. Browser configuration always selects this mode and refuses reuse of an existing service.

```sh
make check       # Go format, vet, race tests; Svelte checks and unit tests
make browser     # native Go build and existing Playwright workflows
./bin/zenquant evaluate --route /api/curve < request.json
```

The evaluate command reads a bounded versioned request from standard input and prints the same report envelope used by HTTP. `/api/synthetic` is also supported for explicit engineering fixtures. It does not fetch market data or load arbitrary data paths.

`make legacy-python` runs existing Python gates A–E explicitly, excluding the data-dependent walk-forward research Gate F. Legacy research/collector operation is documented in [OPERATIONS.md](OPERATIONS.md); it is outside the default service. Rust source, its standalone CLIs and `make legacy-rust` are being removed; the standalone quote/terminal, sealed-panel publication, simulated-intent and cash-reservation workflows have no equivalent Go command. Migration does not release the research freeze or establish strategy profitability. Archived Rust artifacts and fixtures remain historical evidence, not a substitute for current differential or mixed-writer tests.

Rollback is a reviewed code rollback to the previous runtime. Persisted schema/hash compatibility must be checked before switching a writer; never run two independent allocation authorities or reset budget as part of rollback. No production rollout is included in this change.
