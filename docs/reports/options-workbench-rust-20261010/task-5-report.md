# Task 5 — offline sealed exporter and finite P1 compatibility

Status: DONE_WITH_CONCERNS. Task5 scope delivered; whole-branch review/archive remains controller work. BASE b2ef3f0; HEAD 3772897. Clean worktree after three scope-pure commits. No old Python/core/golden/gate changes, raw data mutation, account calls, market network, merge/push/deployment or execution promotion.

## Delivered

- `scripts/export_options_workbench_panel.py`: standalone offline exporter using original public `load_panel` and `input_integrity`; requires PINNED_BYTES_VERIFIED. Original loader file determines fixed original roots. Both fixed seals and all sealed files receive bounded verification before public loader and again after projection. Full actual 480 quotes/48 specs/543 pins including both seals. Strict seal duplicate/nonfinite/float rejection; max16MiB each,64MiB total,1024pins. No Rust runtime required.
- Native canonical Decimal transport is independent of caller Decimal context: plain strings, canonical zero, exact no-float sorted compact UTF-8, `exact_decimal.rust.v1` + NUL domain prefix. New native projection/spec identities remain distinct from preserved P1 spec/book/panel fingerprints and raw pin identity.
- Hash-before-admission normalization matches Rust: sealed_proxy, exercise UNKNOWN, settlement UNKNOWN. Original rule/exercise claims and original index observation/knowledge clocks remain assumptions; Bybit selection observations remain labeled observations. Full raw bodies/selection/preregistration/metadata are retained through their complete sealed pins. Metadata quantities/units/ticks/lot/expiry/knowledge clocks preserved.
- Frozen preregistration fee/cap proxy retained; fee_known_ns=None. No denomination inferred from1, no USD/stablecoin parity invented, index/FX typed fields stay None without independent quote-denomination evidence. Missing terminal fee stays None in actual panel request.
- Original '..'/all symlink components reject before normalization. Existing output/source ancestor-descendant overlaps reject. Existing parent only; invalid inputs create no directories. Descriptor-relative O_NOFOLLOW regular-file bounded reads and anchored parent publication; exclusive stage + atomic hard link no-replace, file+parent fsync. Fixed CLI errors do not echo source paths. Rust remains Python-independent.
- `tests/options/test_rust_workbench_compatibility.py`:22 actual-binary integration checks. Finite-domain P1 quote_cost + settlement_plan call/straddle oracles under ambient Decimal6/100; fee caps, depth, contract multiplier, wallet rejection, terminal fee; SELL diagnostic via vertical; exact native panel admission; recomputed projection spec/fee/book attacks retaining genuine raw pins cannot gain model/authority; no-replace/traversal/symlink/source overlap; unbound PanelInput; precision limits and explicit nonterminating root limitation. No full-domain P1 hash or 50-digit money-rounding equivalence claim.
- `docs/OPTIONS_WORKBENCH_RUST_GUIDE.md`: Chinese architecture Mermaid, real commands and complete schema source links, arithmetic/identity scope, model/status/authority limits, input budgets, actual hand oracles, fields/model-boundary ablations, validation limitations and extension/migration gates.

## Genuine TDD evidence

RED before new exporter implementation:
`PYTHONPATH=. /Users/disaster/workspace/code/zenQuant/.venv/bin/python -m pytest tests/options/test_rust_workbench_compatibility.py -k canonical -q`
→1 failed/10 deselected; assertion `Task5 exporter must exist`. Correct expected missing-feature failure (not import/syntax error).

GREEN after minimal exporter:
same file command without -k →11 passed,0failed. Later actual-export admission test first caught incorrect seal filename (`INVALID_INPUT`, exit2) and passed after fixing seal.json. This was an implementation defect caught by new coverage, not claimed an independent initial TDD cycle for every subcase. Additional finite cases/safety cases are genuine GREEN regressions, not falsely described as individually observed RED.

Self-review strengthened the nonterminating-root counterexample: base multiplier3 would hit unsupported quantity semantics and was insufficient precision evidence. Test now uses contracts multiplier3/quote_per_contract, valid displayed cost .0309 + terminal .001, base quantity .03; break-even division .0319/.03 cannot terminate. It asserts exact actual reason `NUMERICAL_LIMITATION:NumericalLimitation`, while native cost is VALID (verified report). This isolates new numeric-domain fail-closed behavior. Input 29 fractional digits is separately exit2/no report. Rust decimal tests already cover direct1/3 limitation and1/8=.125. No P1 rounding-equivalence assertion outside this finite domain.

## Actual validation

All tools ran with explicit isolated workdir; Python main venv executable + worktree PYTHONPATH for tests/gates.

