# Rust 模拟意图准入与幂等记录验收

本轮完成第三个最小模块：显式文件意图的版本化准入、幂等持久记录和重启重放。状态只到 `RECORDED_UNASSESSED`；没有风险批准、资金预留、订单或成交。诊断结果不能直接进入新入口，也没有自动转换器。

## 基线和改动

云端工作目录 `/workspace/zenQuant`，工作分支 `codex/options-rust-iteration-1`。开始时工作区干净，基线是 `f58e98f65d1be52f8743ed2cefbd80bacba19d14`，其 [CI 38136234124](https://github.com/infdahai/zenQuant/actions/runs/38136234124) 已通过。开始前重跑全部 Rust 测试为 101 passed。仓库 `AGENTS.md`、`CLAUDE.md` 和相关交付指引已核对；仓库没有 `.agents/skills`，本模块没有引入策略研究或实盘 promotion 路径。

代码分为：

| 部分 | 实现与边界 |
|---|---|
| `options-core::intents` | 独立 `simulated-trade-intent.rust.v1` 严格契约；纯函数校验模拟身份、稳定 ID、账户/来源、时序、规格、数量、限价 |
| `options-workbench::intent_store` | `simulated-intent-store.rust.v1` 有界完整快照、全量校验、目录锁、原子替换；无订单状态机 |
| `simulated-intents` 二进制 | init / record / replay，规范 JSON receipt，精确错误码；旧 options-workbench 保持默认运行程序 |
| 原有边界复用 | 仅把已有 tick、descriptor-relative directory/open_dir/same_parent 辅助函数开放到 crate 内；原经济模型和报告发布行为不变 |

细粒度代码提交：`7b96025` 契约，`999f22b` 存储，`e8cae41` CLI，`a963dbf` 原生/集成/恢复测试，`91ed512` 移除测试模块注册后多出的末尾空行。最终受测 Rust 树为 `91ed51214e390fbea6af54b5d70f6dc75fc83615`；随后独立提交 CI 与文档，不改变 Rust 代码。最终提交 SHA 和对应 CI run 由交付消息及本分支 Actions 记录核对。

## 设计与选择理由

当前只接收一条限价 BUY、线性 European option 请求；金额和数量使用原有精确 Dec，拒绝不合法 tick/lot、超精度和未知字段。`mode=SIMULATED` 与 `simulated_account_id` 明确隔离真实账户；`source.kind=MANUAL/ALGORITHM` 与产品独立，两者都不增加权限。请求含完整 spec/descriptor 绑定、来源版本/证据 hash 和声明的时间关系；不读取当前时钟、不认证来源，不代表此刻可交易。

一个 request ID 在整份 store 中唯一。同 ID 完整 typed 语义相等则返回原记录，保留首次原始字节 hash；JSON 空白、键顺序、等价十进制表示不产生新记录。同 ID 换账户、来源、价格或其他字段拒绝。不同 store 不做全局去重。sequence 只表示存储提交顺序，不是市场事件或账本顺序。

持久化选用 `snapshot.json` 全量有序记录加 checksum，以及一个永不作为权威记录的 `pending.json`。每次操作校验整个正式快照；新记录在内存构造后写 pending，file fsync、同目录原子 rename、directory fsync，再确认成功。容量为最多 1024 条或 16 MiB，不自动轮转、删除、缓存或维护索引。请求本身最多 64 KiB。

真实问题是防止重复请求、竞争覆盖以及半截状态成为权威记录；完整快照和内核锁已足够解决本轮有界模拟工作量。代价是 O(n) 全量校验/重写，当前没有性能达标声明。未实现 SQLite/WAL 备选再做消融，也没有引入数据库服务、事件总线或通用存储接口。只有实际容量/延迟/备份需求超出边界时，才值得用测量推动成熟存储迁移。

目录 `flock(LOCK_EX|LOCK_NB)` 覆盖读取、校验、去重和提交；record 与 replay 都取独占锁。支持同一 Linux 主机上遵循协议的多进程调用，竞争者返回可重试 `STORE_BUSY`；没有“假设不会并发”的隐藏前提。测试在真实进程中验证了相同 ID 与不同 ID 竞争。锁由内核在进程退出时释放，不使用 PID 文件。仅声明支持本地、实现所需 flock/rename/fsync 语义的文件系统；NFS/SMB、跨主机共享、外部改写或操作中替换目录不在支持范围。

每份 receipt 都固定 `risk_assessment=NOT_ASSESSED`、账户/执行 `UNVERIFIED`、`execution_authority=NONE`、`funds_reserved=false`。hash 是身份与损坏检测，不能变成账户授权、真实来源认证或金融风控。

## 恢复和失败实验

这些实验使用数学 fixture、Linux 子进程和实际文件 IO，没有交易账户或市场盈利假设。

| 实验 | 观察和断言 |
|---|---|
| 同 ID 同语义重试，含空白和 2.000 表示 | 只有一条记录；返回原记录和首次字节 hash；快照字节及 inode 不变 |
| 同 ID 换账户/价格/来源 | `INTENT_ID_CONFLICT`；正式状态不变 |
| 无效请求、LIVE 模式、诊断请求、非法参数 | 拒绝，不能初始化或改变正式状态 |
| 8 个实际进程提交相同 ID | 恰好一次 RECORDED；其余为已记录或 STORE_BUSY；重放 count=1 |
| 8 个不同 ID 的实际进程竞争并有限重试 | 无覆盖丢失；加上原记录 count=9，序号连续 |
| 子进程持锁时再读写 | 都返回 STORE_BUSY；SIGKILL 持锁者后可继续写入 |
| SIGKILL：temp_created / file_synced | 重启 count=1，旧字节不变；原 ID 重试 RECORDED，最终 count=2 |
| SIGKILL：renamed / directory_synced | 重启 count=2；原 ID 重试 ALREADY_RECORDED，最终仍 count=2 |
| RLIMIT_FSIZE=128 且忽略 SIGXFSZ | 真实系统调用产生 128 字节半截 pending；写入失败、旧快照不变；重试正常提交一次 |
| file sync/rename 前后边界注入失败 | 提交前返回写入失败；提交后返回持久性未确认；重放和原 ID 重试没有重复记录 |
| stdout 管道无读者造成真实 EPIPE | CLI exit 1 / STORE_DURABILITY_UNCONFIRMED；状态已提交；原 ID 重试 ALREADY_RECORDED，count=1 |
| 正式文件缺失、截断、checksum/序号/ID/意图 hash 错误 | 读取和追加都安全拒绝，不自动重置或覆盖损坏字节 |
| 有效正式快照旁存在截断 pending | 只重放正式状态并标记 uncommitted_pending；下一次合法新记录才清理临时文件 |
| 正式快照损坏但 pending 完整 | 仍拒绝，不猜测哪个文件应成为权威 |
| 初始化中途 SIGKILL | 没有正式快照时，replay/record/再次 init 均拒绝，不能悄悄变成空存储 |
| 上限、符号/硬链接、请求与存储重叠、封存根目录 | 拒绝且不改变原正式状态或受保护来源 |

恢复边界的可复现文本在 `recovery.log`；集成测试名称和结果在 `cli-first.log` / `final-test.log`。生产程序没有故障注入开关，子进程控制只在测试二进制内。

必须区分保证：已验证的是**进程被杀、内核仍运行**时的旧/新完整快照和幂等重试。实际执行 file/directory fsync，但未实测断电、机器重启、故障设备或存储缓存丢失；不宣称断电零丢失。没有副本、备份恢复或远端提交锚点，主机/磁盘丢失不在保证范围。checksum 不能识别整体回滚到旧但合法的快照，不能对抗主动改写并重算 hash。目录及文件需要操作者按协议管理。

## 全部验收命令与结果

Cargo 命令从 `rust/` 执行。云端显式设置 `PATH=/workspace/.zenquant-cargo/bin:$PATH`、`CARGO_HOME=/workspace/.zenquant-cargo`、`RUSTUP_HOME=/workspace/.zenquant-rustup`，工具链为固定 1.90.0。

| 命令 | 结果 |
|---|---|
| `cargo test --workspace --locked`（基线） | 101 passed，baseline.log |
| `cargo test --locked -p options-core --test intents` | 4 passed，core-first.log |
| `cargo test --locked -p options-workbench --lib intent_store::tests -- --nocapture` | 11 passed，store-first.log / recovery.log；其中一个 test 是子进程 worker 入口 |
| `cargo clippy --workspace --all-targets --locked -- -D warnings`（初版） | 通过，clippy-first.log |
| `cargo test --workspace --locked`（初版） | 123 passed，implementation-test.log |
| `cargo test --locked -p options-workbench --test intent_cli` | 7 passed，cli-first.log |
| `cargo fmt --all --check` | 最终通过，final-fmt.log；细粒度提交时产生的一个末尾空行曾失败，fmt-first-failure.log 保留记录，91ed512 修正后复跑通过 |
| `cargo clippy --workspace --all-targets --locked -- -D warnings`（最终） | 通过，final-clippy.log |
| `cargo test --workspace --locked`（最终） | **124 passed / 0 failed / 0 ignored**，final-test.log；包含 34 个 CLI 集成 tests、11 个意图存储 tests、9 个原发布/封存 tests |
| `cargo build --workspace --locked` | 通过，build.log |
| `cargo run --locked -p options-workbench -- --help` | 原默认诊断二进制保持可用，default-binary.log |
| `CARGO_TARGET_DIR=/workspace/zenquant-intent-validation/clean-target cargo build --workspace --locked --offline` | 全新目标目录、缓存的锁定依赖离线构建通过，clean-build.log |
| `git diff --check` | 通过 |
| 在所有验收目录执行 `sha256sum --check SHA256SUMS` | 本地与 CI 检查归档完整性 |

无外部程序 smoke 的完整命令（从仓库根执行，父目录需已存在，store 需尚不存在）：

```sh
env PATH=/nonexistent rust/target/debug/simulated-intents init --store-dir /workspace/zenquant-intent-validation/smoke-store --store-id cloud-simulated
env PATH=/nonexistent rust/target/debug/simulated-intents record --store-dir /workspace/zenquant-intent-validation/smoke-store --request-file rust/options-workbench/fixtures/simulated-intent.json
env PATH=/nonexistent rust/target/debug/simulated-intents record --store-dir /workspace/zenquant-intent-validation/smoke-store --request-file rust/options-workbench/fixtures/simulated-intent.json
env PATH=/nonexistent rust/target/debug/simulated-intents replay --store-dir /workspace/zenquant-intent-validation/smoke-store
```

四次均 exit 0，分别 INITIALIZED/RECORDED/ALREADY_RECORDED/REPLAYED，record_count 为 0/1/1/1。回执 `init.json` / `record.json` / `retry.json` / `replay.json` 与正式 `snapshot.json` 已归档。将可执行文件替换为 clean-target 的构建、目标改为 clean-smoke-store 后，init/record/replay 回执和快照四次 `cmp` 均逐字节一致。这里验证产物重放，不声称可执行文件二进制可复现或性能达标。

## 文件和兼容证据

```text
rust/options-core/src/intents.rs
rust/options-core/src/lib.rs
rust/options-core/src/costs.rs                         # tick 仅改变 crate 内可见性
rust/options-core/tests/intents.rs
rust/options-workbench/src/intent_store.rs
rust/options-workbench/src/intent_store_tests.rs
rust/options-workbench/src/bin/simulated-intents.rs
rust/options-workbench/src/lib.rs
rust/options-workbench/src/publication.rs              # 复用目录辅助函数，仅改可见性/注释
rust/options-workbench/Cargo.toml                      # 显式保留 default-run
rust/options-workbench/fixtures/simulated-intent.json
rust/options-workbench/tests/intent_cli.rs
rust/options-workbench/tests/terminal_cli.rs            # 新增第二模块完整报告 golden
.github/workflows/rust.yml
docs/SIMULATED_INTENTS_RUST_GUIDE.md
docs/OPTIONS_WORKBENCH_RUST_GUIDE.md
docs/reports/simulated-intents-20261011/*
```

旧 v1 的 View 必填、未知字段拒绝和三份报告 golden 继续通过；第一模块报价报告 golden 通过；新增第二模块完整到期报告 golden（仅规范化源码身份）通过。原经济公式、io、三个诊断模式 main/report、Cargo.lock、旧 Python/策略代码未改变。没有引入新 crate 或包依赖。

日志归档只裁掉末尾多余空行，所有字节由 SHA256SUMS 绑定；日志显式纳入版本控制，不受全局 `*.log` 忽略影响。CI 执行归档检查、fmt/clippy/test/build、两个诊断 smoke 和模拟意图 init/record/retry/replay，覆盖本指南及验收文档提交。原始实验目录为 `/workspace/zenquant-intent-validation/`。

完整行为与操作边界见 [使用指南](../../SIMULATED_INTENTS_RUST_GUIDE.md)。未实现风控预留、订单状态机、模拟撮合、成交、账本、费用记账、指标、策略、真实账户适配或分布式恢复；未运行 Python 门禁、真实封存 panel、历史回测、多 OS 或性能基准。没有真实账户/密钥/订单、PR、合并或部署。本模块没有需要协调的阻塞。

下一最小步骤建议：在显式模拟资金快照上实现幂等风控预留决策，用 intent hash/请求 ID 绑定来源，验证可用余额与预留不变量及重启恢复；不将当前记录状态直接升级为通过，也不同时建设完整 OMS。
