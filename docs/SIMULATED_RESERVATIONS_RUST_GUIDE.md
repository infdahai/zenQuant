# Rust 模拟资金与现金预留

**历史文档：**Rust 源码和独立预留 CLI 正从当前工作树移除；下方命令、路径和测试仅记录历史实现。当前 Go 手工 paper 工作流不提供等价现金预留、订单或成交功能，参见 [Go 运维说明](GO_OPERATIONS.md)。

本模块把显式模拟资金初值和幂等预留纳入同一权威快照。仅处理单条线性 European BUY 限价、全额现金支付且所有货币相同的期权开仓权利金与手续费上界。它不查询真实账户、不生成订单、不模拟成交，也不是完整账户或组合风控。

模块职责与允许依赖见 [Rust 架构边界](RUST_MODULE_BOUNDARIES.md)。资金注册、幂等业务结果、余额准入和状态转移由 core 的私有字段 `Authority` 统一持有；workbench 只适配读取、时钟、锁、提交和输出。

旧意图记录仍为 `RECORDED_UNASSESSED`。预留请求须显式包含完整意图、意图 hash、资金 hash 和资金归属；旧 receipt、诊断报告、人工或算法来源都不能充当批准。两者没有跨文件事务：预留权威内嵌完整输入，自行校验，不更新旧意图存储。

## 运行与唯一权威

从仓库 `rust/` 目录运行，Rust 1.90.0，无 Python/Go：

```sh
cargo build --workspace --locked
cargo run --locked -p options-workbench --example reservation_fixture -- /tmp/new-reservation-inputs
env PATH=/nonexistent ./target/debug/simulated-reservations init
env PATH=/nonexistent ./target/debug/simulated-reservations fund --request-file /tmp/new-reservation-inputs/funding.json
env PATH=/nonexistent ./target/debug/simulated-reservations reserve --request-file /tmp/new-reservation-inputs/request.json
env PATH=/nonexistent ./target/debug/simulated-reservations reserve --request-file /tmp/new-reservation-inputs/request.json
env PATH=/nonexistent ./target/debug/simulated-reservations replay
```

示例明确创建数学模拟账户，初值 1；输入的实际生成时间起有效一小时。它不是行情、交易所费率或账户证据。示例输出目录必须不存在。`init` 只在新权威上运行一次，不得删除现有权威来重复注资；现有权威使用 replay 检查。归档样例会过期，不应修改时间来假装原证据仍有效。

权威固定为构建源码仓库根目录下 `.simulated-reservations/`，与启动时工作目录无关。复用仓库根解析：同一普通仓库及其 linked worktrees 指向共同仓库根。CLI 没有 `--store-dir`、账户映射、时钟覆盖或初始化覆盖开关。编译路径是当前原型的部署边界，尚不支持任意搬移二进制。独立 clone/主机是独立模拟实例，没有跨实例真实账户去重保证。

仅支持 Linux、本地文件系统、遵循目录锁的进程和操作期间稳定的目录。要求 `flock`、同目录原子 rename、file/directory fsync；不支持 NFS/SMB、共享多主机写入或外部工具改写状态。拒绝 symlink、非普通状态文件、多重硬链接及受保护封存根目录。源请求文件不能处于权威目录内。状态目录 0700、文件 0600。

## 输入与资金归属

所有金额、数量和比例均为精确十进制字符串，沿用有界 `Dec`。拒绝未知字段、重复键、浮点、超精度、尾随 JSON；每个输入最多 128 KiB。没有隐式补齐账户、币种、余额或费用。

| 契约 | 内容 |
|---|---|
| `simulated-funding-snapshot.rust.v1` | `mode=SIMULATED`、snapshot ID、owner、非负 initial_balance、available_ns、source_ref/source_sha256、fee_cap、正 max_spec_age_ns |
| `simulated-premium-fee-cap.rust.v1` | 正 premium_fraction、known_ns、valid_from_ns、排他 valid_until_ns、source_ref/source_sha256；声明此域全部开仓费用不超过权利金乘该比例 |
| `simulated-reservation-request.rust.v1` | `mode=SIMULATED`、owner、funding_sha256、intent_sha256、完整原 v1 意图 |

