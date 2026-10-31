# Local verification, 2026-10-22

Rust 1.90.0; baseline PR #23 commit `25c0d12c88036ede232c2b54a5bc62cdfe43a8c1`.

- Full Rust workspace: **209 passed, 0 failed** (199 existing + 10 new snapshot tests).
- Formatting check and all-target/all-feature clippy with warnings denied: pass.
- Snapshot CLI tested in a fresh process with PATH=/nonexistent; success JSON and admission-failure empty stdout checked.
- Existing VIX HTTP tests and all prior Rust CLI/store tests are included in the full suite.
- No Svelte source change. Browser/Harness final-head results are recorded by GitHub Actions, not asserted by these local logs.
- All new raw evidence is generated synthetically inside temporary test directories. No real prices or permissions were admitted. No network collector was implemented.
- `source-files.sha256` is relative to repository root; other checksums are relative to this directory. Original demo and Cargo.lock are unchanged.

Commands:

```sh
cargo fmt --manifest-path rust/Cargo.toml --all -- --check
cargo clippy --manifest-path rust/Cargo.toml --workspace --all-targets --all-features --locked -- -D warnings
cargo test --manifest-path rust/Cargo.toml --workspace --all-features --locked
cargo test --manifest-path rust/Cargo.toml --locked -p options-workbench --test vix_snapshot
```

Local source reconstruction initially added trailing newlines and omitted archived baseline receipts; the original bytes and required receipts were restored before the final passing full run. No baseline fixtures or existing runtime code were changed to make tests pass.
