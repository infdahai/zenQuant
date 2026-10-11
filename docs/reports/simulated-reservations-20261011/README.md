# 第四模块：模拟资金与幂等预留验收

2026-10-11，云工作区 `/workspace/zenQuant`，开发分支 `codex/options-rust-iteration-1`。没有使用用户本机 CLI，没有 Python/Go 构建或运行依赖，没有真实账户、订单、PR、合并或部署。

第三模块基线：`0d3791e85cb785a87b386a4192e3eea542c2314a`。第四模块首次实现检查点：`e1107bb996f3d47eac00ebb19940dae37b6b0533`，架构评审期间暂停推送；评审通过后只完成共享计量规则归位、纯资金内核提取及相关正确性验证。最终 Rust 源码/测试提交：`8583b646ea6331132031fa9d10f4b230f0cab0f8`，其后是文档、证据及 CI 路径配置提交。最终推送 SHA 和精确 SHA 的远端 CI 结果由交付回执记录，不能用本地通过替代远端验收。

## 最小行为与架构

只接受已支持的单条线性 European BUY 限价中 `LinearCash`、全额支付且四种货币与 owner currency 一致的子集。原生数量与价格按单位精确计算权利金，另加显式声明的全部开仓手续费上界。via-future、未知费用、规格有效期不明、过期/陈旧证据均不能取得新预留。

资金 owner 为 venue、模拟账户、MAIN 子账户、FULLY_PAID_SINGLE_CURRENCY_CASH 抵押域、currency。同仓库固定 `.simulated-reservations` 权威，多个请求来源和产品共享各自 owner 的预算。资金初值不可重新注入；相同声明幂等，变更声明冲突。独立 clone/主机属于独立模拟实例，当前没有真实账户身份映射或跨实例协调。

初值与全部 ACTIVE 记录在一个快照中提交，可用余额派生，严格满足 `initial = available + active_reserved`、`available >= 0`。锁在整个读取、判断、提交期间持有；实际准入时钟在取得锁和完整验证历史后读取。历史重试不重复扣减、不延期、不释放。无 TTL、撤销或自动释放。

保留两个 crate，四个逻辑职责及禁止项见 [模块边界](../../RUST_MODULE_BOUNDARIES.md)：

- `measurement` 统一规格、数量、tick 规则；交易内核不再导入诊断 `costs` 或旧 `inputs`。
- core `Authority` 用私有状态承载资金注册、幂等结果、余额准入和纯状态转移。状态反序列化时重验历史业务不变量；适配器不能直接改余额或追加记录。
- workbench 负责实际时钟、文件边界、锁、原子提交和回执。`snapshot_file` 只复用两个真实存储需要的安全 IO，不是通用 Repository 框架。
- 意图 store 仍为可选未评估输入归档；core 比较语义身份，归档序号/首次字节 hash/容量留在存储。它不承担资金状态，也不与预留双写。

完整操作说明、费用声明边界和错误处理见 [运行指南](../../SIMULATED_RESERVATIONS_RUST_GUIDE.md)。输出继续声明 `execution_authority=NONE`、真实账户 `UNVERIFIED_NO_REAL_BALANCE_SYNC`，费用为 `DECLARED_SIMULATION_BOUND_NOT_VENUE_VERIFIED`。来源 hash 不等于真实费率认证。

## 验证结果

最终 **155 项 Rust 测试通过**，fmt/clippy/build 通过；没有忽略的测试。原 v1 View 必填、未知字段拒绝、两个独立分析入口和旧报告黄金回归保持。第三模块四类回执及快照逐字节匹配已发布基线；第四模块提取前的快照与 replay 回执也逐字节兼容。

| 层次 | 实验证据 |
|---|---|
| 精确计量与证据 | base/contract/per-contract 手算、无 epsilon 守恒、费用覆盖、当前时点、未来/陈旧/未知规格拒绝、错误产品/币种/绑定拒绝 |
| 纯业务转移 | fund/reserve 不修改旧状态、同域人工/算法共享预算、同 ID 同输入幂等/不同输入冲突、回拨/过期不新增、不自动释放、反序列化拒绝破坏守恒或历史身份的状态 |
| 唯一权威 | 8 个实际进程不同 ID 争夺仅可容纳两笔的资金：2 成功、6 不足；同 ID：1 新增、7 幂等；不同 option 共用 owner 预算，显式不同模拟账户隔离 |
| 单次提交 | fund 与 reserve 各 6 个文件阶段故障；rename 前保留旧状态，之后返回持久性不确定，原 ID 重试保持守恒 |
| 实际进程恢复 | 4 个阶段 SIGKILL；真实 RLIMIT_FSIZE 造成 128 字节短写；损坏正式快照即使旁有完整 pending 仍拒绝；容量上限不清除 ACTIVE |
| 分层一致性 | 内存纯转移结果、提交后重新加载的 Authority、replay 业务结果相等；旧格式字节兼容 |
| 架构检查 | 原生词法依赖检查、拒绝案例及 Cargo metadata 检查通过；这是显式源依赖门禁，不是完整宏/trait/传递依赖无副作用证明，不等同既有 public_boundary |

