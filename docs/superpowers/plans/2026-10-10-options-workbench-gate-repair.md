# Options Workbench Integration Gate Repair Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Resolve the A/E/F integration blockers while keeping the Rust D2 contracts and the original dirty checkout intact.

**Architecture:** Gate A changes only formatting. Gate E registers its existing strict local-panel option in the root test configuration, preserving sealed-source verification. Gate F propagates its existing OVERFIT verdict to the CLI exit status, and runs against an isolated, hashed copy of available local historical inputs.

**Tech Stack:** Existing Python/pytest/Ruff harness and native Rust workspace; no new dependencies.

**Spec:** `AGENTS.md` harness rules; `zenquant/app/check.py::gate_f`; `docs/reports/options-workbench-rust-20261010/README.md` integration blockers; `docs/LIVE_VALIDATION_HANDOFF_SPEC.md` current promotion restriction.

## Global Constraints

- Work only in `.worktrees/options-workbench-rust`; the original checkout and historical sources remain read-only.
- Preserve legacy strategy, config, golden output, source pins, Rust D2 schema and order authority.
- Empty windows, unavailable replay data and any OVERFIT window must fail Gate F; do not lower the existing verdict threshold.
- Real historical train/dev validation is distinct from strategy promotion, robustness and live acceptance.
- Root owns formatting and F; the Gate E agent owns root/options conftests and its new registration regression. Independent review follows all changes.

## Review Focus

- Root pytest discovery must register the strict flag early; targeted options discovery must not register it twice.
- Missing local sources must skip only in portable mode; strict mode and invalid seals must still fail.
- One OVERFIT window must block even when the aggregate looks positive; zero windows must block.
- Copies must include all input paths read by the unchanged replay, record missing paths, and never create shared write-through symlinks.
- A full harness success must not be presented as options profitability or permission to promote the paused Quant V2 strategy.

### Task 1: Formatting-only Gate A repair

**Files:** `scripts/analyze_intent_churn_attribution.py`, `zenquant/data/event_calendar.py`, `zenquant/data/liquidation_pipeline.py`.

**Interfaces:** Same Python AST and APIs before/after; no contract or config change.

- [x] Reproduce with `python -m ruff format --check` on the three files; archive exit 1 and capture their ASTs.
- [x] Run `python -m ruff format` on only these paths.
- [x] Compare ASTs with `ast.dump(ast.parse(source), include_attributes=False)`; run `python -m zenquant.app.check --gate a` and archive exit 0.
- [x] Commit only those three files with `style: format existing harness modules`.

### Task 2: Register the strict options flag at root discovery

**Files:** `tests/conftest.py`, `tests/options/conftest.py`, `tests/test_options_pytest_registration.py`.

**Interfaces:** `--require-local-options-panel` retains its current meaning; `require_local_options_panel(strict=...)` is unchanged.

- [x] Build a minimal temporary suite from the two real conftests. Exercise real pytest CLI root and targeted discovery with absent sealed roots.
- [x] Confirm root portable discovery currently errors and root strict discovery rejects the flag; targeted discovery currently works.
- [x] Move the existing `pytest_addoption` hook to `tests/conftest.py` without fallback defaults or weakening seal checks.
- [x] Run all four real CLI cases, existing options source-policy tests, then the final full Gate E run.
- [x] Commit the test and option registration as separate single-purpose commits.

### Task 3: Gate F exit semantics and honest local replay

**Files:** `scripts/walk_forward_eval.py`, `tests/test_walk_forward_gate.py`; isolated ignored `data/` and `results/options_platform/gate_repair_20261010/` snapshots.

**Interfaces:** `run_walk_forward(...) -> int`; CLI calls `sys.exit(run_walk_forward(...))`. Gate runner is unchanged and consumes this real return code.

- [x] Preserve the original checkout's existing uncommitted exit-status repair by leaving it untouched. In the isolated branch, test the same intended contract through the actual CLI and `check.gate_f()` wrapper.
- [x] Use fixed backtest-boundary outcomes `[2.0,-1.0,2.0,5.0]`, healthy `[2.0,1.5,2.0,3.0]`, degraded `[2.0,0.1]`, and zero windows. Hand-derived exit codes are respectively `1,0,0,1`.
- [x] Watch the OVERFIT and empty-window regressions fail. Return 1 for no windows or any existing OVERFIT verdict; otherwise return 0; propagate via `sys.exit`.
- [x] Run the regression suite with split-discipline tests. Existing replay errors must continue raising and returning a nonzero CLI exit.
- [x] Audit the unchanged replay's actual file reads, copy each existing dependency into the isolated data tree with per-file SHA-256 and byte length; record absent dependencies and coverage. Do not copy unrelated bulk archives.
- [x] Run the full `python -m zenquant.app.check` once after A/E/F repairs, with the real snapshot. Record the actual F verdict even if it blocks integration.
- [x] Commit F tests and implementation separately, then preserve compact gate evidence and commands in a tracked report.

### Final acceptance

- [x] Independently review the repair diff and fail-closed behavior.
- [x] Verify original checkout HEAD, branch, dirty path set and all 110 protected file hashes; verify isolated copied historical files have not changed during replay.
- [x] Run native Rust tests/fmt/Clippy once to verify cross-language contracts still pass.
- [x] Leave a clean, scope-pure branch with run instructions, gate results and remaining limits. No merge, push, deployment, account connection or trading occurs in this repair.
