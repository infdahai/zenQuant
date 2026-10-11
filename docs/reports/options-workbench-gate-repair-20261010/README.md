# Rust 期权工作台：全仓集成门禁修复

**本地 `zenquant-check` 的 0/A/B/C/D/E/F 全部通过，退出码 0，耗时 353.1 秒。** 修复代码范围 `a2b2086..8751787`；分支 `codex/options-workbench-rust` 和隔离目录 `.worktrees/options-workbench-rust` 保留。没有合并、推送、部署或交易。

## 改动与验证

| 项目 | 修复 | 实际证据 |
|---|---|---|
| A | 仅格式化三个旧文件 | Ruff lint/format 通过；三个文件 AST 未变 |
| E | 把原 `pytest_addoption` 移到根 `tests/conftest.py` | 根/定向发现 × portable/strict 的真实 CLI 回归；全 Gate E 1851 通过、5 跳过、34 subtests 通过 |
| F | 空窗口、任一既有 `OVERFIT` 返回 1，CLI 传递退出码 | 先红 2 失败/2 通过，再绿 9 通过（含 split tests）；真实历史四个窗口通过 |
| Rust | 没有更改 Rust 代码或协议 | 70 测试通过；fmt 和所有 targets 的 Clippy `-D warnings` 通过 |

独立 reviewer 未发现 Critical/Important；另行执行注册/F/panel 测试得到 53 通过、1 跳过。旧固定 P1 panel/scout 根未复制到当前 worktree，portable 本地源测试允许 skip；严格 flag 和坏 seal 的失败策略没有放宽。全仓测试仍有 5 项 skip，不能将其表述为每项集成都已执行。

F 的结果来自现有 BTC 回放、默认配置和独立历史输入，**不是期权策略收益验证**。下表来自日志四舍五入输出；不是新的 train-only 搜索、最终 2026 test 或 paper/live 验收。

| 窗口 | Rolling train | Dev OOS | Train Sharpe | Dev Sharpe | 既有判断 |
|---|---|---|---:|---:|---|
| wf_1 | 2022-01 → 2024-01 | 2024-01 → 2024-07 | 2.19 | 2.05 | OK |
| wf_2 | 2022-07 → 2024-07 | 2024-07 → 2025-01 | 1.81 | 2.79 | ROBUST |
| wf_3 | 2023-01 → 2025-01 | 2025-01 → 2025-07 | 2.13 | 1.45 | OK |
| wf_4 | 2023-07 → 2025-07 | 2025-07 → 2026-01 | 1.76 | 2.82 | ROBUST |

后续 rolling train 会包含更早的 dev，只能用于原门禁诊断；开发样本平均 Sharpe 2.28、OVERFIT 0/4 不改变既有 `BLOCKED_PAUSE_PROMOTION_AND_TRAINING`。期权实际 PnL、Sharpe、容量、成交和生命周期套利收益仍未知。

## 历史输入与保护

快照为 **2945 文件、164576023 bytes（156.95 MiB）**，按 replay 现有相对路径独立复制到本 worktree `data/`。每项长度和 SHA-256、源/副本不同 inode、无符号链接、仅副本只读均经独立复核。没有网络下载、共享目录写入口或修改来源权限。

- 四个窗口各自 train/dev 的 BTC 4h、1d，共 16 段：数量与理论 bar 数一致，close timestamp 无重复。此项不是全部字段的质量认证。
- OI 从 2023 年开始，2022 年训练段缺失保留；默认 Binance aggregated liquidation 与 5m aggtrades 路径原本不存在，未用其他来源替代。
- Deribit 只覆盖 2026，不与 2025 OOS 重合；其中一个无 snapshot 列的文件由原 loader 跳过。仍复制整个默认 options 输入，保持 `has_options`/`formula_hash` 语义。没有激活 OptionsState。
- 原目录 HEAD/分支、106 个脏路径及 status 文本与本轮基线一致；110 个保护文件、23 个旧 P1 文件、2945 个历史来源和副本 SHA 均未改变。
- 原目录尚未提交的 replay/risk 改动没有进入该分支；本结果不声称与脏目录收益一致。F 失败退出逻辑与原目录已有未提交修复一致，在隔离分支先红绿验证后单独提交。

## 复跑与维护

在保留的 worktree 根目录执行：

```sh
PYTHONPATH=. /Users/disaster/workspace/code/zenQuant/.venv/bin/python -m zenquant.app.check
cargo test --manifest-path rust/Cargo.toml --workspace
cargo fmt --manifest-path rust/Cargo.toml --all -- --check
cargo clippy --manifest-path rust/Cargo.toml --workspace --all-targets -- -D warnings
```

仅验证这轮回归：

```sh
PYTHONPATH=. /Users/disaster/workspace/code/zenQuant/.venv/bin/python -m pytest -q \
  tests/test_options_pytest_registration.py tests/test_walk_forward_gate.py \
  tests/test_split_discipline.py tests/options/test_panel.py
```

换机器须提供同等历史输入，不能把缺数据的 F 当作通过。`data_snapshot_manifest.json` 给出全部相对路径、长度、SHA、原始根、复制根和缺失 patterns；所有 Parquet 仍是忽略的本地输入。恢复时按该清单逐项独立复制、核对两端 hashes、仅将副本 chmod 只读，禁止用 hardlink 或写穿共享目录替代。当前旧 P1 strict test 需要其固定根及 seal 均存在于 loader 指定路径；本轮不改这项准入。

`tests/conftest.py` 负责早期选项注册；`tests/options/conftest.py` 保持 source policy。`scripts/walk_forward_eval.py` 负责评价与失败退出；策略、门禁阈值、配置和 golden 未改变。后续修改失败策略应扩展 `tests/test_walk_forward_gate.py` 并再次执行统一门禁。

## 判断与余项

1. 沿用已授权的并行方式：E agent 修复、F agent 审计，root 实现 A/F；不重复请求同一修复批准。误判成本是审阅或回退隔离提交。
2. 保留旧 F 阈值、非有限数值处理及固定六个月窗口行为。本轮不扩大成策略审计；门禁不能筛掉所有无效 metric 或非默认窗口请求。`--test-months` 当前只影响打印，默认 Gate F 固定 6 个月。
3. 原目录 replay/risk 改动只验证保护，不自动吸收到该分支；后续集成仍需单独审查这些改动。
4. Rust 没有本轮代码变更，70 测试用于既有合同回归，不新增生产适用性承诺；到期前交易、动态对冲、保证金/融资/交割、Linux 原生运行仍未验收。
5. 工程门禁不改变盈利与 promotion/OptionsState/paper/live 权限；将诊断当交易批准会增加资本风险。

非阻断余项：补充 train/test 构造或 `run()` 抛异常的 CLI 回归。当前异常实际外抛、之前真实缺数据运行返回失败，但这轮新增参数化只锁定正常返回结果的判定。

[验收状态](acceptance.json)、[完整门禁日志](zenquant-check.log)、[数据清单](data_snapshot_manifest.json)、[核心 bars 覆盖](bar_coverage.json)、[原目录保护](preservation.json)、[独立评审](review.md)、[执行记录](progress.md) 均在本目录；[archive-manifest.json](archive-manifest.json) 绑定归档内容。旧 Rust D2 实现/数学验证记录仍见 [原报告](../options-workbench-rust-20261010/README.md)。

归档文本日志仅去除行尾空白及末尾多余空行；原始运行日志仍保留在 results 目录，manifest 记录两侧摘要。
