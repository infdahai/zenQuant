# Rust D2 Options Workbench Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox syntax. User approved D2 implementation and explicitly changed its language to Rust; preserve its evidence and permission boundaries.

**Goal:** Deliver the approved offline options-expression comparison and same-venue vertical diagnostic as a Rust executable and reusable pure Rust library, preserving Python P1 and its historical artifacts.

**Architecture:** Two crates under `rust/`: `options-core` owns typed contracts, exact arithmetic, causal selection, costs, expressions and constraint proofs; `options-workbench` owns local transport, reporting and CLI publication. An explicit offline Python panel exporter reuses the existing sealed loader only. The Rust executable does not embed or invoke Python, contact accounts, or submit orders.

**Tech Stack:** Rust 2024; Serde/serde_json, num-bigint/num-rational/num-traits, SHA-256; libc only in the publication adapter. Cargo.lock committed. Python P1 is the regression oracle, not a new runtime dependency of Rust.

**Authority:** [approved architecture](../specs/2026-10-09-options-trading-workbench-design.md); [approved D2 plan](2026-10-09-options-workbench-d2.md) is the economic/acceptance baseline. This amendment replaces its language, file paths, P1-in-process reuse and CLI flags, not its product scope. Base `99d3292aa5873c79d94bfb4d77f857b12a90cb4e`. Worktree `codex/options-workbench-rust`.

## Global constraints and rulings

- All old Python files, tests, exports, CLI and serialization remain byte-identical. No live/backtest, strategy, ledger, gate or golden changes. BTC is the first validation market, not a hard-coded product boundary.
- No network/time/randomness/environment/process APIs in options-core. Core consumes typed arguments. CLI never enables live/paper/account mode. Inputs cannot grant authority.
- Output `diagnostic_only=true`, `rankable=false`, `live_eligible=false`, `execution_authority=NONE`; actual/paper PnL, Sharpe, capacity and lifecycle arbitrage edge remain null.
- Five separate statuses: input validity, model support, modeled feasibility, account eligibility, execution evidence. Account/execution always unverified. A positive mathematical vertical bound never becomes a tradable arbitrage claim.
- New schema `options-workbench-request.rust.v1`, new report `options-workbench-report.rust.v1`, new arithmetic method `exact_decimal.rust.v1`. Never label Rust identities as P1 fingerprints or promise full Python 50-digit rounding equivalence.
- New arithmetic uses arbitrary precision rationals internally and accepts only finite decimal strings, at most 64 significant digits and 28 fractional digits on input. Add/sub/mul are exact; division must terminate with at most 256 fractional digits or return a typed numerical limitation. No float or silently rounded money. Values are canonically plain decimal strings, zero `0`. Intermediate representation bounded to 1024 digits; arithmetic outside this domain fails closed.
- Request at most 1 MiB, 128 view revisions, 64 candidates, 64 scenarios, 512 quotes, 16 legs per candidate, 128 levels per book side, JSON depth64/string4096; checked before analysis/publication. No implicit scaling or budgets.
- Typed structs deny unknown fields. Recursive JSON decoding rejects duplicate keys before any map loses information, any JSON float, non-finite input, booleans in integers, future schema, unsupported extra authority fields, trailing content. Invalid input creates no output path.
- Complete typed view history is carried into reports. Analysis cutoff is distinct from original view cutoff. View parent hashes use the new Rust canonicalization. Manual timestamps remain `manual_unattested`.
- Same canonical spec/time conflicting quotes are invalid, not price selection. Identity binds venue/product/native ID/rule/spec hash. No fee, index or FX cross-record borrowing.
- Quote provenance preserves proxy/UNKNOWN exercise and settlement evidence. Units and FX pairs explicit; no denomination inference from a value of 1. Source bytes hashes and semantic hashes separate.
- Costs can be diagnosed for unsupported model products, but funded terminal model only linear European, same quote/strike/settlement/margin/report currency and common fixing. All long legs BUY, four exact shapes; cash no interest only. No early-exit/path model. Missing terminal fee => gross visible, net/EV/loss bound unknown and feasibility NOT_EVALUABLE.
- Fixing domain binds venue, underlying, expiry, settlement index/window/rule/currency. All same-domain scenario prices must match; conflict invalidates all net outcomes/EV. A single selected future point must equal expiry, not an earlier intrinsic shortcut.
- Entry plus terminal reserve must satisfy view capital/loss budget, per-intent/global/tail policy and each distinct hypothetical wallet. Mutually exclusive candidate reference accounts; no sum of results, no real reservations. Multiple wallet/unsupported account modes retain missing evidence. Short certificate never emits an intent.
- EV only when forecast + subjective + complete nonnegative weights summing exactly1. Risk-neutral/empirical/stress weights do not imply subjective EV. No probability => null.
- Same-venue vertical certificate uses piecewise knots and analytic tail slopes, not finite scenarios alone. SELL only static diagnosis. Terminal fee/financing/margin unavailable => conditional fields unknown; lifecycle edge always null.
- Native Rust implementation is a versioned D2 model, not a rewrite of Python ledger. Meaningful fixed-domain cross-language tests verify costs and paid-long payoff; known precision domains separately tested/documented.
- Shared dirty checkout and sealed evidence remain untouched. Full Python gate failures recorded separately; no merge, push, deployment or alpha promotion.