基线首跑出现 `Busy` 与预期 `CorruptStore` 不符：并行 fork 可暂时继承同一 open file description，原先仅 close 延后释放 flock。先单独复核，再增加显式 unlock 与继承描述符回归，修复后旧基线 125 项通过。保留 `baseline.log` 的失败记录，不把它写成首次全绿。

收口复核还发现合法输入相乘可能产生超出当前 v1 Dec 解码精度的结果，导致写入后不能读回。`precision-regression-before.log` 先复现错误；修复为 `NumericalLimitation` 准入拒绝，**不舍入、不放宽旧输入精度**；after 日志与最终全量通过。中间编译适配错误和源码检查器误把 `#![...]` 当宏的失败日志也保留，均已修正。

## 命令与日志

本地使用固定 Rust 1.90.0，环境变量：

```sh
export PATH=/workspace/.zenquant-cargo/bin:$PATH
export CARGO_HOME=/workspace/.zenquant-cargo
export RUSTUP_HOME=/workspace/.zenquant-rustup
```

工作目录为仓库根；早期在 `rust/` 运行的命令省略 `--manifest-path rust/Cargo.toml`，作用相同。

| 命令或验证入口 | 结果与归档 |
|---|---|
| `cargo test --manifest-path rust/Cargo.toml --workspace --locked` | 初次旧基线失败：baseline.log；修复后 125 通过：baseline-lock-fix.log |
| `cargo test -p options-workbench --lib --locked`（rust/） | 共享 IO 提取后 21 通过：shared-io-test.log |
| `cargo check --workspace --all-targets --locked`（rust/） | 首次实现通过：check-first.log |
| `cargo test -p options-core --test reservations --locked`（rust/） | 6 项纯准入/成本通过：core-first.log |
| `cargo test -p options-workbench --lib reservation_store --locked -- --nocapture`（同范围恢复测试） | 首轮 11 项恢复/并发通过：reservation-first.log |
| `cargo test --workspace --all-features --locked`（rust/） | 第一阶段 142，补兼容后 145：implementation-test.log、tests-final.log |
| `cargo test --manifest-path rust/Cargo.toml -p options-core --locked` | 计量提取通过：measurement-refactor.log |
| `cargo check --manifest-path rust/Cargo.toml --workspace --all-targets --all-features --locked` | 提取途中旧测试直接访问私有状态未适配：state-extraction-check.log；已修复 |
| `cargo test --manifest-path rust/Cargo.toml --workspace --all-features --locked` | 中间适配/检查器失败：state-extraction-test.log、architecture-tests-first.log；随后 153 通过：architecture-tests.log；最终 **155 通过：tests-closure.log** |
| `cargo test --manifest-path rust/Cargo.toml -p options-core --test cash_state results_outside --locked` | 精度回归先失败、修复后通过：precision-regression-before/after.log |
| `cargo fmt --all --manifest-path rust/Cargo.toml -- --check` | 通过：fmt-closure.log（成功无输出） |
| `cargo clippy --manifest-path rust/Cargo.toml --workspace --all-targets --all-features --locked -- -D warnings` | 通过：clippy-closure.log；早期对应 clippy-first/final.log |
| `cargo build --manifest-path rust/Cargo.toml --workspace --locked` | 通过：build-closure.log |
| `git diff 0d3791e..HEAD --check` 与完整变更检视 | 无空白错误；计量规则、状态转移、适配提交边界、测试及兼容路径已复核 |

原生 CLI 验证使用 `env PATH=/nonexistent`，禁止依赖外部可执行程序：

```sh
cargo run --locked -p options-workbench --example reservation_fixture -- /workspace/zenquant-reservation-validation/smoke-inputs
# 初次 smoke 从 rust/ 执行：
env PATH=/nonexistent ./target/debug/simulated-reservations init
env PATH=/nonexistent ./target/debug/simulated-reservations fund --request-file /workspace/zenquant-reservation-validation/smoke-inputs/funding.json
env PATH=/nonexistent ./target/debug/simulated-reservations reserve --request-file /workspace/zenquant-reservation-validation/smoke-inputs/request.json
# 原请求再次 reserve，随后从 /workspace 用绝对二进制路径 replay。
```

`funding.json`/`request.json` 是原始数学输入；init/fund/reserve/retry/replay 五份 JSON 保存原始回执。初值 1，premium=0.02，fee=0.0025，ACTIVE=0.0225，available=0.9775。收口后重新构建，用原资金/请求重试和不同 cwd replay，三份 `*-closure.json` 均保持快照身份 `97a5826b2e64d2815c7159977ce0180abab44df85e3a7c1e08bc6079c23eb0f1`。正式 snapshot 原始字节未变化，没有删除、重置或再次注资。当前云目录保留该模拟状态。

收口后还以无外部 PATH 执行原 quote-costs 和 terminal-call-scenarios 两个诊断 fixture，分别输出至验证目录的 quote-closure/terminal-closure，均成功。最终 CI 在全新 checkout 中再次生成当前时点的数学输入，执行 init/fund/reserve/retry/不同 cwd replay。

