# Go migration validation

Recorded 2026-10-31 in branch `refactor/go-mainline-20261031`, based on fetched `personal/main` `5fca5d69222567dd0108deb3bbd54177d61de1a7`. The results below are historical observations of those revisions, not a new run against the current worktree. No production data, deployment, real orders or strategy promotion was part of that change.

Rust source, its helper and its dedicated CI are being removed. The current Go CI configuration does not build the Rust helper or run live Go/Rust differential and cross-language writer tests. Archived Rust-generated fixtures still cover specific recorded examples, but cannot establish full parity or current mixed-writer safety. The historical Linux results below remain evidence of the revisions and cases actually tested.

## Executed checks

| Check | Observed outcome |
| --- | --- |
| `make check` | PASS: gofmt, `go vet ./...`, `go test -race ./...`, Svelte check and 2 frontend unit tests |
| Go build | PASS: native CLI/server binary |
| `GOOS=linux go vet ./...`, Linux cross-build | PASS; compilation is not Linux runtime evidence |
| `env PATH=/nonexistent ./bin/zenquant help` | PASS; binary does not need external executables |
| Existing options Playwright workflow | PASS: 3/3, default/edited curves, missing evidence, request recovery, mobile layout, 4096 points and rapid input changes |
| Existing manual paper Playwright workflow | PASS: 1/1, configuration, over-allocation, unconfirmed conversion, preview binding, replay, withdrawal, stale preview and mobile option intent |
| Native paper tests | PASS: Rust-generated journal byte/hash parity, reopening, strict JSON, exact bounds, corrupt/rehashed history, concurrent confirmation, write failure injection and child-process termination recovery |
| Native HTTP tests | PASS: actual TCP Host/Origin/duplicate headers/chunked/oversized/guard checks, static symlink rejection and worker shutdown |
| Consolidation tests | PASS: 8 safety contracts; 382 archived payload hashes/lengths/inventory verified |
| Python retained harness | Gates 0/A/B/C/D/E PASS. Gate E: 1853 passed, 11 skipped, 34 subtests passed. Overall command failed at Gate F because no historical Parquet data exists in the isolated worktree |
| Rust retained workspace tests | Core suites passed; workbench library stopped with 7 existing `UnsupportedPlatform` paper-store failures on macOS. The Rust store explicitly requires Linux; the remaining workspace tests were not reached by this command |

Initial browser launch failed because the pinned Chromium executable was missing. Installed the matching Playwright browser and reran. Initial manual-paper browser run found macOS temporary-directory symlink admission failure; engineering-mode temporary roots are now resolved before store construction, preserving strict production path validation. The subsequent full paper workflow passed.

The old `zenquant-check` command implicitly invokes walk-forward research Gate F. It immediately failed before producing a result due to absent data. No data was copied or research rerun. `make legacy-python` now names gates A–E explicitly to avoid launching that research path.

## Independent review

A separate read-only review found two confirmed options Unicode compatibility defects: U+2028/U+2029 canonical escaping differed from Rust, and lone UTF-16 surrogate escapes were silently replaced by Go JSON. Both were fixed with strict decoding and canonical-byte regression coverage. The independent reviewer reran the original reproductions against both Go and Rust: reports/hashes match, malformed surrogates return INVALID_JSON, valid pairs and literal backslash sequences remain accepted. Four archived Rust-produced request/report fixtures now run in ordinary Go tests. The live differential also passed synthetic input plus 14 variants, including 4096 points and Unicode specification binding.

## Remote CI follow-up

