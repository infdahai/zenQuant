# PR #23 VIX historical archive

These files are copied byte-for-byte at their original paths from [PR #23](https://github.com/infdahai/zenQuant/pull/23), head `c9c35a708b0286effc530fdc0919823d8caa9d8f`:

- [Source research](../../VIX_SNAPSHOT_SOURCE_RESEARCH_20261022.md)
- [2026-10-17 observation acceptance](../vix-observation-20261017/README.md)
- [2026-10-22 snapshot acceptance](../vix-observation-snapshot-20261022/README.md)

The archive records the retired Rust implementation and its synthetic demonstrations and tests. It is historical evidence, not a current test result, live market data validation, or proof of Go/Rust behavioral or byte parity. The source research includes historical public page observations and licensing caveats; it does not establish rights to collect or redistribute market data. The state receipts contain only historical hashes and timestamps, not the underlying state files. Historical paths and commands refer to the original environment and may no longer work.

`SHA256SUMS` in this directory covers every copied file, including the original checksum manifests and source research, with paths relative to the repository root. Verify from the repository root with `shasum -a 256 -c docs/reports/vix-pr23-archive/SHA256SUMS`. The original manifests are retained unchanged and can also be checked from their respective directories. `source-files.sha256` identifies historical Rust source bytes at the PR head; those source files are not restored here.

For current behavior and limitations, see the [Go VIX guide](../../GO_VIX_OBSERVER.md). Establishing parity requires a separate comparative validation against the historical implementation.