## 性能基线

命令（最终代码）：

```sh
cargo run --manifest-path rust/Cargo.toml --release --locked -p options-workbench \
  --features reservation-bench --example reservation_baseline \
  -- /workspace/zenquant-reservation-validation/perf-closure
```

2026-10-11 13:16:26.063–13:17:01.843 UTC。release，Linux 6.18.44 x86_64，宿主呈现 AMD EPYC 9V74，容器可用并行度 4，CPU quota `400000/100000`，内存上限 16 GiB，`/workspace` 为 overlayfs，底层物理存储未识别/认证。开始前 load average 为 0.09/0.10/0.04，见 hardware-closure.txt。未同时运行其他基准或 cargo 构建/测试；不是隔离裸机实验。

每档 4 次实际预热，然后 **32 样本 × 6 档 = 192 样本**。单一模拟 owner/option，warm cache；2 workers 为同进程两个竞争线程，跨进程正确性另有测试。计时从读取请求文件到完整回执 JSON 编码，包含解码、目录锁取得/等待、历史全量校验、当前时点检查、精确金额、全量序列化、文件及目录 fsync。锁列包含目录打开/取得锁的开销。不含进程启动、stdout 传输或种子准备；种子经纯转移构造后一次提交，实测请求没有省略持久化。

| 起始→结束记录 | workers | 端到端 p50 ms | p95 ms | p99 ms | 锁 p50 ms | 锁 p95 ms | 锁 p99 ms |
|---|---:|---:|---:|---:|---:|---:|---:|
| 8→40 | 1 | 6.545 | 21.833 | 79.778 | 0.016 | 0.017 | 0.019 |
| 8→40 | 2 | 11.903 | 21.504 | 22.448 | 5.902 | 9.737 | 12.466 |
| 512→544 | 1 | 122.497 | 151.265 | 185.379 | 0.017 | 0.023 | 0.039 |
| 512→544 | 2 | 266.333 | 294.579 | 297.938 | 132.383 | 158.252 | 166.925 |
| 960→992 | 1 | 266.583 | 309.282 | 313.930 | 0.017 | 0.040 | 0.052 |
| 960→992 | 2 | 657.146 | 745.599 | 770.528 | 329.497 | 394.002 | 398.467 |

容量上限 1024；最后一档快照从 2,357,891 增到 2,436,537 字节。原始所有延迟、分位数、状态 hash 和余额在 performance.json。nearest-rank：32 样本的 p99 就是最大值，低容量单线程有约 79.8 ms 极值，未删除。没有置信区间、稳定尾延迟或 SLA 证明。

结论：全量验证和重写随记录数增长，竞争会增加等待，**本实现不具备高吞吐验收结论**。没有预先定义并达到的性能目标，也不能把这次小样本当峰值吞吐。提取前另一次 192 样本保存在 performance-before-extraction.json；两个短实验环境并不受控，不据此宣称提取提升性能。此次没有为了消融增加缓存、异步写、索引或数据库；必要锁/精确数/fsync 不能作为可删优化。

## 变更文件与未完成范围

Rust 文件完整清单与 hash 在 source-files.sha256；从仓库根可用 `sha256sum --check docs/reports/simulated-reservations-20261011/source-files.sha256` 核验。前一实现阶段另存 source-files-before-extraction.sha256，属于历史身份，不应对最终源码执行它。

主要改动：core 的 measurement/reservations 两个实现文件、intents 语义身份与既有 costs/inputs 引用调整、数学 fixture；workbench 的 reservation_store/snapshot_file/CLI、旧 intent_store 的具体 IO 复用；新增原生架构/纯资金/适配恢复/CLI/兼容测试、两个 example 和两个提取前 JSON fixture。没有改动 Cargo.lock 或增加依赖。另有 .gitignore、Rust CI、两份旧指南、两份新指南/架构说明及此证据目录。

明确未实现：真实账户余额同步和费率认证、多币种或组合保证金、资金补充/费用政策更新、自动释放/取消、OMS、成交、成交账本、真实执行、完整到期生命周期、数据库替换或服务拆分。费用政策过期后停止新预留，历史 ACTIVE 保留。

未运行：Python 旧门禁（未修改旧系统，不作为新系统验证依赖）、真实封存数据访问/账户验证、断电/主机重启/故障磁盘/备份恢复、跨主机协调、长时间或大规模性能压测。实际 SIGKILL 的内核和文件系统一直存活，不等于断电零丢失；hash 不能防恶意重算或整个合法状态回滚。

没有需要协调的代码阻塞。后续只在新阶段确认目标后推进：先明确实际模拟负载、资金生命周期和接受阈值，再选择最小切片；当前不自动继续 OMS、释放或换存储。

本目录 JSON 均按原始字节保存，未用浮点工具重写金额或纳秒时间。日志仅清理行尾空白和文件尾空行，完整原始工作日志留在 `/workspace/zenquant-reservation-validation/`。SHA256SUMS 覆盖本报告与全部证据（不包含清单自身）。
