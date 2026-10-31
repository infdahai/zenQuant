# Python to Go inventory

The manifest was regenerated at `7a1731e` and covers 437 tracked Python files. This is an inventory and migration planning baseline, not an authorization to remove Python or promote a strategy. The companion [manifest](python-go-inventory.json) enumerates every Git-tracked `.py` at the source revision identified in it, including tests. It records each file's SHA-256, category, syntactic imports and entrypoint indicators. The historical 425-file count below described the earlier baseline; consult the regenerated manifest for current counts. Git-tracked paths, rather than installed packages or a working-directory walk, determine coverage; ignored and untracked files are excluded. Changes to tracked Python content require regeneration. The source revision identifies the baseline, not the commit containing this document.

## Scope and interpretation

`category` is the top-level directory; `package` is the second directory under `zenquant`, `scripts` or `tests`, or the category for files directly inside it. Imports are raw AST `import` names or `from` module names with relative dots and imported names retained. They include imports under functions, conditional branches and `TYPE_CHECKING`; presence does not establish that a dependency is installed or executed. Relative imports are not resolved, and imported names need not be modules. `main_guard_lines` identifies syntactic `__name__ == '__main__'` guards, `main_function_lines` finds module-level `main` definitions, `console_scripts` records `pyproject.toml` project script mappings, and `shebang` records a first-line shebang. These indicators are hints, not proof of an executable path. Files with parse errors retain their path and error instead of silently disappearing.

Static AST cannot discover all `importlib`, `__import__`, plugin discovery, reflection, subprocess, shell, scheduler, configuration, notebooks or external callers; it also does not prove reachability or cross-language parity. Search configuration and callers and review operations before any disposition. Dependencies are distribution names in `pyproject.toml`, not necessarily identical to imported module names. The manifest does not declare any individual file migrated, obsolete or safe to delete.

## Dependencies and execution surfaces

Python requires >=3.11. The project declares DuckDB, FastAPI, PyArrow, psycopg, Uvicorn, stable-baselines3 and Gymnasium; optional extras are Telegram, macro/yfinance and optimize/Optuna. The dev group contains HTTPX, dill, mutmut, Optuna, pytest and Ruff; see [pyproject.toml](../pyproject.toml) and `uv.lock` for the actual declarations and lock. Ten installed console commands include research, collection, web, validation, checking and RL tuning; see the manifest and project scripts for the exact mapping. Script paths, scheduled workflows and external timers are additional entrypoints, independent of `main` guards.

[Harness CI](../.github/workflows/harness.yml) uses Python 3.12, `uv sync --group dev`, Ruff and pytest gates A–E on main-targeting PRs and specified pushes. `make legacy-python` explicitly runs A–E; it excludes the data-dependent research Gate F. [Go CI](../.github/workflows/go.yml) covers the Go/UI product, not Python-to-Go research parity. Scheduled/manual [data sync](../.github/workflows/data_sync.yml) and [options collection](../.github/workflows/collect_options.yml) invoke Python and may access external data and storage; they are operational callers, not migration tests. Inspect current workflow and branch protections before making CI changes.

## Entrypoint and caller disposition (2026-11-02)

All ten console commands in [pyproject.toml](../pyproject.toml) remain Python-owned and require a verified replacement before retirement:

| Entrypoint | Actual responsibility and disposition |
| --- | --- |
| `zenquant-backtest`, `zenquant-demo` | Historical strategy replay and pipeline demo; port and validate the full workflow. The Go diagnostic replay is narrower. |
| `zenquant-history-sync`, `zenquant-forward-collect` | Historical downloads and forward collection/health; port collectors, storage, scheduler and recovery. |
| `zenquant-live-export`, `zenquant-live-scorecard`, `zenquant-live-validate` | Export, paper-live scorecard and validation; retain pending audited Go replacements and admission. |
| `zenquant-web` | Python demo/live web runtime; Go manual paper UI is not equivalent. |
| `zenquant-check` | Python harness gates including data-dependent research gate; retain until all callers and gates are replaced. |
| `zenquant-rl-tune` | RL tuning; retained research capability, training remains paused. |