## Files and interfaces

Workspace `rust/Cargo.toml`, `Cargo.lock`, `.gitignore` (target only).

`options-core/src/{lib,decimal,contracts,codec,inputs,costs,expressions,constraints}.rs`;
`options-core/tests/{contracts,inputs,costs,expressions,constraints}.rs` and common fixed factories.

`options-workbench/src/{lib,reporting,publish,main}.rs`; integration tests and explicit JSON fixtures under `rust/fixtures/`.

`scripts/export_options_workbench_panel.py`, `tests/options/test_rust_workbench_compatibility.py`, `docs/OPTIONS_WORKBENCH_RUST_GUIDE.md`.

Contract fields mirror approved D2 plan section3, with Rust equivalents of necessary P1 InstrumentSpec/Book/Observation/FeePolicy/TimingPolicy/WalletKey/WalletBalance/RiskPolicy fields. No Rust legacy ledger or P1 executable intent. `ExpressionAnalysis` uses a descriptive model candidate and `ModeledFunding` (status, required cash by wallet, policy hash, reasons), not `P1 RiskApproval`. `RecordMeta/View/ScenarioPoint/Scenario/ScenarioSet/CandidateRequest/QuoteInput/AnalysisInput/SupportColumns/ScenarioOutcome/ConstraintCertificate` retain their approved responsibilities. Values have no interior mutable state. New canonical/hash helpers own Rust identity.

Request envelope: schema_version, cutoff_ns, view_id, views, scenarios, candidates, quotes, hypothetical_balances, risk_policy, source_identities. Exact names/version published by Task1; no generic `Value` in the computational contract. Scenario/view hashes must bind selected content. Optional `--panel-file` accepts exporter evidence only, must be hash-bound, verified against original pinned paths, and cannot override manual request quotes silently. Mathematical fixture invocation is an explicit request fixture_name, mutually exclusive with panel data; own Rust fixture builder, no import of tests. Fixtures never masquerade as market observations.

Core APIs (Result carries fixed safe error codes): `decode_request(&[u8])`, `select_view(&[View], view_id, cutoff_ns)`, `load_analysis(Request)->AnalysisInput`, `quote_cost(&QuoteInput, &CandidateLeg, cutoff, report_currency)->CostEvidence`, `analyze_expression(&AnalysisInput,&CandidateRequest)->ExpressionAnalysis`, `certify_vertical(&AnalysisInput,&CandidateRequest)->ConstraintCertificate`. New unknown template retained as UNSUPPORTED, never fallback. Reporting consumes AnalysisInput with full view_history, analyses and certificates.

