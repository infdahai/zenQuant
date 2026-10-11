# Rust 模拟交易意图记录

本模块只记录模拟流程的请求，尚未运行风控、预留资金、生成订单或模拟成交。`RECORDED_UNASSESSED` 表示结构准入与持久记录成功，不是交易授权。诊断文件不能直接作为意图输入；人工或算法都必须另行提供完整的显式请求。

实现仍在原两个 crate 内：`options-core::intents` 负责纯输入校验，`options-workbench::intent_store` 负责有界快照，独立二进制 `simulated-intents` 提供 init / record / replay。没有新增依赖、Python/Go、后台服务、事件总线或 WAL。原 `options-workbench` 仍是默认二进制，旧 v1 和两个独立诊断入口保持兼容。

## 运行

当前存储仅支持 Linux 的本地文件系统、遵循此锁协议的进程，以及操作期间稳定、由操作者独占管理的存储目录。文件系统必须实现 `flock`、同目录原子 rename 和文件/目录 fsync 语义；不支持 NFS/SMB、对象存储、跨主机共享写入或绕过协议修改目录/文件。没有自动识别并认证底层硬件持久性的机制。

从仓库根目录进入 `rust/` 后执行，使用固定 Rust 1.90.0：

```sh
cargo build --workspace --locked
./target/debug/simulated-intents init --store-dir /tmp/zenquant-sim-intents --store-id demo-simulated
./target/debug/simulated-intents record --store-dir /tmp/zenquant-sim-intents \
  --request-file options-workbench/fixtures/simulated-intent.json
./target/debug/simulated-intents record --store-dir /tmp/zenquant-sim-intents \
  --request-file options-workbench/fixtures/simulated-intent.json
./target/debug/simulated-intents replay --store-dir /tmp/zenquant-sim-intents
```

init 要求父目录已存在、目标目录尚不存在，不递归创建父目录。第一次 record 返回 `RECORDED`；同语义重试返回 `ALREADY_RECORDED`；replay 按持久序号返回完整记录。记录和 replay 都不能自动创建存储。所有操作拒绝路径中的 `..` 和符号链接；受保护 seal 根目录及其祖先不允许成为存储。请求文件必须在存储目录之外，以免与临时/正式状态重叠。

stdout 是单行规范 JSON receipt，stderr 是稳定错误码，不回显输入路径或原始内容。生产二进制没有故障注入环境变量。receipt 无法送达时不得假设操作未提交，应该使用原 request ID 重试。

## 输入契约

完整示例：[simulated-intent.json](../rust/options-workbench/fixtures/simulated-intent.json)。schema：`simulated-trade-intent.rust.v1`。

| 字段 | 约束及含义 |
|---|---|
| `mode` | 只能为 `SIMULATED`；LIVE 和未知模式拒绝 |
| `request_id` | 1–128 字节，ASCII 字母/数字/`.`/`_`/`:`/`-`；调用者提供的稳定 ID，在整份存储中唯一 |
| `simulated_account_id` | 同样的 ID 格式；显式模拟账户标签，不查真实账户、不建立权限 |
| `source` | `kind=MANUAL/ALGORITHM`、`source_id`、`source_version`、`evidence_sha256` 和 `evidence_available_ns`；hash 是输入者声明的证据字节身份，不自动读取或认证该来源 |
| `evidence_cutoff_ns`, `created_ns`, `expires_ns` | 声明的证据截止、请求创建时间和排他的请求截止时间；证据 available ≤ cutoff ≤ created < expires ≤ spec expiry |
| `metadata_known_ns` | 不晚于证据截止；规格 valid_from 不晚于截止，已知 valid_until 必须覆盖请求有效期 |
| `spec`, `leg` | 完整规格与精确匹配的 descriptor/spec hash、side 和原生数量；当前只接收一条线性 European BUY option leg、明确的 linear cash / via-future 结算规则 |
| `limit_price_native` | 正的精确十进制字符串，币种/单位由 spec.quote_currency / price_unit 决定，须满足 tick；数量须满足 lot/step/上限与 multiplier 规则 |

只支持这一明确限价请求，不默认补 market order、数量、币种、账户或决策来源。没有期权估值或终值支持结论。金额数量沿用精确 Dec，不做浮点或隐式舍入。单个请求上限 64 KiB；严格拒绝未知字段、重复键、float、超精度和尾随 JSON。

时间校验只验证输入内的因果关系，**不读取当前时钟，不证明此刻仍有效或来源真实**。历史模拟输入可以记录；后续风控/执行若实现，必须在它们自己的决策时点重新检查有效期、规格和证据。当前未实现这一步。

## 幂等与状态

存储 schema 是 `simulated-intent-store.rust.v1`；receipt schema 是 `simulated-intent-receipt.rust.v1`。每条记录包含连续 sequence、固定状态 `RECORDED_UNASSESSED`、完整请求、规范化意图 SHA256、首次请求原始字节 SHA256。

幂等语义为完整 typed 意图相等：JSON 键顺序、空白和等价十进制表示不影响结果；账户、来源、时间、规格、数量、限价等任一字段改变，都属于不同语义。同一个 request ID 不同语义拒绝，且不重写状态。重复操作保留首次原始字节 hash。ID 不按账户或 source kind 分区，也不提供跨 store 的全局去重。

`sequence` 是存储提交序号，不是市场时间、账户流水或成交序号。record receipt 返回该条记录和总 record_count；replay 返回全部记录，顺序与内容在重启后保持一致。所有 receipt 固定：