The initial Go run [41203324260](https://github.com/infdahai/zenQuant/actions/runs/41203324260) failed because `ubuntu-latest` resolved to Ubuntu 26.04, which locked Playwright 1.58.2 cannot install Chromium dependencies on. Its macOS matrix sibling was cancelled after the Linux failure; that cancellation is not evidence of a macOS defect. The retained Rust browser job [41203362435](https://github.com/infdahai/zenQuant/actions/runs/41203362435) failed for the same Ubuntu/Playwright incompatibility, while its Rust core job passed. The original [Harness Gates run](https://github.com/infdahai/zenQuant/actions/runs/41203362382) passed remotely.

Commit `6893e523a414bfae55851d72edf5a8a07c3940f0` pins both browser-consuming Linux jobs to Ubuntu 24.04, supported by the existing Playwright dependency table. No lockfiles or dependency versions changed. Non-browser jobs retain their existing runners. All seven checks on that repair commit completed successfully:

| Remote workflow | Run | Result |
| --- | --- | --- |
| Go mainline (push) | [41203512482](https://github.com/infdahai/zenQuant/actions/runs/41203512482) | Ubuntu 24.04 and macOS both PASS, including browser installation and both browser suites |
| Go mainline (PR) | [41203515590](https://github.com/infdahai/zenQuant/actions/runs/41203515590) | Ubuntu 24.04 and macOS both PASS, including race/vet/build and browser suites |
| Rust options diagnostics | [41203515610](https://github.com/infdahai/zenQuant/actions/runs/41203515610) | Rust core/CLI and retained options/manual-paper browser jobs both PASS |
| Harness Gates | [41203515559](https://github.com/infdahai/zenQuant/actions/runs/41203515559) | Python gates A–E PASS |

This establishes Linux runtime evidence as well as local macOS evidence. The local legacy Rust macOS `UnsupportedPlatform` result above remains a platform limitation, not a failing Linux CI gate. Workflow YAML parsing and `git diff --check` also passed. This follow-up changes only browser runner selection and verification documentation; no dependency upgrade, production action or main-checkout write occurred.

## Paper initialization review follow-up

The initial mkdir-before-snapshot defect is addressed in both Go and Rust: prepare a complete journal in a private staging directory, retain the directory flock, publish with descriptor-relative no-replace rename, then confirm parent durability. Competing initializers reopen and validate the winner rather than treating EEXIST as fatal. Existing missing or corrupt snapshots still fail closed; no user journal is migrated or reset.

At that revision, coverage included first-write injected failure checkpoints, initial-creation process termination, concurrent first confirmations, and Linux Go/Rust process exclusion and replay. The historical Linux CI built the pinned Rust test helper and ran these cross-language tests explicitly; current CI no longer does so. Independent review identified published-directory permissions and existing-store trust checks; both were corrected with mode/ownership rejection tests. The historical protocol required updated binaries on both sides; older binaries cannot gain the new recovery guarantees retroactively, and current mixed-writer safety has not been established. See the manual guide for the Unix owner/mode trust boundary and limits.

Final local follow-up checks passed: `make check` (Go format/vet/race and frontend checks), `make browser` (3 options tests and 1 full paper workflow), `GOOS=linux go vet ./...`, Rust format/clippy, benchmark-script Ruff checks and `git diff --check`. Process tests cover same-request replay and different-request stale rejection. At delivery commit `16a412e588ee0c92a49503f355526bd5009a173f`, the Linux job in [Go PR run 41206817180](https://github.com/infdahai/zenQuant/actions/runs/41206817180) successfully built the Rust helper and passed the explicit Go/Rust process exclusion/replay step. The [retained Rust run 41206817228](https://github.com/infdahai/zenQuant/actions/runs/41206817228) completed successfully in both core and browser jobs. These are Linux runtime results, not macOS cross-compilation claims. Final aggregate checks are linked from PR #24.

The local lightweight performance measurement uses only an engineering journal; methodology, raw results and failure counts are in [GO_PERFORMANCE.md](GO_PERFORMANCE.md). It is not production capacity evidence.

## Scope and limits

- The default Go product implements the current options UI and manual record-only paper behavior. Rust source and standalone quote/terminal, simulated-intent and reservation CLIs are being removed, with no equivalent Go commands for those standalone workflows. Python strategy/research/collectors remain outside the default service. This is not a claim that every historical command was ported.
- Runtime validation was on macOS with Go 1.27.3; module minimum is Go 1.25. Remote Go CI passed on Ubuntu 24.04 and macOS, as linked above. Windows is unsupported by this Unix file-lock implementation.
- A Rust-produced journal fixture proves compatibility for the covered events; no user journal was accessed or migrated. File-sync and kill tests do not prove every hardware/power-loss scenario.
- Svelte dependency installation reported two low-severity advisories in the existing locked dependency graph; dependencies were not upgraded as part of migration.
- Archived research is source preservation, not correctness or economic evidence. See the [per-file archive manifest and verification](research-archive/20261031-local-consolidation/README.md). Local settings are excluded; tracked lockfiles are preserved as inert text.
- Golden research outputs and original Rust evidence were not regenerated or rewritten.
