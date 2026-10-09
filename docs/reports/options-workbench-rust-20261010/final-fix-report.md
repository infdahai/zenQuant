# Final whole-branch fix wave

Status: DONE; fresh independent scoped rereview remains controller work. BASE `3772897`, HEAD `bc73f82` (`git rev-parse HEAD` exact ID in evidence validation-status.json). Worktree `/Users/disaster/workspace/code/zenQuant/.worktrees/options-workbench-rust`, branch `codex/options-workbench-rust`; tracked worktree clean. No subagents, merge, push, deployment, orders, account calls, legacy/golden/gate changes.

## Both Important findings fixed

1. Cash candidate admission now allows zero legs only for `template_id=cash`. Full decode→load→public expression and actual binary→report regression succeeds. `v1` cash returns zero conditional PnL and valid no-trade modeled funding under existing explicit budgets/scenario/probability rules. Versions `future` and `1` remain `UNSUPPORTED`, no alias. Empty non-cash legs still fail admission. No cash economic rules rewritten.
2. `inputs::ValidatedAnalysis<'a>` is a borrowed handle with private field and fallible constructor. It reconstructs the same Request through the existing pure admission path, verifies exact selected view equals the selected full-history record, and retains every existing scenario hash/clock/shape, spec, quote, candidate, budget, fee-knowledge rule. Candidate argument must exactly occur in admitted requests. Immutable borrow prevents later mutation during evaluation. Public `analyze_expression` and `certify_vertical` preserve signatures but require this handle; private implementations run only after admission success or return a typed admission error. `render` validates once and reuses the handle for all candidates, avoiding 64 repeated full quote admission/hash passes.

Producer/consumer adjustment was communicated to controller before implementation and accepted. Struct fields/serde/output identities are unchanged. Publicly constructed/deserialized invalid data now returns `INVALID` with fixed-code `INPUT_ADMISSION_*` reason; no model funding, modeled candidate, net outcomes, EV or valid certificate. Report returns typed error before publication. Missing/late quote knowledge remains retained evidence but cannot yield valid funding/net. Extra panel adapter `gaps` are deliberately not compared bytewise with reconstructed loader gaps, and never establish authority.

Economic tests were corrected, not bypassed: missing terminal fee is now changed inside admitted requests; changed view budgets/horizon are honestly rebound to history + scenario view hash and readmitted; removing fee evidence clears its knowledge timestamp; initial capital reduction also preserves max-loss<=capital. Incomplete probability input now fails structurally as INVALID, while valid unsupported products/numeric limitations remain fail-closed. No original product semantics weakened to keep tests green.

## TDD and actual checks

All commands use explicit worktree cwd; logs copied to `results/options_platform/rust_d2_execution_20261010/final-fix/`.

Before implementation (compiling RED, runtime assertions):
- `cargo test --locked --manifest-path rust/Cargo.toml --test public_boundary` → exit101, 4 failed: cash admission returned InvalidInput; unbound/mismatched expression, certificate and foreign candidate remained Valid.
- `cargo test --locked --manifest-path rust/Cargo.toml -p options-workbench --test cli cash_request_reaches_actual_cli_report` → exit101, CLI returned2 instead of0.
- `cargo test --locked --manifest-path rust/Cargo.toml -p options-workbench --test cli report_public_boundary` → exit101, render accepted invalid input.

GREEN and final validation:
- `cargo test --locked --manifest-path rust/Cargo.toml` →70 passed,0failed (includes six public boundary tests and two new actual CLI/report tests). Initial broad run exposed four old economic fixture construction failures; corrected as above, then full suite passed. `final-green-initial.log` retains these failures honestly.
- `cargo fmt --manifest-path rust/Cargo.toml --all --check` → PASS.
- `cargo clippy --manifest-path rust/Cargo.toml --locked --all-targets -- -D warnings` → exit0.
- `PYTHONPATH=. /Users/disaster/workspace/code/zenQuant/.venv/bin/python -m pytest tests/options tests/test_import_boundaries.py tests/test_parity.py -q --tb=short` →459 passed,1 skipped,0failed in9.55s. No Python files changed; no Ruff rerun necessary.
- `git diff --check` clean. Formatting only changed lines in new D2 files; no separate unrelated format changes.

The first four public tests and two CLI/report tests were observed RED. Expanded malformed metadata, zero multiplier, deserialization/adapter gaps, borrowed-handle candidate membership, and late quote knowledge cases were subsequent GREEN coverage, not falsely claimed individually observed RED cycles.

## Real panel and preservation

Fresh exporter invocation: original main venv Python, `PYTHONPATH=/Users/disaster/workspace/code/zenQuant`, cwd `/private/tmp`, absolute worktree new exporter, original `results/crypto_opportunity/20261009_options_liquidity_panel_v1`, fresh `final-fix/sealed-panel.json`. Exit0. New export equals task5 sealed-panel bytes.

Actual native binary `rust/target/debug/options-workbench --request-file task5/sealed-request.json --panel-file <old/new panel> --output-dir <fresh final-fix/{old-panel,new-panel-a,new-panel-b}>` ran three times, all exit0. Every report/REPORT.md/identity.json is byte-identical across those three fresh outputs. All480quotes/543verifiedpins retained; all specs UNKNOWN exercise/settlement and fee_known/index/FX evidence None preserved, adapter gaps accepted. Authority NONE, all actual/paper/performance/lifecycle fields null. Code source hash differs from pre-fix task5 report as expected. No task5 reports or root_demos overwritten.

Old CLI rerun in fresh own dirs with explicit capital1000 and long-call/straddle: all6 artifacts byte-identical with original baseline. Baseline SHA checks:23protected P1 files and110main protected files unchanged. All21task5 evidence-manifest entries match both SHA256 and size. Evidence script initially asserted lowercase enum values; corrected the checker to actual SCREAMING_SNAKE_CASE UNKNOWN and verified all480records (no product change).

`final-fix/verification.json`, `validation-status.json`, logs, three native reports, fresh exporter output, six old CLI outputs, and `evidence-manifest.json` preserve the evidence.

## Commits

- `15ad3d9 test(options): cover public analysis admission boundaries`
- `862174f test(options): exercise cash CLI and admitted economic inputs`
- `aa1032d fix(options): admit zero-leg cash candidates`
- `bcac4b0 fix(options): enforce public causal admission before evaluation`
- `bc73f82 docs(options): explain enforced public admission and cash inputs`

## Remaining boundaries

Full harness was NOT rerun; prior full gate A/E/F blockers remain recorded in task5 report/log, not waived or repaired. Linux compile/runtime remains unrun. Raw pins are byte integrity, not independent semantic projection attestation. No actual PnL, account eligibility, fills, margin, financing, settlement, full P1 arithmetic/ledger equivalence or trading authority claimed. Public invalid inputs now intentionally fail sooner; direct callers that mutate view/candidate state must rebind/readmit honestly. New handle adds one bounded clone/admission per batch, no I/O and no ledger/grant authority. New guide documents enforced entry points and reachable cash request. Deferred Mac-corpus portability issue remains out of this fix wave.
