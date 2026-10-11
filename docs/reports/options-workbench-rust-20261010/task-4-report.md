# Task 4 report — offline Rust CLI, reports and native publication

DONE. Base87cef6c; HEAD96c0b1a. Only options-workbench crate and its direct-dependency Cargo.lock entries changed. Core and every preexisting Python/legacy/artifact path unchanged. No accounts, trading network, orders, deployment, merge or push. No Task5 work undertaken.

## Exact CLI

`options-workbench --request-file PATH --output-dir PATH [--panel-file PATH]`

Standalone `--help` prints exactly that usage. Unknown/duplicate flags, missing arguments and combined help fail safely; no account/live/paper flags or Python runtime. Paths are OS strings; errors never echo input, argv or paths. Exit2 INVALID_INPUT for admission/preflight/report failures, creating no new directories; exit1 INCOMPLETE_PUBLICATION for publication failures; exit1 PUBLISHED_DURABILITY_UNCONFIRMED after successful rename but failed final durability/parent/destination identity checks; exit0 after complete fsync even when economic diagnostics decline support.

Run:

```
cargo run --manifest-path rust/Cargo.toml --locked -p options-workbench -- --request-file rust/options-workbench/fixtures/terminal-eth-call.json --output-dir /absolute/canonical/new-report
```

Three complete explicit typed fixtures under `rust/options-workbench/fixtures/`: terminal-eth-call.json, terminal-eth-straddle.json, terminal-vertical.json. Each supplies cutoff/view_id, mathematical marker, finite quotes/scenarios and every capital/loss/wallet/policy budget. CLI decodes supplied values, never fills financial defaults or builds outcomes from a name. Existing public core fixture builder remains unchanged.

## Reporting and identities

Deterministic fixed-schema report.json, REPORT.md and identity.json; canonical JSON rejects floats through the existing exact serializer. Full AnalysisInput preserves selected view plus complete view_history, scenarios, budgets, source identities, competing explanations/invalidation/no-trade conditions and gaps. Individual expressions and vertical certificates retain five support columns, independent costs, conditional outcomes, funding explanations, reasons and constraints. Markdown includes per-candidate diagnostic detail and the complete structured timeline. Untrusted strings remain escaped inside HTML preformatted blocks; HTML delimiters, quotes, control characters and bidirectional overrides are encoded. No source path becomes a link or supplied HTML.

Authority is fixed by renderer: diagnostic_only=true, rankable=false, live_eligible=false, execution_authority=NONE. actual_pnl/paper_pnl/sharpe/capacity/lifecycle_arbitrage_edge null. Account/execution columns remain UNVERIFIED. Conditional mathematical PnL is explicitly labeled; no raw-source verification promotes projection semantics or account authority.

Identity separates actual request-byte SHA256, canonical AnalysisInput SHA256, source-byte pins/identities, code-source hash, arithmetic/model versions and Cargo.lock SHA256. Build script embeds sorted Rust source/Cargo manifest identities and lock bytes; code identity is source identity, not a binary/reproducible-compiler attestation. No time/stage/pid/output path enters artifacts. View parent hashes never rewritten. Whitespace-only request-byte changes alter byte identity while leaving canonical input identity unchanged.

## New public IO APIs

- `publication::read_bounded(&Path,usize)->Result<Vec<u8>,Failure>`: limit+1 checked read, regular files only; descriptor-relative traversal with O_NOFOLLOW/O_NONBLOCK/O_CLOEXEC.
- `io::repository_root()/trusted_roots()` derive fixed dataset locations from build repository context. Linked-worktree gitdir+commondir discovers original repo, never input pins. No arbitrary root CLI flags.
- `io::verify_panel_sources(&PanelEnvelope)->Result<VerifiedSources,Failure>`; opaque proof with read-only `pins()->&[PinnedSource]`. Only native allowlisted-source verification constructs this public proof.
- `io::validate_output(&Path,&[PathBuf])->Result<PublicationTarget,Failure>`; opaque target retains preflight ancestor descriptor/inode. Existing target, symlink/dangling components and protected ancestor/descendant overlaps reject.
- `publication::publish(&PublicationTarget,&[(&str,Vec<u8>)])->Result<(),Failure>`; exactly three unique fixed artifact names. Native exclusive descriptor-relative rename and durability checks. Failure injection remains private, used by in-module test files only.
- `report::render(&AnalysisInput,&[u8],Option<VerifiedSources>)`: deterministic three artifacts; raw verified label requires the opaque proof. `report::escape_markup(&str)` is the text renderer helper.

Panel/request bounded16MiB/1MiB. Native sources each<=16MiB,total<=64MiB,count<=1024 including both seals. Both raw seal bytes must match built-in SHA256 before either seal supplies file paths. Strict pre-map duplicate rejection also applies to seals. Seal entries require safe relative Normal components, exact length/hash and no symlinks. Every sealed file is read, including unused files. Exact transport pinset equality includes seals, excludes duplicates/omissions/additions; transport absolute paths are comparisons only, never filesystem authority. Raw integrity RAW_PINNED_BYTES_VERIFIED is distinct from EXPORTED_PROJECTION_UNATTESTED. apply_panel remains responsible for proxy/UNKNOWN semantic restrictions.

