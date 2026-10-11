# 云端 Rust 首轮迭代：独立期权报价成本诊断

本轮完成一个可独立验收的切片：给定时点、原生数量和显式报价证据，直接诊断 BUY 显示支出 / SELL 显示净收入，不需要指标、View、场景或假设资金。只使用 Rust 构建、原生测试和运行。旧 v1 View 必填、未知字段拒绝、报告身份和封存保护保留。

## 基线、范围与源码证据

- 云工作区 `/workspace/zenQuant` 初始为干净 `work` 分支，提交 `c2aa54af0b39c8a8eb89256b5bee7fd23e0d249e`。保留该分支，没有已有未提交改动需要转移。
- 实际 `git ls-remote` 与 fetch 核实目标 `origin/codex/options-workbench-rust` 为 **`6a6f8211fa8352a66343b015507c8e1bd9d9c941`**，与此前只读记录一致。从它创建 `codex/options-rust-iteration-1`，没有以默认 checkout 作为实施基线。
- 已读根 `AGENTS.md`、`CLAUDE.md`、相关 D2 Rust 计划/测试说明、工作台指南及 live handoff 边界。目标树无嵌套 AGENTS，无 `.agents/skills` 目录。旧计划中的 D2 执行记录作为迁移参考，本轮范围由当前授权确定。
- 原 `contracts::Request` 强制携带 `view_id/views/scenarios/risk_policy/hypothetical_balances`；`inputs::load_analysis` 选择 View 并校验场景绑定。不能通过删 View 或伪造它来得到独立入口。
- 原 `costs::quote_cost` 已经只依赖完整报价、leg、cutoff 和 report currency，能精确计算深度、数量单位、费用及显式 FX。这是本轮复用的数学内核。
- `expressions` 已有现金及四种 paid-long 到期模型，`constraints` 已有同平台 vertical 静态证书；它们的产品/到期/fixing 限制和假设资金检查继续保持，不扩大为真实账户风控或可成交套利。
- `options-workbench` 已有严格本地读取、完整来源 pins 校验、不可覆盖的原子发布。新入口复用这些发布/保护机制，未新增交易所、网络、账户或订单接口。

在开始编码前已报告该证据和选定切片，无需再次等待设计批准。

## 改动与保留标准

| 改动 | 解决的真实问题 / 保留标准 |
|---|---|
| 独立 `options-quote-diagnostic-request.rust.v1`、对应 report schema | 让成本诊断不依赖观点或指标。只接收必要证据；旧 schema 继续拒绝缺 View 和未知字段。两者互不静默转换。 |
| 一个 `quote_diagnostics` 模块 | 完整结构准入、逐项时点选择和已有成本函数调用。无通用插件/DSL/空接口；输入冲突整体失败，局部缺证据保留其他项。 |
| 提取 crate 内部报价/leg 校验 | 防止新旧入口在证据校验上分叉。原 v1 的 70 项基线回归及三个完整报告语义哈希必须保持。 |
| 独立 CLI 参数和报告结构，共用身份计算/原子发布 | 不向旧 v1 报告添加字段；新报告保留全输入、逐项请求/报价/输入哈希和严格无权限声明。 |
| 工具链固定为 Rust 1.90.0，独立 Linux CI 配置 | 缺 Rust 的云环境暴露可重复验证缺口。固定已实测版本、`Cargo.lock` 与原生门禁；CI 不安装 Python，无缓存或并发机制。 |

没有新增依赖；`Cargo.lock` 不变。原金额、成本、终值及约束算法不变。未添加可选产品功能、生产缓存或生产并发，所以没有为消融预建复杂方案。必要证据/时点/安全机制通过失败测试锁定，不参与削弱正确性的消融。

新独立入口只接收内联报价，不支持 `--panel-file`，也不跟 `--request-file` 混用。输入 source/provenance 标签不构成 raw pin 验证；报告始终 `NOT_INDEPENDENTLY_VERIFIED` / `MANUAL_UNATTESTED`，保留 UNKNOWN 和 proxy 原值。现有封存通道仍按原 pin/seal 限制工作，没有放宽允许根目录。

## 测试与实验

环境为 Linux x86_64，`rustc 1.90.0 (1159e78c4 2025-09-14)` / Cargo 1.90.0。首次 `rustc --version && cargo --version && cargo test --workspace --locked` 返回 127（没有工具链），没有执行 Rust 测试。随后通过官方 rustup 仅安装到云工作区 `/workspace/.zenquant-cargo`、`/workspace/.zenquant-rustup`，未调用用户本机 CLI。