`owner` 的五元组是 venue、simulated_account_id、subaccount、collateral_domain、currency。venue/currency 必须规范大写 ASCII ID；模拟账户 ID 是区分大小写的显式不透明标识。当前 subaccount 只有 `MAIN`，collateral_domain 只有 `FULLY_PAID_SINGLE_CURRENCY_CASH`，不允许任意文本切分同一资金池。输入者仍负责真实世界标签身份；本模块不验证真实账户或别名。

同一 owner 的初值和费用政策只允许首次注册；完全相同输入重试返回 `ALREADY_FUNDED`。换 snapshot ID、金额或政策再次注入同一 owner 一律冲突。不同 option、不同人工/算法来源共享该 owner 的资金池。不同显式模拟账户各有独立初值，不跨币种净额。当前最多 16 个 owner、1024 条预留及 16 MiB 快照，以先到的限制为准；不滚动、不删除旧预留。

source_ref/hash 是有身份标识的**模拟声明**，要求非空引用及非零合法 hash，但不会自动读取、认证或联网验证其来源。费用上界必须显式声明覆盖该域全部开仓费用，不能把任意行情的估计手续费或缺失/零费用当作保证。当前没有真实交易所证据适配器，因此 receipt 明确标注 `DECLARED_SIMULATION_BOUND_NOT_VENUE_VERIFIED`。真实费率、最低收费、附加费用若无法证明被覆盖，不应将此模拟政策用于真实账户。

## 精确成本与当前时点

对本轮支持的产品：

```text
QuotePerBase: priced_quantity = 原生数量换算后的 base 数量
QuotePerContract: priced_quantity = 原生合约数量
premium_bound = limit_price_native × priced_quantity
opening_fee_bound = premium_bound × premium_fraction
reserve_total = premium_bound + opening_fee_bound
initial_balance = available + sum(ACTIVE.reserve_total)
available >= 0
```

换算沿用计量底座的 quantity unit、multiplier、lot/step、tick 校验，不引入浮点或 epsilon。预留结果若超出当前 v1 持久金额解码精度，在准入阶段返回 NumericalLimitation，防止写入后无法精确重放。示例 `2 × 0.01 = 0.02`，费用上界 `0.02 × 0.125 = 0.0025`，总预留 `0.0225`。只接收 `LinearCash` 且 quote/strike/settlement/margin currency 全部等于资金 currency；`LinearViaFuture` 因尚无到期仓位与保证金生命周期而拒绝。此上界不覆盖到期手续费、行权后其他负债、跨币种保证金或组合风险。

新预留取得独占锁、完整验证已提交状态后，读取真实 `SystemTime`。测试可注入时钟，生产 CLI 不允许输入评估时间。当前必须满足：

- 资金 available_ns 不晚于当前时点；费用 known/from 不晚于当前，当前早于 until，且费用有效期覆盖整个意图有效期。
- created ≤ 当前 < expires，当前早于合约到期。
- 规格必须有明确 valid_until；当前位于规格有效期内，metadata_known 不晚于当前且年龄不超过资金政策 max_spec_age_ns；原意图仍须满足规格覆盖其有效期等因果约束。
- 当前不早于上次已提交的准入时点。时钟回拨拒绝新写入；尚无网络授时或时钟可信认证。

这些是**锁内准入时点**的校验，不构成以后执行时的授权。文件同步耗时期间不重新赋予更长有效期，后续执行必须再次验证时间和证据。相同 ID 的重试返回历史 ACTIVE 记录，不重新批准、延期或重复扣款。replay 同样重放历史准入事实，不以当前过期为由释放资金。

## 单次提交、幂等与恢复

唯一权威 `snapshot.json` 包含资金声明和完整 ACTIVE 预留；available 不单独落盘。每次操作用阻塞独占目录锁串行化，同机多个进程和重启后均读取这一份状态，没有依赖内存余额、缓存或进程本地锁。ID 在整个权威内唯一，同 ID 完整 typed 请求相等则幂等，任何语义字段改变则冲突。保存规范请求 hash 和首次原始请求字节 hash。

操作顺序：解码 → 取锁 → 完整校验快照、hash、序号、历史准入和余额守恒 → 读取实际时钟 → 查幂等/资金并计算 → 构造新快照 → 写 pending → file fsync → 同目录 rename → directory fsync → 回执。旧快照不含半条预留，新快照同时包含预留及其资金占用。`pending.json` 永远不自行升级为权威。