CLI always protects the two fixed raw roots plus request, panel and verified source paths. Full admission, pin verification, preflight and artifact rendering occur before creating parents. Missing parent suffix is created with mkdirat/openat under the held ancestor. Existing ancestor/parent identity is rechecked; stage identity checked before rename, destination identity afterward. Each file, stage directory and parent are fsynced; created parent entries are synced. Native macOS renameatx_np(RENAME_EXCL)/Linux renameat2(RENAME_NOREPLACE); no overwriting fallback. Racing targets untouched. Pre-rename failures retain `.options-workbench-incomplete-PID-SEQUENCE`; if stage cannot be created (for example readonly parent), no stage exists and error remains fixed INCOMPLETE_PUBLICATION. After rename there is no truthful remaining stage: preserve destination and exit1 on durability failure.

## Evidence and limitations

Observed meaningful compiling RED: initial actual CLI invalid-request test expected exit2 but received0; deterministic fixture test found no artifacts from placeholder binary. Both GREEN after implementation. Remaining safety/economic-output tests were added as GREEN regressions, not falsely claimed independently RED.

Final GREEN:58 Rust tests =41 existing core integration tests +7 new IO/publication unit groups +10 actual CLI integration tests. Full locked workspace cargo test, cargo fmt --all --check and cargo clippy --locked --all-targets -- -D warnings pass. CLI tests cover actual concurrent target race, existing target untouched, readonly-parent publication exit1, request/raw-root overlap, source/dangling symlinks, unknown flags/oversize/no-dir/error sanitization, repeated same bytes, separate byte/canonical identity, retained future timeline, escaped hypotheses, all three explicit fixtures/null metrics. Native publication tests inject write/file-fsync/stage-fsync/rename/post-rename-parent-fsync failures, racing sentinel target, parent swaps both before publication and before rename. IO unit fixtures verify both seals/every unused raw/exact pins, omissions/duplicates/arbitrary paths, modified unused source, unauthenticated seal, traversal and symlink rejection.

macOS native publication and actual CLI were executed on this workstation. Linux syscall branch exists but was not compiled or runtime-executed here; no Linux native acceptance claim. If build-context fixed sealed roots are absent (including unmapped Linux export paths), native panel admission fails closed; fixture CLI does not need sealed roots to exist. Strict no-symlink policy rejects macOS /tmp and /var aliases: use canonical paths (for test temp dirs, canonicalize base). The readonly-parent CLI assertion ran under the normal non-root workstation user; root-only environments can bypass UNIX mode bits and require separate test adaptation.

Synthetic small seals are used only by private unit verification helpers; production public verifier has exactly the two built-in seals. Full real exporter/native480-quote panel acceptance remains Task5, not claimed here. Existing raw root current bytes were not rewritten. Local filesystem owner/admin can still modify files concurrently; descriptor anchoring and identity rechecks make detected parent/stage swaps fail closed, but this is not an isolation guarantee against a malicious same-UID process continuously altering directory contents or racing the final check. Source proof describes bytes read at verification, not perpetual source immutability. Mathematical reports grant no trading, strategy promotion, liquidity capacity or live performance acceptance.

Commits:ae851da dependencies;460ccfb native publisher;f54098e sealed source verification;4d618a6 deterministic report identities;3a59531 CLI;da98124 explicit fixtures;96c0b1a safety tests. Worktree clean after commits. Report is retained in ignored SDD handoff directory, matching prior task reports.

## Review fix round 1 — three Important findings

All3 corrected; HEADb2ef3f0. Candidate inline heading now encodes newline as `&#10;`, preventing an untrusted blank line from terminating its HTML block and enabling a Markdown image/link. Full preformatted JSON keeps structural newlines; supplied string newlines there remain JSON escapes.

Original caller paths now reject ParentDir before any normalization. Shared public absolute/read_bounded/reject_symlinks/validate_output and crate-internal target preparation enforce this, covering request/output/panel and library callers. Only private repository-owned Git gitdir/commondir discovery retains parent-component normalization; linked-worktree common-root discovery remains functional. No caller path is silently reinterpreted as its lexical alternative.

Publication artifact-name validation remains admission INVALID_INPUT. After admission, every operational open/path/identity failure is mapped to INCOMPLETE_PUBLICATION (exit1), including disappearing or symlink-replaced parents after a stage exists. Post-rename failures retain PUBLISHED_DURABILITY_UNCONFIRMED. Regression keeps the identifiable stage in the renamed held parent and creates no destination in the replacement path. The existing actual CLI readonly-parent test independently covers exit1 mapping; parent-disappearance/symlink injection itself is a deterministic private publisher unit test, not a falsely claimed actual argv process injection.

Exact observed RED commands (all compiled, exit101):

- `cargo test --manifest-path rust/Cargo.toml --locked -p options-workbench candidate_heading_cannot_escape`: FAILED assertion `!heading.contains('\n')`.
- `cargo test --manifest-path rust/Cargo.toml --locked -p options-workbench original_parent_traversal`: FAILED expected CLI Some(2), actual Some(0).
- `cargo test --manifest-path rust/Cargo.toml --locked -p options-workbench post_stage_parent_disappearance`: FAILED expected Err(Incomplete), actual Err(Invalid).

After implementation, the same three commands each returned exit0 with one matching test passed. Additional direct target-prepare/no-dir check and second branches are GREEN regressions; no claim each subcase was separately RED.

Final commands: `cargo test --manifest-path rust/Cargo.toml --locked` →62 passed (41core +9IO/publication +12actual CLI),0failed; `cargo fmt --manifest-path rust/Cargo.toml --all --check` →exit0; `cargo clippy --manifest-path rust/Cargo.toml --locked --all-targets -- -D warnings` →exit0. Four scope-pure commits94bf424 heading fix,175afaa original-path refusal,8065e49 operational error classification,b2ef3f0 reviewed boundary regressions. Worktree clean. Platform, source/projection authority and Task5 boundaries unchanged; no legacy edits.