## Review focus

1. Invalid request and arithmetic domain overflow fail before any output directories.
2. Late view/spec/fee/book/FX cannot rewrite original identity or enter an earlier decision.
3. Same native ID on another venue remains separate; same fixing inconsistent terminal prices invalidate results.
4. Missing fee/depth/unit/terminal reserve produces unknown, never zero or budget pass.
5. No result can be interpreted as account approval, live order, completed trade or profitable strategy.

## Task 1: Rust workspace, exact values and frozen contracts

- [ ] Create two-crate Cargo workspace with core-only dependencies serde, serde_json, sha2, num-bigint, num-rational, num-traits. CLI libc later. Pin Cargo.lock. `forbid(unsafe_code)` in core; no Python changes.
- [ ] Define versioned Dec, canonical serialization/hashes, complete typed contracts/envelope/result enums and error taxonomy described above. Expose only exact operations; decimal parser rejects exponents in this new schema, `.01` accepted and canonicalized, negative zero canonical0. Division 1/3 returns limitation; 1/8=.125. No old hashing equivalence claim.
- [ ] Build reusable Rust fixture factories (test-only) and explicit CLI fixture builder if needed as a separate public fixture module clearly tagged. Freeze interfaces for subsequent tasks; record exact names in task report.
- [ ] RED meaningful decimal/canonical/identity/invalid timestamp/limits tests before implementation; GREEN cargo test for Task1. Tests for high magnitude, precision rejection, distinct spec venue/hash, round-trip unknown rejection and finite values. Separate scope-pure commits.

## Task 2: Strict codec, causal input and history

- [ ] Implement recursive strict JSON visitor and typed decoding, all engineering limits, schema allowlist, immutable view-chain selection and complete history retention.
- [ ] Validate revision1 parentnull, contiguous revisions, exact parent hashes, unchanged object identity, admissible clocks. Visible selected version based received+available+cutoff; future revisions remain excluded in timeline. Reject conflicting revisions/duplicate IDs/invalid selected scenario binding.
- [ ] Bind descriptors to exact spec hashes and fields; validate structural metadata/lots/ticks/observations/fee timing, same spec/knowledge conflicting quote. Missing evidence stays typed gaps. Select latest visible unambiguous quote without lower-price bias. Restrict manual/source identities from claiming external attestation.
- [ ] Implement exporter-envelope adapter validation signature fields as a reusable pure projection; concrete I/O pinned-file verification belongs CLI Task4/5. Core never reads files.
- [ ] RED/GREEN malformed JSON, nested duplicate keys, future fields, float/bool, depth/size/collection limits, revision selection100/120, late fee/spec/FX, duplicate native IDs across venues, conflicting book, full typed history. Commit only Task2 files/tests.

## Task 3: Native Rust costs, expressions and vertical proofs

- [ ] Implement exact native displayed cost, base/contracts multiplier, native lot/tick validation, BUY spend and SELL proceeds, complete ordered uncrossed two-sided book, time and unit checks, separate fee cap and FX. No fills inferred.
- [ ] Implement four long templates and same-currency cash model; support/modeled budget separate. Model opening/terminal losses and break-even knots, frozen scenario prices, aggregate reserve constraints including nonzero terminal fee, conditional subjective EV. Any incomplete scenario makes EV unknown; no accidental zero.
- [ ] Preserve disabled/unsupported/path/interim/IV cases and reasons. No generic price model or interpolation. Early-time payoff not expiry payoff. Capital profiles explicit input.
- [ ] Implement call/put vertical signed intrinsic nodes/slopes and full tail proof; independent cost diagnostics, no short funded intent or lifecycle edge.
- [ ] RED/GREEN approved D2 plan §4 exact hand oracles: call cost .0203, put .0153, straddle .0356, EV .0047/.0144; strangle roots86.44/113.56; depth .26+.003=.263; vertical spend-.0094, bounds.0094/.1094. Same fixing110vs90 rejects. Reserve .0203 exact passes vs.020299 fails, terminal.001 reserve/global/tail/wallet tests. Missing sources/unsupported templates return explicit statuses. Commit pure modules/tests scope-pure.