| 场景 | 处理 |
|---|---|
| 无效输入、资金不足、缺少资金、过期、未知费用/规格、冲突、容量满 | 拒绝；已提交资金及预留不变 |
| rename 前写入/同步失败或进程终止 | 已提交旧快照保留；可能遗留 pending |
| rename 后同步或回执失败 | 返回持久性不确定；状态可能已提交，必须原 ID 重试或 replay，不能换 ID |
| 正式状态损坏/缺失，即使旁边有完整 pending | 拒绝；不重置、不猜测恢复、不覆盖损坏状态 |
| 历史请求过期 | ACTIVE 仍保留，无 TTL、取消、成交或自动释放路径 |
| 首次 init 中断 | 不把残缺目录当空账户；保留核对，本轮无自动修复 |

快照含 SHA256，load 还验证规范字节、每条输入及成本、资金归属、唯一 ID、连续序号和守恒。hash 是完整性检查，不是签名；无法识别重算 hash 或整体回滚到旧的合法快照。SIGKILL 与短写实验验证了内核持续运行时的进程恢复；没有实测断电、磁盘故障、主机丢失或备份恢复。阻塞锁没有公平性或最大等待时长承诺；进程退出释放锁。共享锁封装显式 unlock，避免并行 fork 暂时继承描述符延长锁。

所有 receipt 明确返回 `SIMULATED`、`SIMULATED_OPENING_PREMIUM_AND_FEE_ONLY`、`execution_authority=NONE`、`account_evidence=UNVERIFIED_NO_REAL_BALANCE_SYNC`。退出码与基础 IO 保持兼容：拒绝通常为 2，写入失败或持久性不确定为 1；stderr 不回显原输入。回执丢失不等于未提交。

## 验证、测量与保留标准

```sh
cargo fmt --all -- --check
cargo clippy --workspace --all-targets --all-features --locked -- -D warnings
cargo test --workspace --all-features --locked
cargo run --release --locked -p options-workbench --features reservation-bench \
  --example reservation_baseline -- /workspace/new-reservation-benchmark
```

性能例子是显式 opt-in 开发工具，在新目录创建隔离的数学模拟权威；生产 CLI 没有路径覆盖。四次实际预热后测 32 次操作，三个起始容量 8/512/960，各测 1/2 个线程；接近上限档结束于 992。计时从读请求文件到完整回执 JSON 编码，包括锁等待、历史全量校验、精确计算、全量序列化及实际 fsync；不计进程启动、stdout 传输、初始种子数据生成。种子快照一次写入，但先做全量语义校验；测量路径没有省略持久化。

本轮实测与 155 项原生测试证据见 [验收报告](reports/simulated-reservations-20261011/README.md)。收口后近容量单线程 p50 约 267 ms，双线程约 657 ms，整份快照成本明显，不能宣称高吞吐；32 样本的 p99 就是最大值，不能当 SLA。正确性通过与性能是否满足未来负载是两件事。

| 机制 | 当前必要性与保留标准 |
|---|---|
| 单权威、锁、精确数、全量校验、原子替换与 fsync | 保护唯一预算、幂等、守恒与故障恢复；不可通过消融削弱 |
| 当前时钟与版本化严格输入 | 阻止用历史声明冒充当前准入；必须保留 |
| 直接全量快照 | 最简单可审计基线；容量和延迟可接受才保留，实测已暴露增长成本 |
| opt-in 性能工具 | 可复现真实持久路径成本；不进入生产依赖，不提供权威路径覆盖 |

没有新增缓存、并发余额计算、WAL、数据库、异步任务、通用账本/DSL 或框架接口，也没有为了消融先实现复杂替代。共享 IO 只复用两个真实存储已经需要的安全文件操作。下一阶段先明确实际目标负载和接受阈值；只有容量/延迟超出要求时，才在同一业务契约与故障测试下比较持久实现。本轮不替换存储或扩建交易功能。

本轮明确不实现真实余额同步、费用来源认证、多币种/组合保证金、资金补充或政策更新、撤销/释放、订单、成交、精确成交账本、真实执行或完整到期生命周期。旧 v1 View 必填及严格未知字段、独立期权诊断和原模拟意图契约均保留。