以下 cargo 命令工作目录均为仓库 `rust/`。本云环境先设置：

```sh
export PATH=/workspace/.zenquant-cargo/bin:$PATH
export CARGO_HOME=/workspace/.zenquant-cargo
export RUSTUP_HOME=/workspace/.zenquant-rustup
```

| 阶段 | 实际命令 | 结果 / 日志 |
|---|---|---|
| 基线 | `cargo test --workspace --locked` | 70 passed，0 failed/ignored；[日志](baseline-test.log) |
| 基线 | `cargo fmt --all -- --check` | 退出 0；[日志](baseline-fmt.log)（无输出） |
| 基线 | `cargo clippy --workspace --all-targets --locked -- -D warnings` | 退出 0；[日志](baseline-clippy.log) |
| 共享校验提取 | `cargo test --workspace --locked` | 原 70 项全部通过；[日志](shared-admission-test.log) |
| 功能/测试首轮 | `cargo test --workspace --locked` | 85 passed；[日志](implementation-test.log) |
| 功能检查 | `cargo clippy --workspace --all-targets --locked -- -D warnings` | 退出 0；[日志](implementation-clippy.log) |
| 最终 | `cargo fmt --all -- --check` | 退出 0；[日志](final-fmt.log)（无输出） |
| 最终 | `cargo clippy --workspace --all-targets --locked -- -D warnings` | 退出 0；[日志](final-clippy.log) |
| 最终 | `cargo test --workspace --locked` | **85 passed，0 failed/ignored**；[日志](final-test.log) |
| 独立干净目标目录构建 | `CARGO_TARGET_DIR=/workspace/zenquant-validation/clean-target cargo build --workspace --locked --offline` | 退出 0；[日志](clean-build.log)，使用已下载的锁定依赖，不访问网络 |
| 独立二进制运行 | `env PATH=/nonexistent /workspace/zenquant-validation/clean-target/debug/options-workbench --quote-request-file options-workbench/fixtures/quote-costs.json --output-dir /workspace/zenquant-validation/quote-smoke` | 退出 0；[日志](quote-smoke.log)（无输出），[实际报告](report.json)、[身份](identity.json) |
| 差异检查 | `git diff --check` | 退出 0 |

实现阶段执行 `cargo fmt --all` 格式化新代码。三份原 v1 fixture 在修改前均通过实际二进制生成基线报告：

```sh
for fixture in terminal-eth-call terminal-eth-straddle terminal-vertical; do
  target/debug/options-workbench \
    --request-file "options-workbench/fixtures/$fixture.json" \
    --output-dir "/workspace/zenquant-validation/baseline-$fixture"
done
```

最终 85 项由 core 集成测试 56 项、workbench 模块测试 9 项、真实 CLI 集成测试 20 项组成。新增 15 项：core 9、quote CLI 5、旧 v1 完整报告兼容 1。既有发布竞态测试的 `INCOMPLETE_PUBLICATION` 是预期的失败进程输出，测试及总命令仍退出 0。无 doctest 用例，不计入 85 项。

| 对照或失败实验 | 已验证结果 |
|---|---|
| 无 View/指标/预算的 BUY / SELL | 同一数学报价，数量 `.01`，权利金 `.02` / `.019`、费 `.0003`，精确总额 `.0203` / `.0187`；账户和执行始终 UNVERIFIED。 |
| 撤去一条腿的报价 | 该项 `cost=null` / NOT_EVALUABLE，另一项仍可计算。 |
| 较新报价尚未 available | 保留并绑定全输入哈希，选旧可见报价；一旦可见且缺费用，不向旧报价借费用。 |
| 费用、元数据或 FX 迟到；缺费用/index/FX；盘口过期或深度不足 | 不产生总额；可确定的权利金可保留，没有零费用或默认 FX。 |
| 数量 `.015` 不满足 `.01` step | REJECTED，不舍入；两档深度 `.04×2 + .06×3` 加费用得到精确 `.263`。 |
| 修改未知字段、重复键、版本、布尔/float、descriptor、同 spec/time 报价、raw hash | JSON 或公开 typed 边界拒绝；无新输出目录。 |
| 修改未来未选中报价 | 选中成本和报价 hash 不变，全输入/逐项 input hash 改变；原始空白变化只改变 request bytes hash。 |
| UNKNOWN/proxy/inverse 标签 | 原样保留；成本可计算不提升终值模型、账户或执行资格。 |
| 发布到已有目录、受保护 seal 根、符号链接/`..`、只读目录 | 拒绝或返回发布失败；旧输出完整保留。既有原子 rename/fault 测试同样通过。 |
| 旧 v1 缺 View 或被传入新 schema | 拒绝；三个旧 fixture 报告仅将 code hash 置零后与基线完整 JSON 哈希一致。 |