## Task 4: Rust report, offline CLI and atomic publication

- [ ] Build new deterministic JSON/Markdown with complete view timeline, sources and version/code identities, fixed authority/null outputs, individual candidate explanation. Escape Markdown/HTML/control characters; never inject supplied source paths as HTML. Do not label scenario PnL as actual.
- [ ] Offline binary `options-workbench --request-file PATH --output-dir PATH [--panel-file PATH]`. No other permission/account/live flags. Fixture names exactly terminal-eth-call, terminal-eth-straddle, terminal-vertical. Include view_id/cutoff in explicit fixture requests; build own finite mathematical fixture and require budget values, never implicit financial defaults.
- [ ] Bounded read(limit+1), reject before parent creation; protect request, exported panel and every pinned source from output ancestor/descendant/symlink overlap. Verify pinned identities using SHA-256 before using panel; bound each source/manifest traversal and fail closed on unsupported seal.
- [ ] Staging dir contains report.json, REPORT.md, identity.json; hashes bind request bytes/canonical input, source/code/method/Cargo.lock identities; fsync files/dir then native macOS renamex_np/Linux renameat2 no-replace. Existing target or racing target untouched; no fallback overwriting rename. Failed publication leaves identifiable incomplete dir. libc confined publication module.
- [ ] exit2 invalid input/no new dirs; exit1 internal/incomplete publication; exit0 complete diagnostic even NO_TRADE. RED/GREEN actual CLI tests including existing target/race/symlink/source overlap/write failure, identical outputs and null authority. No timing values that break same-input output.

## Task 5: Sealed Python input exporter, parity and user handoff

- [ ] New standalone Python exporter reuses `load_panel` + `input_integrity`; requires PINNED_BYTES_VERIFIED, never modifies old panel/report source. Exports full metadata/quotes/units/times/gaps/proxy labels in new panel schema. Preserve old UNKNOWN fields and rules. Missing account/settlement evidence not fabricated. Native Rust identities separate from P1 source fingerprints. Output no-replace and source/output overlap protection.
- [ ] Add Python integration tests which generate bounded P1 fixture cost/payoff oracles and compare built Rust results in explicitly shared finite input domain; low/high Python caller contexts produce identical oracle results. Native arithmetic domain counterexamples fail closed or are explicitly out of equivalence scope. No full-domain P1 hash parity claim.
- [ ] Verify old P1 source SHA and CLI fixture artifacts unchanged versus base worktree. Run cargo fmt --check/test --locked/clippy --all-targets -Dwarnings; Python options/import boundary/parity tests, Ruff only new Python files. Run full zenquant-check once to record existing integration gate outcomes; never repair legacy/golden within this task.
- [ ] Write guide with architecture diagram, actual commands/request schema, native arithmetic scope, module extension recipe, limits, all evidence/status meanings, terminal-only scenarios, sealed-data limitations, and future migration gates. At least call/straddle/vertical CLI artifacts independently checked. No profitability or performance claim without experiment.
- [ ] Whole-branch independent review, address load-bearing findings, archive evidence and ledger before cleanup. Keep isolated branch, no merge/push/deploy.

## Dependencies and preflight

`1 -> 2 -> 3 -> 4 -> 5`. Only one implementation worker at a time; read-only architecture/test reviews can be parallel. Fresh implementer and task reviewer each task. Source compatibility audits repeated after integration; never re-copy dirty main checkout. Known broader Python gate issues remain an explicit integration limitation, not a Rust task waiver.

The user already approved execution and selected subagents; this Rust language amendment is within that authorization. Root rulings resolve language-related plan defects in the execution ledger; no repeated permission question.
