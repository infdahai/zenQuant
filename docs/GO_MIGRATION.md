# Go mainline migration

Status: the native Go local options and manual paper product is implemented; the broader Go-native trading research workbench is the direction of travel, not a completed migration. The matrix below defines the delivered migration scope. Verification and known limitations are recorded in [GO_VALIDATION.md](GO_VALIDATION.md).

## Product direction and admission

The intended workbench should make trading research reproducible and auditable,
from point-in-time data and confirmed bar-close decisions through strategy
intent, risk, realistic execution and replay. Today the Go service provides
options diagnostics and record-only manual paper workflows. It does not provide
the retained Python strategy/replay pipeline, data collectors, automated risk
and execution runtime, or paper-live operation. The Rust source and standalone
CLIs are being removed from this worktree. Their standalone quote-cost and
terminal diagnostics, sealed-panel report publication, simulated-intent and
cash-reservation workflows have no equivalent standalone Go command; Go's
options UI and manual record-only paper flow cover a narrower scope. These
capabilities require explicit implementation and validation before they can be
claimed as Go-native; the migration matrix does not establish parity for them.

The user-facing workflow is owned by the Go backend and Svelte UI: domain
validation and paper-state authority belong in Go, while the UI collects input
and presents results. Manual records do not become reservations, fills, or
execution authorization. Historical Rust-generated fixtures and prior Linux
cross-language results are evidence for the cases exercised at the time; after
source and helper removal, fresh Go/Rust differential and concurrent-writer
tests cannot be run from this checkout. Fixture checks alone do not prove full
cross-language compatibility or safety of mixed writers.

The language change does not change admission: use train for parameter learning,
dev for candidate selection and robustness, and frozen test only for final
historical acceptance. The Quant V2 frozen acceptance failed late-dev at extra
15bp on all five seeds (strict robustness 0/5); promotion and new parameter
training remain paused. Paper-live requires the existing validation gates, a
positive eligibility verdict and the [operator handoff](LIVE_VALIDATION_HANDOFF_SPEC.md);
real capital requires separate approval. See [architecture](ARCHITECTURE.md)
and [Quant V2 acceptance](QUANT_V2_ACCEPTANCE_HANDOFF.md) for evidence and
limits. Manual paper declarations and unassessed intents are not paper-live
admission or execution authority.

## Design and acceptance

The default local product becomes a Go binary serving the existing Svelte options and manual paper UI. Domain decisions remain deterministic and use exact decimal/rational arithmetic. HTTP, disk persistence and CLI wiring are separate adapters. Existing schema identifiers remain compatibility identifiers, even when they contain `rust`; changing a language does not silently change persisted contracts.

An alternative is a Go launcher calling the existing Python/Rust programs. Rejected: that would not migrate the core behavior or remove those runtime/build dependencies from the default product.

| Surface | Target | Acceptance |
| --- | --- | --- |
| Synthetic and supplied option payoff inputs | `internal/options` native Go | Existing request/report shapes, exact costs, missing-evidence behavior, strict decoding and adverse cases |
| Manual configuration, intents and withdrawal | `internal/paper` pure domain | Combined USD100000 declaration, explicit assumptions, no execution authority, preview binding/expiry and retry semantics |
| Paper snapshot persistence | `internal/paper` adapter | Canonical replay, compatible hashes, exclusive writes, atomic durable publication, corrupt/missing existing state fails closed |
| Local HTTP and static UI | `internal/server` | Loopback only; bounded workers, bytes and deadlines; Host/Origin/header checks; no arbitrary file routes; original browser workflows |
| Default run/build/check | `cmd/zenquant`, Makefile, npm and Go CI | Go build, vet and race tests; Svelte checks and browser tests; no Python/Rust subprocess implementation |
| Standalone Rust diagnostics, intent and reservation CLIs | Removed with Rust source | Historical evidence remains; these commands and their unported functions are unavailable in the current checkout |
| Python research and local-only changes | Reviewed consolidation/archive | Per-file disposition, source hashes, local-only history preserved, no training/tuning/promotion |

The work uses the designated worktree. The primary checkout is a read-only source. Tests use temporary paper stores, never the user's allocation authority. No production changes, deployment, real orders or training are part of this migration.

## Reference reviewed

Source: [yetone/magpie](https://github.com/yetone/magpie), pinned at `cac655c14fa546f903b7fcfb742c5bfa88ae0f97`, downloaded into a new temporary directory on 2026-10-31. Its scripts were not executed and no Magpie configuration or LLM feature is introduced.

Concrete patterns adopted after reading source and development documents:

- [`main.go`](https://github.com/yetone/magpie/blob/cac655c14fa546f903b7fcfb742c5bfa88ae0f97/main.go): a thin executable delegates behavior to internal packages. ZenQuant keeps composition in its CLI and business behavior in domain packages.
- [`internal/steady/steady.go`](https://github.com/yetone/magpie/blob/cac655c14fa546f903b7fcfb742c5bfa88ae0f97/internal/steady/steady.go): bounded file operations and temporary-file flush before rename. ZenQuant additionally preserves its existing directory fsync and journal replay requirements.
- [`docs/code-standards.md`](https://github.com/yetone/magpie/blob/cac655c14fa546f903b7fcfb742c5bfa88ae0f97/docs/code-standards.md): failed reads mean unknown, not empty; verify the entire input-to-state path, neighboring cases, numeric edges and races; tests use isolated real-shaped inputs.
- [`docs/contributors.md`](https://github.com/yetone/magpie/blob/cac655c14fa546f903b7fcfb742c5bfa88ae0f97/docs/contributors.md): inspect scope and side effects before running downloaded changes, and review concrete diffs and verification evidence. ZenQuant retains its own commit/review policy.

These are architecture and iteration references, not adopted instructions from the external repository.