未做性能基准，构建/测试耗时不是性能验收，不宣称达到吞吐或延迟目标。所有金额精确，无新近似估值。

## 提交与文件

本轮最终代码/测试/CI 提交是 **`cab4b05f18332e909f900974d0fff14f9caa4ba5`**；[environment.log](environment.log) 记录最终测试时 HEAD。后续仅提交本文、使用指南和验收日志，不改变已验收源码。完整交付 HEAD 见本地分支及交接消息。

| 提交 | 单一用途 |
|---|---|
| `9728bb5` | 提取共享报价/leg 准入 |
| `afdee2e` | 独立 core 契约与分析 |
| `b6a7119` | CLI 与报告入口 |
| `641dec8` | 原生行为/失败/旧契约回归和数学 fixture |
| `cab4b05` | 固定工具链及 Rust Linux CI |

代码、测试、配置和指南变更文件：

```text
.github/workflows/rust.yml
README.md
docs/OPTIONS_WORKBENCH_RUST_GUIDE.md
rust/rust-toolchain.toml
rust/options-core/src/inputs.rs
rust/options-core/src/lib.rs
rust/options-core/src/quote_diagnostics.rs
rust/options-core/tests/quote_diagnostics.rs
rust/options-workbench/src/main.rs
rust/options-workbench/src/report.rs
rust/options-workbench/tests/cli.rs
rust/options-workbench/tests/quote_cli.rs
rust/options-workbench/fixtures/quote-costs.json
```

验收归档位于本目录：本文、`environment.log`、基线/中间/最终测试和检查日志、`clean-build.log`、`quote-smoke.log`、实际 `report.json` / `identity.json` 及 `SHA256SUMS`。空日志对应成功且静默的命令；结果以上表和实际工具退出码为准。

归档仅去除测试日志末尾多余空行，以通过 `git diff --check`；未规范化的原始日志保留在云工作区 `/workspace/zenquant-validation/`。`SHA256SUMS` 绑定归档字节。

`zenquant/`、旧 Python `scripts/` 与 `tests/`、数据、Cargo.lock、原 contracts/costs/expressions/constraints、原 io/publication 源码均无差异；原 `work` 分支仍在 `c2aa54a`。没有推送、PR、合并、部署、真实账户、密钥或订单操作。

## 明确未实现、未运行与下一步

- 没有真实账户风控、资金预留、OMS、模拟成交、精确账本、重启恢复、提前平仓估值或指标策略。未引入 Go、微服务、通用 DSL、多交易所平台或抽象空壳。
- 新入口不计算场景 EV、终值 payoff、资金可行性或组合净额；这些仍由原受限 v1 模型提供。卖出显示收入不证明能开空或成交；静态价差证书不证明套利。
- 未运行 Python `zenquant-check` / pytest / 历史回测 / 跨语言兼容 suite。本轮不修改旧 Python 路径，Rust 原生验证独立通过，不把过去门禁结果当本轮全仓结果。
- 两份真实固定 seal 数据在云工作区缺失，未运行真实封存 panel 端到端或下载/复制封存数据。Rust 合成 seal/hash/pin 和发布故障测试已运行；不会把这些当真实 panel 验收。
- GitHub workflow 尚未推送或远端执行；Linux 上已运行对应 fmt/clippy/test，以及全新目标目录离线构建和无 PATH 的实际二进制。其他 OS 本轮未复测。
- 本切片无剩余阻塞。下一最小步骤建议：在独立版本化边界提供单一期权到期情景 payoff，显式绑定场景来源/时点/规格，复用现有线性 European 终值核但继续不要求 View、不声称账户资金可行。先限定一条 long leg 及原模型明确支持的到期域，再考虑组合或 intent 闭环。