```text
mode=SIMULATED
risk_assessment=NOT_ASSESSED
account_evidence=UNVERIFIED
execution_authority=NONE
execution_evidence=UNVERIFIED
source_integrity=NOT_INDEPENDENTLY_VERIFIED
funds_reserved=false
```

## 持久方式与竞争边界

仅有一份权威 `snapshot.json`，包含完整有序记录和覆盖其内容的 SHA256。暂存文件固定为 `pending.json`，永远不作为权威记录；没有追加日志、回放事件解释器、压缩或检查点框架。存储最多 1024 条或 16 MiB，以先到的限制为准，达到上限拒绝新记录，不自动删除、滚动或切换存储。

一次写入的步骤是：

1. 解码和验证请求，然后打开并独占锁住存储目录的文件描述符；所有 record/replay 使用相同 `flock(LOCK_EX | LOCK_NB)`。
2. 严格读取和验证完整快照，包括 schema、规范化字节、checksum、每条意图 hash、合法性、唯一 ID 和连续序号。
3. 命中相同 ID 时比较完整语义，返回原记录或拒绝冲突；新记录先在内存中构造并检查容量。
4. 验证正式快照后，才允许丢弃遗留的普通 pending 文件；新建临时文件、完整写入、file fsync。
5. 同目录原子 rename 替换 snapshot，再 directory fsync；确认目录身份仍一致后才确认成功。

首次 init 还同步新目录的父目录。重试与 replay 也重新同步已验证的正式文件和目录，以确认上次不确定回执的状态。文件和目录权限分别为 0600/0700。复用原 descriptor-relative 安全路径访问；状态文件拒绝符号链接、非普通文件和多重硬链接。

这里是**每个存储同一时刻一个操作**，不是默认“只有一个调用者”。同机多进程可竞争，但锁未拿到返回 `STORE_BUSY`，不会读取旧状态继续写；调用者可以使用相同请求 ID 有限重试。读者也取独占锁，避免复杂的读写协议。锁由内核在文件描述符关闭或进程退出时释放，无 PID 文件和过期锁清理。不同存储之间没有事务、排序或资金净额保证。不得在操作期间搬动/删除/替换存储目录，或使用未遵循此协议的写入工具。

## 崩溃、损坏和错误处理

| 情况 | 实际行为 |
|---|---|
| 输入无效、同 ID 冲突或达到容量上限 | 拒绝，已提交快照不变 |
| 其他进程持锁 | exit 3 / `STORE_BUSY`，无写入 |
| 写入或 rename 前失败 | exit 1 / `STORE_WRITE_FAILED`；旧快照保留，可能有不完整 pending |
| rename 后同步/回执失败 | exit 1 / `STORE_DURABILITY_UNCONFIRMED`；操作可能已提交，必须重放或原 ID 重试，不得换新 ID 假装首次请求 |
| rename 前进程被 SIGKILL | 重启读取旧快照；pending 不升级；原 ID 重试可以新增一次 |
| rename 后进程被 SIGKILL | 本次实测重启读到完整新快照；原 ID 重试返回已记录 |
| 正式快照缺失、截断、checksum 或语义错误 | exit 2 / `CORRUPT_OR_MISSING_STORE`；不自动重置、不用 pending 猜测恢复、不修改损坏字节 |
| init 中断且没有正式快照 | 后续读写和再次 init 都拒绝；保留目录供检查，不将它解释为合法空存储 |

有效正式快照旁有截断 pending 时，可以重放原状态；receipt 的 `uncommitted_pending=true` 表明遗留写入，下一次合法新记录会在锁内清除它。正式快照损坏时，即使 pending 完整也拒绝恢复，因为没有足够证据证明哪个状态应该成为权威。

进程崩溃实验在内核继续运行、文件系统未丢失的条件下完成。file/directory fsync 是实际执行的持久化请求；尚未做断电、主机重启、存储缓存失效或故障磁盘实测，不能据此宣称断电零丢失。单机存储没有远端副本和备份恢复协议，主机/磁盘丢失不在保证范围。checksum 不是签名、备份或外部提交锚点；不能发现攻击者重新计算 hash，也不能发现整体回滚到一份旧但完整合法的快照。

正式快照损坏时，应停止使用该存储并保留全部字节进行核对；本模块没有自动修复命令。不要通过删除目录并重新 init 来掩盖已提交记录是否丢失。

## 保留标准与后续边界

完整快照的每次操作是 O(n) 校验/重写；容量上限使该成本有明确边界。当前没有实测需求证明需要数据库、WAL、索引或缓存，先保留更易审查的实现。若实际容量、延迟或备份恢复要求超出这个范围，再用可复现测量选择成熟存储实现并明确迁移；不先造复杂备选做消融。

目录锁、严格读取、hash、精确数量和 fsync 是必要正确性机制，不属于可削弱的优化项。验证包括实际进程竞争、SIGKILL、真实半截写入和边界故障注入，详见 [验收报告](reports/simulated-intents-20261011/README.md)。

下一最小步骤可以消费这份固定意图记录，实现显式模拟资金快照上的幂等风控预留决策。那需要独立的资金来源、有效时点、余额/预留不变量和失败恢复验收；不能将当前 `RECORDED_UNASSESSED` 直接当作通过。订单状态机、撮合、成交、精确账本和真实账户仍是后续模块，本轮没有空接口或占位实现。