- `cargo fmt --manifest-path rust/Cargo.toml --all --check` →exit0.
- `cargo test --manifest-path rust/Cargo.toml --locked` →62tests passed,0failed.
- `cargo clippy --manifest-path rust/Cargo.toml --locked --all-targets -- -D warnings` →exit0.
- New-file Ruff check and format check →exit0. No old files formatted.
- `PYTHONPATH=. /Users/disaster/workspace/code/zenQuant/.venv/bin/python -m pytest tests/options tests/test_import_boundaries.py tests/test_parity.py -q --tb=short` →459passed,1skipped,0failed. Final repeat after strengthening numerical assertion: same459/1 in8.56s. New test file accounts for22passes. Existing P1 sealed integration skips in isolated worktree because original fixed roots absent; actual new sealed-export/native panel runs separately and does not skip here.
- **Full zenquant-check executed once only**: `PYTHONPATH=. /Users/disaster/workspace/code/zenQuant/.venv/bin/python -m zenquant.app.check > .../task5/zenquant-check.log 2>&1` →exit1. Gate0/B/C/D PASS. GateA Ruff check passed, format failed on three unchanged legacy files: scripts/analyze_intent_churn_attribution.py; zenquant/data/event_calendar.py; zenquant/data/liquidation_pipeline.py. GateE1837passed,10skipped,1error,34subtests; error tests/options/test_panel.py::test_sealed_input_integration, `ValueError: no option named 'require_local_options_panel'`. GateF failed wf_1 with no historical events under data/raw/binance for BTCUSDT. No repair/waiver or copied/symlinked shared data.
- To establish GateE causality rather than infer from unchanged SHA: default root discovery `PYTHONPATH=. .../python -m pytest -q -k test_sealed_input_integration --ignore=tests/options/test_rust_workbench_compatibility.py --tb=short` →1836deselected,1sameerror. New test module excluded from collection/import/execution. This proves the existing root-discovery option-loading issue independently of new test execution; it is not repaired. No second full suite/gate run.

## Real sealed export and old-path protection

Actual standalone export ran **cwd /private/tmp** with `PYTHONPATH=/Users/disaster/workspace/code/zenQuant` and original main `.venv/bin/python`, invoking absolute worktree new script. No loader/root/pin monkeypatch or dirty-main copying in product:

`.../python .../.worktrees/options-workbench-rust/scripts/export_options_workbench_panel.py --panel-root /Users/disaster/workspace/code/zenQuant/results/crypto_opportunity/20261009_options_liquidity_panel_v1 --output-file .../.worktrees/options-workbench-rust/results/options_platform/rust_d2_execution_20261010/task5/sealed-panel.json`

→exit0 EXPORTED_PROJECTION_UNATTESTED. Independent actual native CLI with explicit sealed-request.json + panel →exit0, all480quotes and543verified sources preserved, projection hash `49d8304eca3b10fbcd4765e9b1cf4a6d3003f1cb07695575b938b20e0863657e`. Model support/feasibility UNSUPPORTED due UNKNOWN; cost NOT_EVALUABLE due missing evidence; account/execution UNVERIFIED; NONE authority. Actual/paper PnL, Sharpe, capacity and lifecycle edge null.

Baseline `.superpowers/sdd/.../baseline.json`: all23 protected P1 sources/tests checked SHA unchanged; all110 main protected files checked SHA unchanged. Own legacy fixture CLI calls with capital1000, scenario long-call and straddle each produced three files matching baseline p1_call/p1_straddle byte-for-byte. Existing CLI accepted spelling is `straddle`, not long-straddle. Raw roots untouched.

Reused controller's actual native `root_demos/{terminal-eth-call,terminal-eth-straddle,terminal-vertical}` without overwrite. Hand checks: call spend .0203/root102.03/net(-.0203,-.0203,.0797); straddle spend .0356/roots96.44,103.56/net(.0644,-.0356,.0644); vertical spend-.0094 and bounds.0094/.1094, no short execution support. These are mathematical fixtures, not market performance experiments.

## Evidence and concerns

Own evidence directory `results/options_platform/rust_d2_execution_20261010/task5/` contains exported-panel bytes, actual sealed request/report+identities, legacy byte-comparison outputs, compatibility-evidence.json, validation-status.json, cargo-fmt/test/clippy logs, Python initial/final targeted logs, full harness log, exclusion control log, and evidence-manifest.json with byte lengths/SHA256 for every artifact. Directory is ignored results output; controller must archive report/rulings/evidence before scratch cleanup. Nothing in root_demos overwritten. Guide is tracked and maintainable independent of chat; operational artifact paths are explicitly identified.

Full Python harness remains red, so this is Task5 completion only, not whole branch/merge readiness. macOS execution verified; Linux not compiled/run. Code-source identity is not binary/compiler attestation. Raw pins prove bytes read, not exchange authenticity, continuous immutability, account eligibility or projection semantics. A privileged same-UID process can still mutate filesystem state; descriptor anchoring/source rereads are detection safeguards, not OS isolation. Scenario EV/terminal math does not prove profitability, capacity, arbitrage or strategy promotion. No added market observation/time/performance assumptions.

Commits:
- 26d1ddb feat(options): add sealed Rust panel exporter
- a9c1da7 test(options): verify finite Rust P1 compatibility
- 3772897 docs(options): document offline Rust workbench
