# Local research consolidation

This is an inert source preservation archive, recorded 2026-10-31. It does not approve research, training, promotion, deployment, or use of the Python accounting path as current funds authority. The current Go/Rust/frontend policy and the October retirement decision remain unchanged.

## Identity and coverage

Source checkout (read-only): `/Users/disaster/workspace/code/zenQuant`.
Destination worker: `/Users/disaster/.herdr/worktrees/zenQuant/refactor-go-mainline-20261031`, branch `refactor/go-mainline-20261031`.

| Ref | Frozen commit |
| --- | --- |
| Source HEAD / local main | `440389aebdb6fce796c9411c833c0b96d051f3bf` |
| Cached personal/main / worker initial HEAD | `5fca5d69222567dd0108deb3bbd54177d61de1a7` |
| Merge base | `6a6f8211fa8352a66343b015507c8e1bd9d9c941` |

The coordinator fetched `personal` at task start. This worker's comparison is frozen at the recorded `personal/main` ref; it does not claim remote freshness at a later time.

[The detailed manifest](snapshot/manifest.json) accounts for every one of 204 distinct paths: 99 paths changed by local committed history relative to the merge base, and 115 dirty/untracked source paths (21 tracked modifications and 94 untracked files; 10 overlap). It records path, dirty status, local-commit membership, disposition, reason, SHA-256 of base/personal-main/local-main/working bytes where applicable, and archive payload locations. Missing versions are explicit nulls. This avoids interpreting remote-only additions as local deletions.

All 171 commits reachable from local main but not personal/main have SHA, parent list, subject, per-file path list, exclusions, and a first-parent patch. This includes intermediate changes that were later reverted or changed again, and the consolidation merge. Per-commit patches are evidence, not a recommendation to apply them. Their overlapping changes must not be replayed as one linear patch series.

The 382 payloads total 7,864,514 bytes. Every payload has SHA-256 and byte length. Original source bytes are stored under `snapshot/committed/` and `snapshot/working/`; history patches are under `snapshot/history/`. All payload names end in `.txt`, including old Python tests, CI definitions, package metadata, frontend sources and deployment unit examples. They are not importable modules, discovered tests, active workflows, or executable deployment definitions.

## Decisions by source family

| Source family | Preserved meaning and disposition |
| --- | --- |
| Accounting/replay/risk guard changes and tests | Preserve committed liability, canonical-mark, report atomicity, provenance and rerun repairs plus later working-tree cashflow/replay changes. Archive only: the October decision explicitly declined activating this legacy authority. |
| Quant V2 acceptance and episode tests | Preserve updated acceptance/accounting evidence contracts and uncommitted test revisions. Their assertions are historical source, not proof of acceptance. |
| Time-space/trend registration, export, readiness and data normalization | Preserve full committed implementations, tests, configs, workflow definitions and intermediate fixes beyond the older archive tag. Do not re-enable old workflow authority or research searches. |
| CSV conversion | Preserve strict spot normalization history and dirty positional/header validation fixes with their tests. Integration would activate a retired Python path and needs a separate scoped decision. |
| Research framework, bounded search, target/multi-leg accounting, ablation, perturbation, diagnostics, evidence evaluation and runners | Preserve all untracked modules, CLI scripts, configs, tests and specifications. No claim of tested research behavior or safe search authorization. |
| Frontier lab D01–D11 | Preserve experiment implementations, common contracts and tests as unassessed research. No runs, data reads, training or promotion. |
| TradFi monitoring and deployment | Preserve application/research modules, dislocation/sector-monitor tests, configuration, guide and service example as inert text. No installation or service activation. |
| Local architecture, handoff, CLAUDE, README and operational documents | Preserve exact historical text without replacing current documentation or policies. Archived instructions are historical data, not instructions for this checkout. |
| Browser/store tests, Vite and package metadata | Preserve dirty/untracked frontend-related source as inert archive evidence only. Current frontend, root package metadata and CI are not changed by this worker. |
| Exclusions | `.claude/settings.local.json` is listed but never opened, hashed, copied or included in patches. Dirty `uv.lock` is preserved as `snapshot/working/uv.lock.txt`; the historical `rust/Cargo.lock` delta is preserved in the consolidation merge patch. Dependency lockfiles are source evidence and are not excluded. Root datasets, results, environment files, dependency trees, Git internals and caches are excluded before payload reads. Ignored source artifacts are outside the requested dirty/untracked inventory. |

No source fixes were integrated into the active runtime. A wholesale merge was rejected because local main would replace newer remote Rust/UI/manual-paper policy and restore retired research authority. Preservation retains the code and tests without making that product decision.

## Relationship to the October archive

Read [20261011/README.md](../20261011/README.md) and [archive-refs.json](../20261011/archive-refs.json) first. Both recorded annotated tag objects and target commits were verified against local Git objects. All four pinned source-file SHA-256 values match. This consolidation supplements those tags with later local history and working files; it does not replace their negative-result interpretation.

`BLOCKED_PAUSE_PROMOTION_AND_TRAINING`, incomplete all-cost accounting, unverified legacy comparisons, and unverified receipt ZIP bytes remain limitations. No datasets, results corpus, receipt downloads or historical experiments were obtained or rerun here. Source hashes establish byte identity, not correctness, market authenticity or reproducibility of experiments.

## Verification and recovery

The snapshot was created before copying. The tool rebuilt the full inventory twice before destination writes and refused any mismatch in refs, status, source hashes or commit evidence. The archive uses the captured bytes, never a later unchecked filesystem copy. A final independent inventory comparison after copying also matched.

Checks run:

- Eight standard-library unittest contracts passed: committed/dirty/untracked retention, source mutation refusal, settings exclusion even for an unreadable symlink, tamper/extra-file rejection, local deletion recording, source-module versus dataset classification, lockfile retention in working/committed/history evidence, and binary/credential-marker rejection.
- `ruff check` and `ruff format --check` passed for the consolidation script and its tests.
- All 382 archive payload hashes and byte counts verified, with no missing/extra files.
- Prior archive tag/object and four pinned source hashes matched local Git.
- Direct `git diff --no-index --check` inspection found whitespace diagnostics in 133 historical payloads (2,016 trailing-whitespace and 14 blank-EOF diagnostics). Patch context lines and original source bytes must remain intact. The archive-local `.gitattributes` disables whitespace checks only for `snapshot/**/*.txt`; normal checking remains enabled for current code, manifests and documentation. Archive additions were checked using a temporary Git index without staging or altering the shared worktree index.

Verification commands from the repository root:

```sh
python3 -I -m unittest discover -s tests -p test_consolidate_local_research.py
python3 -I scripts/consolidate_local_research.py verify \
  --output docs/research-archive/20261031-local-consolidation/snapshot
ruff check scripts/consolidate_local_research.py tests/test_consolidate_local_research.py
ruff format --check scripts/consolidate_local_research.py tests/test_consolidate_local_research.py
```

For recovery, select a manifest path and its `committed` or `working` version, verify its hash, and copy it to a separate explicitly designated research workspace, removing only the final `.txt` suffix. Retrieve unchanged dependencies from the pinned Git base/main objects. Do not copy the entire archive over the current checkout. History patches preserve dependency lock changes and explicit excluded-path lists, but intentionally cannot reconstruct secrets. The archive is a delta/source preservation record, not a standalone executable environment or Git bundle.

The source was not edited, staged, reset, stashed, committed or pushed. This worker leaves its archive, script and tests uncommitted for controller review. Other workers' files are outside this evidence scope. Full Python runtime tests and research backtests were not run because no runtime behavior was integrated; successful archive tests do not certify the archived research.