Direct operational callers also remain: [data sync](../.github/workflows/data_sync.yml) schedules historical, macro and options scripts; [options collection](../.github/workflows/collect_options.yml) schedules Deribit collection and storage; [harness CI](../.github/workflows/harness.yml) calls Python tests and fixture checks. These callers are not replaced by the Go workspace. External timers, dynamic imports, fixtures and the remaining scripts still require individual ownership review; this table is not a complete retirement authorization.

## Migration status and staged acceptance

The Go diagnostic workspace adds an isolated JSON snapshot and synthetic replay workflow; it does not replace Python data collection, strategy, risk, realistic execution, scheduled jobs, fixture generation or the Python validation gates. The manifest remains an inventory, not a complete disposition map: callers, schedules and ownership still require review before any Python retirement. See [GO_RESEARCH_WORKSPACE.md](GO_RESEARCH_WORKSPACE.md).

[GO_MIGRATION.md](GO_MIGRATION.md), [GO_VALIDATION.md](GO_VALIDATION.md) and [ARCHITECTURE.md](ARCHITECTURE.md) describe the actual boundary: the local options and record-only manual paper product is Go; the Python strategy/replay, collection, automated risk/execution and paper-live runtime are not established as Go-native. Historical fixtures or passing Go product tests do not establish parity for those systems. Python research freeze and existing promotion rules remain in force. On 2026-10-31 PR [#25](https://github.com/infdahai/zenQuant/pull/25) was open with GitHub merge state `BLOCKED`; this is a time-specific observation, not a durable dependency or migration verdict. Recheck the PR and its checks before relying on that status.

The first Go slice implements the default BTC split summary and containment comparisons in `internal/research/splits.go`, exposed as `zenquant research-splits`. A golden summary was compared with the Python reference and Go tests cover boundary and reversed-period behavior. Go nanoseconds are `int64`, whereas Python integers are unbounded; containment itself preserves the Python behavior and does not validate period ordering. This is a contract port, not a research pipeline or promotion gate. The Python module and callers remain active.

The second slice ports the pure Gate v3 evaluator to `internal/research/gate_v3.go`, using synthetic Python-generated golden cases for parity and a Python fixture freshness check in the harness. It does not build episodes from fills, replace Python callers, or authorize promotion. The JSON fixture generator added in this stage is not part of the baseline manifest; regenerate the inventory at a later committed revision to include it. The Python 3.12+ reference uses compensated floating-point summation; Python 3.11 uses a different summation algorithm. The Python in-process evaluator accepts non-finite floats, whereas the CLI accepts finite, representable JSON numbers only.

The next slice ports position-episode accounting and a single-symbol replay accounting adapter to `internal/research`. Synthetic Python-generated event snapshots cover fills, funding, price observations, ordering and errors; the Python fixture freshness check runs in the harness. The adapter consumes only the latest visible funding row, records price before bar-triggered fills and normalizes stale report timestamps to the visibility cutoff. It does not implement the full replay loop, execution, report collection or the legacy trade ledger. Unlike Python, Go rejects non-finite accounting inputs and rejects an invalid bar before changing funding or cutoff state. Go rejects episode durations exceeding `int64` nanoseconds; Python integers are unbounded. Go rejects non-finite derived episode arithmetic and applies each replay bar's funding and price updates atomically. Both fixture generator scripts added since the baseline are absent from its manifest. Python sources and callers remain active.

The offline sizing slice ports the Python leverage calculation to `internal/research/sizing.go`. Synthetic reference cases cover configuration, discounts, caps, reason codes and decimal rounding boundaries; the fixture check runs in the Python harness. The scope is the tested finite inputs with nonnegative confidence products; unsupported non-finite inputs, non-finite intermediate results and negative products are rejected. Python indicator coercion and all floating-point boundary cases are not established as equivalent. Equity is accepted but unused by the reference. This component is not connected to replay, execution or live workflows. The sizing fixture generator also postdates the baseline manifest.

The offline replay event slice filters and orders in-memory candles and derives complete one-hour candles from five-minute inputs only when native one-hour coverage is absent. Synthetic Python fixtures cover filtering, fallback and ordering. Go requires timeframe-aligned full candles with exclusive closes or inclusive closes one nanosecond or one millisecond earlier; it also validates candle inputs more strictly than Python. Volume summation targets Python 3.12+; Python 3.11 can differ. Storage, auxiliary joins, strategy and execution remain Python responsibilities. This fixture generator also postdates the baseline manifest.

The offline mark-price execution slice produces synthetic full-fill or rejected reports with an explicit timestamp and taker fee, and can feed replay accounting. It rejects invalid numeric inputs and unsupported limit, non-market and reduce-only orders more strictly than Python. It does not enforce risk approval, reduce-only, position limits, slippage or limit orders, and has no live order path. Its fixture generator postdates the baseline manifest; the Python gateway and callers remain active.

The offline risk target clamp applies the final exposure and reduce-only quantity calculation using an already computed cap and position. Its v2 contract derives the quantity cap from the exact binary64 inputs and conservatively quantizes quantity toward zero to six decimal places and independently capped leverage to four decimal places. The returned quantity respects the exact exposure cap and reduce-only position bound. This intentionally diverges from historical Python v1 nearest rounding on some synthetic cases; the Python-generated fixtures preserve that historical evidence. It does not calculate risk state, authorize orders or replace the Python overlay. Its fixture generator postdates the baseline manifest.

The offline trade proxy slice normalizes synthetic Hyperliquid recent trades and aggregates large-trade proxy buckets in memory. It does not collect, deduplicate, persist or verify liquidations. Event time is not receipt time; a bucket cannot be treated as point-in-time available without evidence of completion and collection availability. Go rejects malformed values and mixed sources more strictly than Python. Its fixture generator postdates the baseline manifest.

The offline basis slice joins in-memory spot and perpetual rows and provides cutoff-based basis and robust z-score snapshots, compared against synthetic Python fixtures. Input timestamps must share a unit and represent data already available at that time, including any duplicate corrections; the slice does not establish publication or receipt timing. Go rejects non-finite values, nonpositive perpetual prices and invalid window parameters. It does not replace Python storage or ingestion. Its fixture generator postdates the baseline manifest.

The offline indicator slice implements ATR, annualized realized volatility and rate of change for synthetic ordered candles. Python reference fixtures cover windows, short histories and timeframe fallback with floating-point tolerance. Go requires finite inputs, positive bounded windows and bounded input size; it rejects detected overflow and volatility ratio underflow. It does not implement the complete indicator engine, event processing or strategy. Its fixture generator postdates the baseline manifest.

The synthetic offline replay integration combines prepared candle events, indicators, caller-supplied policy, mark-price simulated fills and episode accounting. Funding and price observations precede bar-triggered fills. It is an in-memory testable workflow, not the Python strategy or full historical replay; same-close fills are synthetic, the adapter uses only the latest newly visible funding row, and no risk or order authorization is implied.

The offline exit engine ports bounded position exit decisions and trailing-stop state transitions using synthetic Python reference cases. It requires finite inputs and rejects arithmetic overflow; it does not own position lifecycle, approve orders or replace the strategy engine. Its fixture generator postdates the baseline manifest.

The offline playbook slice selects a bounded strategy playbook from caller-supplied state and compares complete synthetic decisions with Python. Go compares the correctly rounded binary64 fourth root of the sequentially rounded confidence product against the preservation threshold, with ties to even; Python uses `product ** 0.25`, whose result depends on the platform math library and need not be correctly rounded. Consequently the preservation decision can differ at thresholds between those results: for product `0.8479336249272827`, a Python platform may return `0.959600497389907` while the correctly rounded result is `0.9596004973899072`; at the latter threshold Python preserves capital and Go does not. Fixtures do not establish parity at all floating-point boundaries or across Python platforms. Go rejects unsupported non-finite or overflowing arithmetic even when Python might short-circuit to preservation; callers must treat errors as no decision. Configuration is a complete value and overrides should start from the default constructor. It does not classify market state or replace strategy orchestration. Its fixture generator postdates the baseline manifest.

The offline state dimensions slice classifies volatility, positioning and data quality from caller-supplied indicators and flags, and computes transition risk with synthetic Python reference cases. It requires finite inputs and a bounded directional confidence. Directional classification, hysteresis, indicator extraction and the complete state engine remain in Python. Its fixture generator postdates the baseline manifest.

The loop stability review has a Go CLI, `zenquant review-loop-stability --results-root DIR --output-dir DIR`, which discovers local loop artifacts and writes the JSON and Markdown review. A synthetic stale-running case was compared with the Python reference; Go tests cover completed rounds, missing roots and malformed JSON. The Python script and module remain available; no scheduler or other repository caller was found for this script. This does not run campaigns or replace campaign orchestration. Parity is limited to ordinary well-formed status objects and tested cases; malformed types, path edge cases and all Python formatting and error details have not been established as equivalent. The Go `loop-stability-v2` publication contract intentionally differs from Python: each run creates a new private `loop-stability-v2-*` directory and publishes both files together under `report-v2`; stdout returns their paths. It does not overwrite Python's fixed report files. A failed pre-publication write removes the temporary run directory; a crash may leave an incomplete staging directory, which is not a published report. After publication, a sync error reports the path and unconfirmed durability. Use a trusted local output directory that is not replaced concurrently; pre-create and durably sync it and its ancestors when crash durability is required, because their creation is not synced by this command. A stdout failure reports the published path in the error. The inherited stale classification checks status values only, not process liveness or freshness; relative current-run paths resolve from the command's working directory.

The `zenquant download-deribit-options --currency BTC --output-root DIR --endpoint URL` command fetches one options chain and writes JSON and Parquet using `parquet-go`. The default endpoint is Deribit's public API; use a local fake endpoint and temporary output directory for synthetic verification. The Python harness invokes the Go CLI against a local HTTP fixture and reads both outputs through PyArrow and `OptionsDataStore`. The scheduled options workflow still uses Python. This bounded one-shot adapter is not a collector scheduler, has no retries, and has not been validated against real Deribit responses or production storage; tested synthetic cases do not establish full Python parity. HTTP requests have 15-second timeouts and 16 MiB response limits. It stages both files before publication and refuses same-second filename collisions; the two hard-link publications are not crash-atomic and require a filesystem supporting hard links. A crash or cleanup error can leave one published file or a staging directory; staged Parquet uses a `.tmp` suffix and is excluded from the downstream store's `*.parquet` scan. Output directories must be trusted and not modified concurrently. The new Python test postdates the inventory manifest.

The current product UI and newly revised documentation use English. Historical evidence and archived records retain their original language and content; existing authoritative operational documents remain in force and are not translated by changing archived evidence.

A proposed sequence, with a separately reviewed scope and explicit evidence at each stage:

1. **Baseline and ownership.** Regenerate the manifest on the target revision, compare path coverage and hashes, map console commands, scheduled jobs, configs and dynamic/plugin callers to owners, and classify every file as retained, candidate port or pending investigation. Acceptance: no unowned entrypoint or unexplained tracked file; validate against Git and review dynamic gaps.
2. **Contracts and fixtures.** Freeze versioned input/output schemas, deterministic synthetic fixtures and expected error behavior for one selected, bounded component. Acceptance: Python reference tests and fixture checks pass without production data; preserve timestamps, decimals, point-in-time rules and known edge cases. Document environment and exact revision.
3. **Port one pure component.** Implement an isolated Go equivalent behind an explicit boundary, keeping Python as reference. Acceptance: Go unit tests and Python/Go differential checks on the same fixtures, including failures and ordering; run Go race checks where concurrency applies. No inference of broader parity.
4. **Replay and risk integration.** Port incrementally only after dependencies and contracts are understood. Acceptance: deterministic event-by-event replay, bar-close and point-in-time checks, risk and execution behavior, golden outputs and error cases match on approved synthetic or sanitized fixtures. Investigate every mismatch; do not equate aggregate returns with parity.
5. **Operations and cutover proposal.** Inventory collectors, scheduler, persistence, CLI, UI and recovery paths; verify failure, restart, observability and rollback in isolated environments. Acceptance: owners approve a documented, tested deployment and rollback plan and required CI gates pass before any change to callers. Production or data actions need their own authorization. Retire any Python file only in a later reviewed change after dependency, historical and operational disposition is evidenced.

No training, tuning, production data access, source removal or promotion is required to produce this inventory. [LIVE_VALIDATION_HANDOFF_SPEC.md](LIVE_VALIDATION_HANDOFF_SPEC.md) remains the tester/operator handoff authority.

## Reproduce

From the repository root, with Python 3.11+ and Git, run the following. It reads tracked Python source and `pyproject.toml`, executes no project code, and rewrites only the manifest. Run from a clean checkout at the intended baseline; `source_revision` is `HEAD` and hashes describe working-tree bytes, so tracked Python and `pyproject.toml` must be unchanged from that revision for a consistent snapshot. Review the resulting diff. Paths are sorted and JSON is deterministic; rerunning with the same inputs is byte-for-byte reproducible.

```python
import ast
import hashlib
import json
from pathlib import Path
import subprocess
import tomllib

root = Path(subprocess.check_output(['git', 'rev-parse', '--show-toplevel'], text=True).strip())
revision = subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip()
paths = subprocess.check_output(['git', '-C', str(root), 'ls-files', '-z', '--', '*.py']).decode().split('\0')
config = tomllib.loads((root / 'pyproject.toml').read_text(encoding='utf-8'))
scripts = config.get('project', {}).get('scripts', {})
entries = []
for path in sorted(filter(None, paths)):
    raw = (root / path).read_bytes()
    parts = Path(path).parts
    category = parts[0] if len(parts) > 1 else '(root)'
    package = parts[1] if len(parts) > 2 else category
    module = path[:-3].replace('/', '.')
    if module.endswith('.__init__'):
        module = module[:-9]
    item = {'path': path, 'category': category, 'package': package,
            'sha256': hashlib.sha256(raw).hexdigest(), 'imports': [],
            'main_guard_lines': [], 'main_function_lines': [],
            'console_scripts': sorted(name for name, target in scripts.items()
                                      if target.partition(':')[0] == module),
            'shebang': raw.startswith(b'#!'), 'parse_error': None}
    try:
        tree = ast.parse(raw, filename=path)
        item['main_function_lines'] = [n.lineno for n in tree.body
                                       if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == 'main']
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                item['imports'].extend({'kind': 'import', 'module': a.name, 'names': [], 'line': node.lineno} for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                item['imports'].append({'kind': 'from', 'module': '.' * node.level + (node.module or ''),
                                        'names': [a.name for a in node.names], 'line': node.lineno})
            elif isinstance(node, ast.If) and isinstance(node.test, ast.Compare):
                test = node.test
                if (len(test.ops) == 1 and isinstance(test.ops[0], ast.Eq) and len(test.comparators) == 1
                    and any(isinstance(x, ast.Name) and x.id == '__name__' for x in (test.left, test.comparators[0]))
                    and any(isinstance(x, ast.Constant) and x.value == '__main__' for x in (test.left, test.comparators[0]))):
                    item['main_guard_lines'].append(node.lineno)
        item['imports'].sort(key=lambda x: (x['line'], x['kind'], x['module'], x['names']))
        item['main_guard_lines'].sort()
    except (SyntaxError, UnicodeDecodeError) as exc:
        item['parse_error'] = str(exc)
    entries.append(item)
manifest = {'schema_version': 1, 'source_revision': revision,
            'source': 'git ls-files -- *.py; working-tree contents',
            'files': entries}
(root / 'docs/python-go-inventory.json').write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
```

For example, copy the fenced block into `python3 -` from the repository root. Afterwards compare manifest paths to `git ls-files '*.py'`, check parse errors, review the diff and rerun to check byte-for-byte reproducibility. The generation itself does not run tests or data jobs.
